/*******************************************************************************
 * File Name        : imu_app.c
 *
 * Description      : See imu_app.h.
 *
 *  ---------------------------------------------------------------------------
 *  BMI270 SensorAPI dependency
 *  ---------------------------------------------------------------------------
 *  The BMI270 *requires* an ~8 KB configuration file to be uploaded over the
 *  bus before it produces data. That blob ships with Bosch's official, open
 *  source BMI270_SensorAPI, so this driver builds on top of it rather than
 *  reproducing the blob.
 *
 *  To enable the real sensor:
 *    1. Ensure deps/bmi270.mtb is present (it is), then run:  make getlibs
 *       (this also pulls every BSP library, so you run it anyway).
 *    2. Rebuild. The build auto-discovers bmi2.c / bmi270*.c and this file
 *       links against them.
 *
 *  If the SensorAPI headers are not found at compile time, this file still
 *  builds and streams a synthetic waveform so the WebSerial link and UI can be
 *  verified end-to-end without the library. A #warning is emitted in that case.
 *  You can also force the demo waveform with  DEFINES+=BMI270_SENSORAPI=0.
 *
 *  Bus: sensor I2C on SCB0 (CYBSP_I2C_CONTROLLER), pins P8_0 (SCL) / P8_1 (SDA),
 *  owned by sensor_i2c.{c,h}. (The BMM350 magnetometer is on a separate I3C
 *  controller, not this bus - see bmm350/mag_app.c.)
 *  The BMI270's I2C address is auto-probed (0x68 with SDO low, 0x69 with high).
 *  If your kit wires the IMU to a different bus, change it in sensor_i2c.c / the
 *  address probe below.
 *******************************************************************************/

#include "imu_app.h"
#include "sensor_i2c.h"

#include "cybsp.h"
#include "cy_scb_i2c.h"
#include "cy_syslib.h"

#include "FreeRTOS.h"
#include "task.h"

#include <math.h>
#include <string.h>

/*******************************************************************************
 * SensorAPI detection
 ******************************************************************************/
#ifndef BMI270_SENSORAPI
#define BMI270_SENSORAPI 1
#endif

#if BMI270_SENSORAPI
  #if defined(__has_include)
    #if __has_include("bmi270.h")
      #include "bmi2.h"
      #include "bmi270.h"
      #define BMI270_LIB_PRESENT 1
    #endif
  #else
    #include "bmi2.h"
    #include "bmi270.h"
    #define BMI270_LIB_PRESENT 1
  #endif
#endif

#if (BMI270_SENSORAPI) && !defined(BMI270_LIB_PRESENT)
  #warning "BMI270 SensorAPI not found - building synthetic IMU demo. Run 'make getlibs' (deps/bmi270.mtb) to stream the real sensor."
#endif

/*******************************************************************************
 * Constants
 ******************************************************************************/
#define BMI270_ADDR_PRIMARY (0x68u)
#define BMI270_ADDR_SECOND  (0x69u)
#define IMU_EXPECTED_CHIP_ID (0x24u)

#define REG_CHIP_ID         (0x00u)
#define REG_TEMPERATURE_LSB (0x22u)

/*******************************************************************************
 * Local state
 ******************************************************************************/
static uint8_t      s_addr   = BMI270_ADDR_PRIMARY;
static imu_source_t s_source = IMU_SOURCE_NONE;
static uart_cfg_t   s_cfg    = { 100u, 4u, 200u, 2000u, "normal" };

#ifdef BMI270_LIB_PRESENT
static struct bmi2_dev s_bmi;
static bool            s_fifo_ready = false;
static uint32_t        s_sample_idx = 0u;   /* running index for derived t_us */

/* Diagnostics (see imu_app_fifo_debug): actual ODR the sensor reports after a
 * configure, and FIFO fill / pairing seen during draining. */
static uint8_t  s_dbg_acc_odr  = 0u;
static uint8_t  s_dbg_gyr_odr  = 0u;
static uint16_t s_dbg_avail_max = 0u;
static uint16_t s_dbg_acc_len  = 0u;
static uint16_t s_dbg_gyr_len  = 0u;

/* FIFO drain scratch. Headerless accel+gyro frames are 12 bytes each; keep the
 * raw buffer small so each blocking I2C read stays short (we drain often). */
#define IMU_FIFO_MAX_SAMPLES   (32)
#define IMU_FIFO_BUF_BYTES     (IMU_FIFO_MAX_SAMPLES * 12 + 16)
static uint8_t                    s_fifo_buf[IMU_FIFO_BUF_BYTES];
static struct bmi2_sens_axes_data s_fifo_acc[IMU_FIFO_MAX_SAMPLES];
static struct bmi2_sens_axes_data s_fifo_gyr[IMU_FIFO_MAX_SAMPLES];
#endif

