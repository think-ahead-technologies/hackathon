/*******************************************************************************
 * File Name        : imu_app.h
 *
 * Description      : BMI270 application driver for the PSOC Edge E84 AI Kit.
 *                    Owns the sensor I2C bus (SCB0, P8_0/P8_1), brings up the
 *                    Bosch BMI270 (including the mandatory config-file upload),
 *                    applies ODR / range / power configuration on demand, and
 *                    reads acceleration, angular-rate and temperature samples.
 *
 *******************************************************************************/

#ifndef IMU_APP_H
#define IMU_APP_H

#include <stdint.h>
#include <stdbool.h>
#include "uart_stream.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum
{
    IMU_SOURCE_NONE = 0,
    IMU_SOURCE_BMI270,    /* real sensor over I2C                 */
    IMU_SOURCE_SYNTHETIC  /* fallback demo waveform (no sensor)   */
} imu_source_t;

/*******************************************************************************
* Initializes the sensor I2C bus and the BMI270. If the sensor cannot be found
* or the Bosch SensorAPI is not present, the driver falls back to a synthetic
* waveform so the WebSerial link is still demonstrable. Returns the active
* data source.
*******************************************************************************/
imu_source_t imu_app_init(void);

/*******************************************************************************
* Applies a configuration received from the web UI (ODR / full-scale / power).
* Safe to call while not streaming. Returns true on success.
*******************************************************************************/
bool imu_app_configure(const uart_cfg_t *cfg);

/*******************************************************************************
* Reads one sample into *out (raw counts, matching the wire format). Returns
* true if fresh data was produced.
*******************************************************************************/
bool imu_app_read(uart_imu_sample_t *out);

/*******************************************************************************
* Returns the active data source (valid after imu_app_init()).
*******************************************************************************/
imu_source_t imu_app_source(void);

/*******************************************************************************
* True when the real BMI270 is the source and its hardware FIFO is available
* for gap-free high-rate capture (use imu_app_read_fifo instead of polling).
*******************************************************************************/
bool imu_app_fifo_active(void);

/*******************************************************************************
* Drains the BMI270 hardware FIFO into out[] (up to max_samples paired
* accel+gyro samples). Each sample gets acc/gyr/temp and a device timestamp
* spaced by the configured ODR; the magnetometer field is left for the caller
* to fill (read once per batch). Returns the number of samples produced (0 if
* none are buffered, negative if FIFO capture is not active).
*******************************************************************************/
int imu_app_read_fifo(uart_imu_sample_t *out, int max_samples);

/*******************************************************************************
* Discards buffered FIFO samples and resets the sample-timestamp origin. Call
* when (re)starting a stream so a fresh session starts from a clean FIFO.
*******************************************************************************/
void imu_app_fifo_flush(void);

/*******************************************************************************
* Diagnostics for the high-rate FIFO path. Reports the ACTUAL ODR the BMI270
* reports back after the last configure (raw BMI2_*_ODR_* enum: 0x08=100Hz,
* 0x09=200Hz, 0x0A=400Hz, 0x0B=800Hz, 0x0C=1600Hz, gyro 0x0D=3200Hz), plus the
* peak FIFO fill (bytes) and the last paired accel/gyro extract counts seen
* since the previous call. Lets us distinguish "sensor producing slowly"
* (avail stays small, acc==gyr) from "drain can't keep up" (avail near the 2 KB
* FIFO) and "mixed ODR" (acc_len >> gyr_len). Reading clears the peak.
* Any out pointer may be NULL.
*******************************************************************************/
void imu_app_fifo_debug(uint16_t *avail_max, uint16_t *acc_len,
                        uint16_t *gyr_len, uint8_t *acc_odr_enum,
                        uint8_t *gyr_odr_enum);

#ifdef __cplusplus
}
#endif

#endif /* IMU_APP_H */
