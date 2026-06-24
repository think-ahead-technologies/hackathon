// ABOUTME: BMI270 IMU bring-up on CM55 — reads 3-axis accel for the wear-detection feature path.
// ABOUTME: Accel is returned in m/s^2 to match the training recordings (mean |a| ~= 9.8).

#ifndef IMU_H
#define IMU_H

#include <stdbool.h>

// Bring up the onboard BMI270 over I2C (CYBSP_I2C_CONTROLLER). Returns false on failure.
bool imu_init(void);

// Read one accelerometer sample into out[3] = {x,y,z} in m/s^2. Returns false on failure.
bool imu_read_accel_ms2(float out[3]);

#endif  // IMU_H