/*******************************************************************************
 * Low-level I2C helpers — thin wrappers over the shared sensor bus so the
 * existing call sites and SensorAPI callbacks below stay unchanged.
 ******************************************************************************/
static int i2c_read(uint8_t addr, uint8_t reg, uint8_t *data, uint32_t len)
{
    return sensor_i2c_read(addr, reg, data, len);
}

#ifdef BMI270_LIB_PRESENT
static int i2c_write(uint8_t addr, uint8_t reg, const uint8_t *data, uint32_t len)
{
    return sensor_i2c_write(addr, reg, data, len);
}

static int16_t read_temperature_raw(void)
{
    uint8_t t[2] = { 0u, 0u };
    if (i2c_read(s_addr, REG_TEMPERATURE_LSB, t, 2u) != 0)
    {
        return 0x8000; /* BMI270 "invalid temperature" sentinel */
    }
    return (int16_t)((uint16_t)t[0] | ((uint16_t)t[1] << 8));
}
#endif /* BMI270_LIB_PRESENT */

/*******************************************************************************
 * SensorAPI bus + delay callbacks
 ******************************************************************************/
#ifdef BMI270_LIB_PRESENT
static BMI2_INTF_RETURN_TYPE bmi2_i2c_read(uint8_t reg, uint8_t *data,
                                           uint32_t len, void *intf_ptr)
{
    uint8_t addr = *(uint8_t *)intf_ptr;
    return (i2c_read(addr, reg, data, len) == 0) ? BMI2_INTF_RET_SUCCESS
                                                 : (BMI2_INTF_RETURN_TYPE)-1;
}

static BMI2_INTF_RETURN_TYPE bmi2_i2c_write(uint8_t reg, const uint8_t *data,
                                            uint32_t len, void *intf_ptr)
{
    uint8_t addr = *(uint8_t *)intf_ptr;
    return (i2c_write(addr, reg, data, len) == 0) ? BMI2_INTF_RET_SUCCESS
                                                  : (BMI2_INTF_RETURN_TYPE)-1;
}

static void bmi2_delay_us(uint32_t period, void *intf_ptr)
{
    (void)intf_ptr;
    while (period > 1000u)
    {
        Cy_SysLib_Delay(1u); /* 1 ms */
        period -= 1000u;
    }
    if (period > 0u)
    {
        Cy_SysLib_DelayUs((uint16_t)period);
    }
}

/* ---- enum mapping from engineering units to SensorAPI codes ---- */
static uint8_t map_acc_odr(uint16_t hz)
{
    switch (hz)
    {
        case 25:   return BMI2_ACC_ODR_25HZ;
        case 50:   return BMI2_ACC_ODR_50HZ;
        case 100:  return BMI2_ACC_ODR_100HZ;
        case 200:  return BMI2_ACC_ODR_200HZ;
        case 400:  return BMI2_ACC_ODR_400HZ;
        case 800:  return BMI2_ACC_ODR_800HZ;
        case 1600: return BMI2_ACC_ODR_1600HZ;
        default:   return BMI2_ACC_ODR_100HZ;
    }
}
static uint8_t map_acc_range(uint8_t g)
{
    switch (g)
    {
        case 2:  return BMI2_ACC_RANGE_2G;
        case 4:  return BMI2_ACC_RANGE_4G;
        case 8:  return BMI2_ACC_RANGE_8G;
        case 16: return BMI2_ACC_RANGE_16G;
        default: return BMI2_ACC_RANGE_4G;
    }
}
static uint8_t map_gyr_odr(uint16_t hz)
{
    switch (hz)
    {
        case 25:   return BMI2_GYR_ODR_25HZ;
        case 50:   return BMI2_GYR_ODR_50HZ;
        case 100:  return BMI2_GYR_ODR_100HZ;
        case 200:  return BMI2_GYR_ODR_200HZ;
        case 400:  return BMI2_GYR_ODR_400HZ;
        case 800:  return BMI2_GYR_ODR_800HZ;
        case 1600: return BMI2_GYR_ODR_1600HZ;
        case 3200: return BMI2_GYR_ODR_3200HZ;
        default:   return BMI2_GYR_ODR_200HZ;
    }
}
static uint8_t map_gyr_range(uint16_t dps)
{
    switch (dps)
    {
        case 125:  return BMI2_GYR_RANGE_125;
        case 250:  return BMI2_GYR_RANGE_250;
        case 500:  return BMI2_GYR_RANGE_500;
        case 1000: return BMI2_GYR_RANGE_1000;
        case 2000: return BMI2_GYR_RANGE_2000;
        default:   return BMI2_GYR_RANGE_2000;
    }
}
static uint8_t map_filter_perf(const char *power)
{
    /* "lowpower" optimizes for power; everything else for performance. */
    if (strncmp(power, "lowpower", 8) == 0)
    {
        return BMI2_POWER_OPT_MODE;
    }
    return BMI2_PERF_OPT_MODE;
}

