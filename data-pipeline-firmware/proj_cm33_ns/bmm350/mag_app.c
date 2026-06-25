/*******************************************************************************
 * File Name        : mag_app.c
 *
 * Description      : See mag_app.h.
 *
 *  ---------------------------------------------------------------------------
 *  The BMM350 is on the I3C bus, NOT the IMU's I2C bus
 *  ---------------------------------------------------------------------------
 *  On the PSOC Edge E84 AI Kit the BMM350 is an I3C target on the dedicated I3C
 *  controller (CYBSP_I3C_CONTROLLER = I3C_CORE, pins P3_0/P3_1) - a different
 *  peripheral from the SCB0 I2C bus the BMI270 hangs off. So this driver brings
 *  up the I3C controller (init + interrupt + enable), performs I3C dynamic
 *  address assignment (RSTDAA -> ENTDAA) and configures the sensor via Bosch's
 *  BMM350 SensorAPI, all wrapped by Infineon's mtb_bmm350 helper (vendored here
 *  as mtb_bmm350.{c,h}).
 *
 *  NOTE on interrupts: the BMM350 runs on the CM55 core, where the I3C
 *  peripheral interrupt is not wired into the NVIC. Rather than fight the
 *  cross-core interrupt routing, the vendored mtb_bmm350 driver PUMPS the I3C
 *  state machine by hand (see _bmm350_wait_xfer there), so no ISR is hooked
 *  here. Every transfer wait is bounded, so a missing/!responding sensor falls
 *  back to synthetic instead of stalling the stream task.
 *
 *  If bring-up fails at any step, this driver streams a STATIC synthetic field
 *  (constant heading) so the link/UI still work and a missing sensor reads as
 *  idle rather than spinning. mag_app_status_str() reports what happened.
 *******************************************************************************/

#include "mag_app.h"
#include "mtb_bmm350.h"

#include "cybsp.h"
#include "cy_pdl.h"

#include <math.h>
#include <stdio.h>

/*******************************************************************************
 * Constants
 ******************************************************************************/
/* On-wire fixed-point: 1 count = 1/256 microtesla (matches the host/web decode).
 * Full scale becomes +/-128 uT (int16) -> comfortably covers Earth's field
 * (~25..65 uT) with margin, while the 0.0039 uT LSB preserves the BMM350's true
 * resolution (was 1/16 uT, which threw away ~9 bits below the sensor's noise
 * floor). Strong nearby magnets above ~128 uT will clip - acceptable for a
 * heading/orientation use case. */
#define MAG_LSB_PER_UT          (256.0f)

/*******************************************************************************
 * Local state
 ******************************************************************************/
static mag_source_t           s_source = MAG_SOURCE_NONE;
/* Sized to hold the enriched bring-up diagnostic, e.g.
 * "I3C bring-up failed rslt=0xFFFFFFFD id=0x00 dyn=0x00". */
static char                   s_status[80] = "not initialized";

/* DEBUG: raw on-the-wire bytes captured during bring-up (see mtb_bmm350.c). */
extern const uint8_t* mtb_bmm350_debug_probe(void);

static cy_stc_i3c_context_t   s_i3c_ctx;
static mtb_bmm350_t           s_mag;
/* Descriptor for the on-bus target. ENTDAA fills in the dynamic address; the
 * BMM350 on this kit answers at the ADSEL-high static address. */
static cy_stc_i3c_device_t    s_i3c_dev = { .staticAddress = MTB_BMM350_ADDRESS_SEC };

static bool i3c_bus_bringup(void)
{
    if (Cy_I3C_Init(CYBSP_I3C_CONTROLLER_HW, &CYBSP_I3C_CONTROLLER_config,
                    &s_i3c_ctx) != CY_I3C_SUCCESS)
    {
        strncpy(s_status, "I3C init failed", sizeof(s_status) - 1);
        return false;
    }

    /* No NVIC hookup: transfers are pumped in software (see mtb_bmm350.c). */
    Cy_I3C_Enable(CYBSP_I3C_CONTROLLER_HW, &s_i3c_ctx);
    return true;
}

