/*******************************************************************************
 * File Name        : camera_fwd.h
 *
 * Description      : See camera_fwd.c — forwards CM55-encoded camera JPEG
 *                    frames from shared SOCMEM to the Wi-Fi TCP stream.
 *******************************************************************************/

#ifndef CAMERA_FWD_H
#define CAMERA_FWD_H

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Creates the forwarder task. Call before the scheduler starts. */
bool camera_fwd_create_task(void);

#ifdef __cplusplus
}
#endif

#endif /* CAMERA_FWD_H */