static bool bmi270_apply_config(const uart_cfg_t *cfg)
{
    struct bmi2_sens_config config[2];
    config[0].type = BMI2_ACCEL;
    config[1].type = BMI2_GYRO;

    if (bmi2_get_sensor_config(config, 2, &s_bmi) != BMI2_OK)
    {
        return false;
    }

    uint8_t perf = map_filter_perf(cfg->power);

    config[0].cfg.acc.odr         = map_acc_odr(cfg->acc_odr);
    config[0].cfg.acc.range       = map_acc_range(cfg->acc_range);
    config[0].cfg.acc.bwp         = BMI2_ACC_NORMAL_AVG4;
    config[0].cfg.acc.filter_perf = perf;

    config[1].cfg.gyr.odr         = map_gyr_odr(cfg->gyr_odr);
    config[1].cfg.gyr.range       = map_gyr_range(cfg->gyr_range);
    config[1].cfg.gyr.bwp         = BMI2_GYR_NORMAL_MODE;
    config[1].cfg.gyr.ois_range   = BMI2_GYR_OIS_2000;
    config[1].cfg.gyr.filter_perf = perf;
    config[1].cfg.gyr.noise_perf  = perf;

    if (bmi2_set_sensor_config(config, 2, &s_bmi) != BMI2_OK)
    {
        return false;
    }

    /* Read the configuration BACK from the sensor so the diagnostics report the
     * ODR the BMI270 actually accepted (not just what we asked for). If the part
     * rejected/clamped a rate — e.g. the gyro silently staying at its previous
     * ODR — this is where it shows up. */
    {
        struct bmi2_sens_config rb[2];
        rb[0].type = BMI2_ACCEL;
        rb[1].type = BMI2_GYRO;
        if (bmi2_get_sensor_config(rb, 2, &s_bmi) == BMI2_OK)
        {
            s_dbg_acc_odr = rb[0].cfg.acc.odr;
            s_dbg_gyr_odr = rb[1].cfg.gyr.odr;
        }
    }

    /* (Re)enable the hardware FIFO for gap-free high-rate capture: headerless
     * accel+gyro frames, advance-power-save off (required for continuous FIFO).
     * Flush so the new ODR's stream starts clean. */
    s_fifo_ready = false;
    s_sample_idx = 0u;
    if (bmi2_set_adv_power_save(BMI2_DISABLE, &s_bmi) != BMI2_OK)
    {
        return false;
    }
    if (bmi2_set_fifo_config(BMI2_FIFO_ACC_EN | BMI2_FIFO_GYR_EN, BMI2_ENABLE,
                             &s_bmi) != BMI2_OK)
    {
        return false;
    }
    (void)bmi2_set_command_register(BMI2_FIFO_FLUSH_CMD, &s_bmi);
    s_fifo_ready = true;
    return true;
}

static bool bmi270_bringup(void)
{
    s_bmi.intf           = BMI2_I2C_INTF;
    s_bmi.read           = bmi2_i2c_read;
    s_bmi.write          = bmi2_i2c_write;
    s_bmi.delay_us       = bmi2_delay_us;
    s_bmi.intf_ptr       = &s_addr;
    s_bmi.read_write_len = 256u;
    s_bmi.config_file_ptr = NULL; /* use SensorAPI's built-in config blob */

    if (bmi270_init(&s_bmi) != BMI2_OK)
    {
        return false;
    }

    uint8_t sens_list[2] = { BMI2_ACCEL, BMI2_GYRO };
    if (bmi2_sensor_enable(sens_list, 2, &s_bmi) != BMI2_OK)
    {
        return false;
    }

    return bmi270_apply_config(&s_cfg);
}
#endif /* BMI270_LIB_PRESENT */

/*******************************************************************************
 * Synthetic fallback waveform (no sensor / no SensorAPI)
 ******************************************************************************/
