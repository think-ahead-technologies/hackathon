/*******************************************************************************
 * File Name        : imu_stream.h
 *
 * Description      : The sensor streaming task: reads BMI270 (I2C) + BMM350
 *                    (I3C) at the configured ODR and streams binary frames
 *                    over whichever transport requested the stream — the
 *                    KitProg3 UART (USB/WebSerial) or the Wi-Fi TCP server
 *                    (SoftAP). Moved here from the CM55 project so it shares
 *                    the core with the Wi-Fi stack.
 *******************************************************************************/

#ifndef IMU_STREAM_H
#define IMU_STREAM_H

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/*******************************************************************************
* Initializes the UART transport and creates the streaming task. Call before
* the scheduler starts. Returns true on success.
*******************************************************************************/
bool imu_stream_create_task(void);

#ifdef __cplusplus
}
#endif

#endif /* IMU_STREAM_H */
