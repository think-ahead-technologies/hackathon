// ABOUTME: BMI270 IMU driver (CM55) — I2C bring-up + accel read, scaled to m/s^2 for the model.
// ABOUTME: Mirrors the PSOC Edge deepcraft-deploy-motion example's CM55 IMU init.

#include "imu.h"

#include "cybsp.h"
#include "mtb_hal.h"
#include "mtb_bmi270.h"

// ±2 G range -> 16384 LSB/g (BMI2 default). Training recordings are in m/s^2 (gravity ~9.8),
// so convert raw int16 -> g -> m/s^2 to match the spectrogram the model was trained on.
#define IMU_LSB_PER_G   16384.0f
#define IMU_G_TO_MS2    9.80665f
#define IMU_LSB_PER_DPS 16.4f

static cy_stc_scb_i2c_context_t g_i2c_ctx;
static mtb_hal_i2c_t            g_i2c;
static mtb_bmi270_t            g_bmi270;

bool imu_init(void) {
    // The SCB I2C peripheral + its clock are configured by the BSP (cybsp_init); bring the
    // block up on this core and wrap it in the HAL the sensor driver expects.
    if (Cy_SCB_I2C_Init(CYBSP_I2C_CONTROLLER_HW, &CYBSP_I2C_CONTROLLER_config, &g_i2c_ctx)
            != CY_SCB_I2C_SUCCESS) {
        return false;
    }
    Cy_SCB_I2C_Enable(CYBSP_I2C_CONTROLLER_HW);

    if (mtb_hal_i2c_setup(&g_i2c, &CYBSP_I2C_CONTROLLER_hal_config, &g_i2c_ctx, NULL)
            != CY_RSLT_SUCCESS) {
        return false;
    }
    if (mtb_bmi270_init_i2c(&g_bmi270, &g_i2c, MTB_BMI270_ADDRESS_DEFAULT) != CY_RSLT_SUCCESS) {
        return false;
    }
    // Default config enables the accelerometer; we sample it by polling at the model's rate.
    if (mtb_bmi270_config_default(&g_bmi270) != CY_RSLT_SUCCESS) {
        return false;
    }
    return true;
}

bool imu_read_motion(float accel_ms2[3], float gyro_dps[3]) {
    mtb_bmi270_data_t data;
    if (mtb_bmi270_read(&g_bmi270, &data) != CY_RSLT_SUCCESS) {
        return false;
    }
    const float acc_k = IMU_G_TO_MS2 / IMU_LSB_PER_G;
    accel_ms2[0] = (float)data.sensor_data.acc.x * acc_k;
    accel_ms2[1] = (float)data.sensor_data.acc.y * acc_k;
    accel_ms2[2] = (float)data.sensor_data.acc.z * acc_k;
    gyro_dps[0] = (float)data.sensor_data.gyr.x / IMU_LSB_PER_DPS;
    gyro_dps[1] = (float)data.sensor_data.gyr.y / IMU_LSB_PER_DPS;
    gyro_dps[2] = (float)data.sensor_data.gyr.z / IMU_LSB_PER_DPS;
    return true;
}

bool imu_read_accel_ms2(float out[3]) {
    float gyro_dps[3];
    return imu_read_motion(out, gyro_dps);
}