static void synthetic_sample(uart_imu_sample_t *out)
{
    static float phase = 0.0f;
    phase += 0.06f;

    /* counts-per-unit at the active full scale */
    float acc_lsb_per_g   = 32768.0f / (float)s_cfg.acc_range;
    float gyr_lsb_per_dps = 32768.0f / (float)s_cfg.gyr_range;

    /* gentle tumble: ~1 g settling on Z with a slow wobble on X/Y */
    out->acc[0] = (int16_t)(0.25f * sinf(phase)        * acc_lsb_per_g);
    out->acc[1] = (int16_t)(0.25f * sinf(phase * 0.7f) * acc_lsb_per_g);
    out->acc[2] = (int16_t)(1.00f * acc_lsb_per_g
                            + 0.05f * sinf(phase * 1.3f) * acc_lsb_per_g);

    out->gyr[0] = (int16_t)(40.0f * sinf(phase * 1.1f) * gyr_lsb_per_dps);
    out->gyr[1] = (int16_t)(30.0f * sinf(phase * 0.9f) * gyr_lsb_per_dps);
    out->gyr[2] = (int16_t)(60.0f * cosf(phase * 0.5f) * gyr_lsb_per_dps);

    out->temp = (int16_t)((27 - 23) * 512); /* ~27 C */
}

/*******************************************************************************
 * Public API
 ******************************************************************************/
imu_source_t imu_app_init(void)
{
    /* Bring up the shared sensor I2C block (BSP already routed pins + clock).
     * The magnetometer driver shares this bus, so init is idempotent. */
    if (sensor_i2c_init())
    {
        /* Probe both possible addresses for the BMI270 chip-id. */
        uint8_t id = 0u;
        if ((i2c_read(BMI270_ADDR_PRIMARY, REG_CHIP_ID, &id, 1u) == 0)
                && (id == IMU_EXPECTED_CHIP_ID))
        {
            s_addr = BMI270_ADDR_PRIMARY;
        }
        else if ((i2c_read(BMI270_ADDR_SECOND, REG_CHIP_ID, &id, 1u) == 0)
                && (id == IMU_EXPECTED_CHIP_ID))
        {
            s_addr = BMI270_ADDR_SECOND;
        }
        else
        {
            id = 0u; /* not found */
        }

#ifdef BMI270_LIB_PRESENT
        if (id == IMU_EXPECTED_CHIP_ID && bmi270_bringup())
        {
            s_source = IMU_SOURCE_BMI270;
            return s_source;
        }
#endif
    }

    s_source = IMU_SOURCE_SYNTHETIC;
    return s_source;
}

bool imu_app_configure(const uart_cfg_t *cfg)
{
    if (cfg == NULL)
    {
        return false;
    }
    s_cfg = *cfg;

#ifdef BMI270_LIB_PRESENT
    if (s_source == IMU_SOURCE_BMI270)
    {
        return bmi270_apply_config(&s_cfg);
    }
#endif
    return true; /* synthetic source honors range/odr in software */
}

bool imu_app_read(uart_imu_sample_t *out)
{
    if (out == NULL)
    {
        return false;
    }

    out->t_us = (int32_t)(xTaskGetTickCount() * (1000000UL / configTICK_RATE_HZ));

#ifdef BMI270_LIB_PRESENT
    if (s_source == IMU_SOURCE_BMI270)
    {
        struct bmi2_sens_data data;
        memset(&data, 0, sizeof(data));
        if (bmi2_get_sensor_data(&data, &s_bmi) != BMI2_OK)
        {
            return false;
        }
        out->acc[0] = data.acc.x;
        out->acc[1] = data.acc.y;
        out->acc[2] = data.acc.z;
        out->gyr[0] = data.gyr.x;
        out->gyr[1] = data.gyr.y;
        out->gyr[2] = data.gyr.z;
        out->temp   = read_temperature_raw();
        return true;
    }
#endif

    synthetic_sample(out);
    return true;
}

imu_source_t imu_app_source(void)
{
    return s_source;
}

bool imu_app_fifo_active(void)
{
#ifdef BMI270_LIB_PRESENT
    return (s_source == IMU_SOURCE_BMI270) && s_fifo_ready;
#else
    return false;
#endif
}

