/*******************************************************************************
 * File Name        : mag_app.h
 *
 * Description      : Bosch BMM350 3-axis magnetometer driver for the PSOC Edge
 *                    E84 AI Kit. The BMM350 is an I3C target on the dedicated
 *                    I3C controller (CYBSP_I3C_CONTROLLER, pins P3_0/P3_1) - a
 *                    different peripheral from the BMI270's SCB0 I2C bus. This
 *                    driver brings up I3C and reads the sensor via Infineon's
 *                    mtb_bmm350 helper on top of the Bosch SensorAPI.
 *
 *                    The magnetometer provides the absolute heading reference
 *                    the 6-axis IMU lacks: gravity (accel) corrects roll/pitch
 *                    but cannot observe rotation about the vertical axis, so
 *                    gyro-only yaw drifts. Streaming the mag axes lets the web
 *                    UI fuse a tilt-compensated magnetic heading into yaw and
 *                    kill that drift.
 *
 *******************************************************************************/

#ifndef MAG_APP_H
#define MAG_APP_H

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum
{
    MAG_SOURCE_NONE = 0,
    MAG_SOURCE_BMM350,    /* real sensor over I2C                 */
    MAG_SOURCE_SYNTHETIC  /* fallback demo field (no sensor)      */
} mag_source_t;

/*******************************************************************************
* Brings up the BMM350 on the (already-initialized) shared sensor I2C bus:
* probes the two possible addresses, runs the magnetic reset, sets 100 Hz /
* 4x averaging, enables all three axes and puts it in normal mode. Falls back
* to a synthetic rotating field if the sensor or SensorAPI is absent so the
* stream still carries plausible mag data. Returns the active source.
*
* Independent of the IMU's I2C bus (different peripheral), so ordering versus
* imu_app_init() does not matter. Must run with interrupts enabled.
*******************************************************************************/
mag_source_t mag_app_init(void);

/*******************************************************************************
* Reads one compensated magnetometer sample. out_mag[0..2] receive X/Y/Z in
* units of 1/16 microtesla (i.e. uT = raw / 16.0), matching the wire format the
* web UI decodes. Returns true if fresh data was produced.
*******************************************************************************/
bool mag_app_read(int16_t out_mag[3]);

/*******************************************************************************
* Returns the active data source (valid after mag_app_init()).
*******************************************************************************/
mag_source_t mag_app_source(void);

/*******************************************************************************
* Returns a short human-readable bring-up outcome for the boot log, e.g.
* "detected @0x14", "wrong chip-id 0x..", "init failed (rslt=-2) @0x15",
* "set_odr failed (rslt=-1)", or "SensorAPI not compiled in".
*******************************************************************************/
const char *mag_app_status_str(void);

#ifdef __cplusplus
}
#endif

#endif /* MAG_APP_H */
