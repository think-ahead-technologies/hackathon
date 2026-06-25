/*******************************************************************************
 * File Name        : sensor_i2c.h
 *
 * Description      : Tiny shared owner of the on-board sensor I2C bus
 *                    (SCB0 = CYBSP_I2C_CONTROLLER, pins P8_0 SCL / P8_1 SDA).
 *
 *                    Owns the BMI270 IMU's I2C bus. (The BMM350 magnetometer is
 *                    NOT on this bus - it is an I3C device on a separate
 *                    controller; see bmm350/mag_app.c.) Init is idempotent and
 *                    the module exposes blocking register read/write helpers
 *                    built on the PDL manual-master API (no ISR needed).
 *
 *                    All transactions are self-contained (START..STOP) and the
 *                    sensor task issues them sequentially, so a single shared
 *                    context is safe.
 *
 *******************************************************************************/

#ifndef SENSOR_I2C_H
#define SENSOR_I2C_H

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/*******************************************************************************
* Initializes and enables the sensor I2C SCB block. Idempotent: the first call
* brings the bus up, later calls are no-ops that report the cached result.
* Returns true if the bus is usable.
*******************************************************************************/
bool sensor_i2c_init(void);

/*******************************************************************************
* Reads `len` bytes starting at register `reg` from device `addr`.
* Returns 0 on success, -1 on any bus error.
*******************************************************************************/
int sensor_i2c_read(uint8_t addr, uint8_t reg, uint8_t *data, uint32_t len);

/*******************************************************************************
* Writes `len` bytes starting at register `reg` to device `addr`.
* Returns 0 on success, -1 on any bus error.
*******************************************************************************/
int sensor_i2c_write(uint8_t addr, uint8_t reg, const uint8_t *data, uint32_t len);

#ifdef __cplusplus
}
#endif

#endif /* SENSOR_I2C_H */