int imu_app_read_fifo(uart_imu_sample_t *out, int max_samples)
{
#ifdef BMI270_LIB_PRESENT
    if ((out == NULL) || (max_samples <= 0) || !imu_app_fifo_active())
    {
        return -1;
    }

    uint16_t avail = 0u;
    if (bmi2_get_fifo_length(&avail, &s_bmi) != BMI2_OK)
    {
        return 0;
    }
    if (avail > s_dbg_avail_max)        /* peak fill since last debug read */
    {
        s_dbg_avail_max = avail;
    }
    if (avail == 0u)
    {
        return 0;
    }

    /* Read whole headerless accel+gyro frames only (12 bytes each), capped to
     * what extract can hold; any remainder stays in the hardware FIFO and is
     * picked up on the next drain. Reading a partial frame would consume bytes
     * from the HW FIFO that we then can't extract, desyncing the stream. */
    #define IMU_FIFO_FRAME_BYTES   (12u)
    uint16_t max_bytes = (uint16_t)(IMU_FIFO_MAX_SAMPLES * IMU_FIFO_FRAME_BYTES);
    uint16_t to_read   = (avail < max_bytes) ? avail : max_bytes;
    to_read = (uint16_t)((to_read / IMU_FIFO_FRAME_BYTES) * IMU_FIFO_FRAME_BYTES);
    if (to_read == 0u)
    {
        return 0;
    }

    struct bmi2_fifo_frame fifo;
    memset(&fifo, 0, sizeof(fifo));
    fifo.data         = s_fifo_buf;
    fifo.length       = to_read;
    fifo.header_enable = 0u;   /* headerless */
    fifo.data_enable  = (uint16_t)(BMI2_FIFO_ACC_EN | BMI2_FIFO_GYR_EN);

    if (bmi2_read_fifo_data(&fifo, &s_bmi) != BMI2_OK)
    {
        return 0;
    }

    uint16_t cap     = (uint16_t)((max_samples < IMU_FIFO_MAX_SAMPLES)
                                  ? max_samples : IMU_FIFO_MAX_SAMPLES);
    uint16_t acc_len = cap;
    uint16_t gyr_len = cap;
    (void)bmi2_extract_accel(s_fifo_acc, &acc_len, &fifo, &s_bmi);
    (void)bmi2_extract_gyro(s_fifo_gyr, &gyr_len, &fifo, &s_bmi);

    s_dbg_acc_len = acc_len;            /* diagnostics: acc>>gyr => mixed ODR */
    s_dbg_gyr_len = gyr_len;

    uint16_t n = (acc_len < gyr_len) ? acc_len : gyr_len;
    if (n == 0u)
    {
        return 0;
    }

    int16_t  temp = read_temperature_raw();
    uint16_t odr  = (s_cfg.acc_odr != 0u) ? s_cfg.acc_odr : 1600u;
    uint32_t dt   = 1000000u / odr;   /* microseconds between samples */

    for (uint16_t i = 0u; i < n; i++)
    {
        out[i].t_us  = (int32_t)(s_sample_idx * dt);
        s_sample_idx++;
        out[i].acc[0] = s_fifo_acc[i].x;
        out[i].acc[1] = s_fifo_acc[i].y;
        out[i].acc[2] = s_fifo_acc[i].z;
        out[i].gyr[0] = s_fifo_gyr[i].x;
        out[i].gyr[1] = s_fifo_gyr[i].y;
        out[i].gyr[2] = s_fifo_gyr[i].z;
        out[i].temp   = temp;
        out[i].mag[0] = out[i].mag[1] = out[i].mag[2] = 0;  /* caller fills mag */
    }
    return (int)n;
#else
    (void)out;
    (void)max_samples;
    return -1;
#endif
}

void imu_app_fifo_flush(void)
{
#ifdef BMI270_LIB_PRESENT
    if (imu_app_fifo_active())
    {
        (void)bmi2_set_command_register(BMI2_FIFO_FLUSH_CMD, &s_bmi);
        s_sample_idx = 0u;
    }
#endif
}

void imu_app_fifo_debug(uint16_t *avail_max, uint16_t *acc_len,
                        uint16_t *gyr_len, uint8_t *acc_odr_enum,
                        uint8_t *gyr_odr_enum)
{
#ifdef BMI270_LIB_PRESENT
    if (avail_max    != NULL) { *avail_max    = s_dbg_avail_max; }
    if (acc_len      != NULL) { *acc_len      = s_dbg_acc_len;   }
    if (gyr_len      != NULL) { *gyr_len      = s_dbg_gyr_len;   }
    if (acc_odr_enum != NULL) { *acc_odr_enum = s_dbg_acc_odr;   }
    if (gyr_odr_enum != NULL) { *gyr_odr_enum = s_dbg_gyr_odr;   }
    s_dbg_avail_max = 0u;               /* clear the peak for the next window */
#else
    if (avail_max    != NULL) { *avail_max    = 0u; }
    if (acc_len      != NULL) { *acc_len      = 0u; }
    if (gyr_len      != NULL) { *gyr_len      = 0u; }
    if (acc_odr_enum != NULL) { *acc_odr_enum = 0u; }
    if (gyr_odr_enum != NULL) { *gyr_odr_enum = 0u; }
#endif
}
