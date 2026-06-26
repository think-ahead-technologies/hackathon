/*******************************************************************************
 * File Name        : camera_stream.h
 *
 * Description      : USB (UVC) webcam capture on CM55 + JPEG publishing into
 *                    the cross-core shared memory region (see shared/cam_shm.h).
 *                    Ported and trimmed from the PSOC Edge Face ID demo's
 *                    usb_camera_task: same emUSB-Host/UVC capture flow, but
 *                    frames are JPEG-encoded (YUYV in, no display/ML pipeline)
 *                    and handed to the CM33 for Wi-Fi streaming.
 *
 *  Plug a UVC webcam into the kit's USB-C host port (Type-A adapter for
 *  standard cameras). Any camera offering uncompressed 320x240 works; the
 *  frame interval closest to ~10 FPS is selected.
 *******************************************************************************/

#ifndef CAMERA_STREAM_H
#define CAMERA_STREAM_H

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/*******************************************************************************
* Initializes the shared-memory header and creates the camera tasks (USB host
* main/ISR tasks, capture handler, JPEG encoder). Call before the scheduler
* starts. Returns true on success.
*******************************************************************************/
bool camera_stream_create_tasks(void);

#ifdef __cplusplus
}
#endif

#endif /* CAMERA_STREAM_H */