/*******************************************************************************
 * Synthetic fallback field (no sensor / bring-up failure)
 *
 * A STATIC ~50 uT field. It must not move: a rotating demo field would drive
 * the fused yaw and make the cube spin on its own. Static field -> static
 * heading -> the cube holds still, so a missing sensor looks idle.
 ******************************************************************************/
static void synthetic_mag(int16_t out[3])
{
    out[0] = (int16_t)( 50.0f * MAG_LSB_PER_UT);
    out[1] = (int16_t)(  0.0f * MAG_LSB_PER_UT);
    out[2] = (int16_t)(-30.0f * MAG_LSB_PER_UT);
}

/*******************************************************************************
 * Public API
 ******************************************************************************/
mag_source_t mag_app_init(void)
{
    if (i3c_bus_bringup())
    {
        cy_rslt_t rslt = mtb_bmm350_init_i3c(&s_mag, CYBSP_I3C_CONTROLLER_HW,
                                             &s_i3c_ctx, &s_i3c_dev);
        if (rslt == CY_RSLT_SUCCESS)
        {
            /* mtb_bmm350_init_i3c leaves the sensor at 25 Hz / AVG8; bump to
             * 400 Hz (the BMM350 max). AVG2 is used because heavier averaging
             * will not complete within the 2.5 ms measurement window at 400 Hz.
             * The forwarder reads the mag per IMU sample, so the host captures
             * up to min(IMU ODR, 400 Hz) distinct field values. */
            (void)mtb_bmm350_set_odr_performance(BMM350_DATA_RATE_400HZ,
                                                 BMM350_AVERAGING_2, &s_mag);
            snprintf(s_status, sizeof(s_status), "detected @0x%02X (I3C dyn 0x%02X)",
                     MTB_BMM350_ADDRESS_SEC, s_i3c_dev.dynamicAddress);
            s_source = MAG_SOURCE_BMM350;
            return s_source;
        }
        /* Diagnostic: surface the raw chip-id byte the SensorAPI read and the
         * dynamic address ENTDAA assigned. This disambiguates the failure mode:
         *   id=0x00            -> response never drained (I3C wait/pump race)
         *   id=plausible-shift -> read offset by the 2 BMM350 dummy bytes
         *   dyn=0x00           -> ENTDAA never enumerated the part
         * (chip-id 0x33 == BMM350; rslt=0xFFFFFFFD == -3 == DEV_NOT_FOUND.) */
        const uint8_t *pr = mtb_bmm350_debug_probe();
        snprintf(s_status, sizeof(s_status),
                 "BMM fail rslt=0x%08lX dyn=0x%02X raw=%02X%02X%02X%02X%02X%02X",
                 (unsigned long)rslt,
                 (unsigned)s_i3c_dev.dynamicAddress,
                 pr[0], pr[1], pr[2], pr[3], pr[4], pr[5]);
    }

    s_source = MAG_SOURCE_SYNTHETIC;
    return s_source;
}

static int16_t clamp_i16(float v)
{
    if (v >  32767.0f) return  32767;
    if (v < -32768.0f) return -32768;
    return (int16_t)lroundf(v);
}

bool mag_app_read(int16_t out_mag[3])
{
    if (out_mag == NULL)
    {
        return false;
    }

    if (s_source == MAG_SOURCE_BMM350)
    {
        mtb_bmm350_data_t data;
        if (mtb_bmm350_read(&s_mag, &data) != CY_RSLT_SUCCESS)
        {
            return false;
        }
        out_mag[0] = clamp_i16(data.sensor_data.x * MAG_LSB_PER_UT);
        out_mag[1] = clamp_i16(data.sensor_data.y * MAG_LSB_PER_UT);
        out_mag[2] = clamp_i16(data.sensor_data.z * MAG_LSB_PER_UT);
        return true;
    }

    synthetic_mag(out_mag);
    return true;
}

mag_source_t mag_app_source(void)
{
    return s_source;
}

const char *mag_app_status_str(void)
{
    return s_status;
}
