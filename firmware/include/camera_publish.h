// ABOUTME: camera-data publisher — poll the camera HAL and PUB JPEG frames to edge.camera over NATS.
// ABOUTME: Scatter-sends header + JPEG straight from HAL memory (no copy); on-target (uses the HAL).

#ifndef CAMERA_PUBLISH_H
#define CAMERA_PUBLISH_H

#include <stdint.h>

#include "camera_proto.h"

// Publish one already-captured JPEG frame as a NATS PUB on `subject`. Scatter-sends, in order:
// the PUB header line, the CAM_PROTO_HDR_LEN binary frame header, the JPEG bitstream (straight from
// `jpeg` — no copy into a contiguous frame buffer), then the trailing CRLF. No application-layer
// CRC: the NATS connection is TLS, which authenticates every byte. Returns >=0 on success, <0 on a
// transport error (the caller should reconnect).
int camera_publish_frame(int sock, const char *subject, const hal_cam_meta_t *meta,
                         const uint8_t *jpeg, uint32_t jpeg_len);

// Poll the camera HAL once; if a new frame is ready, publish it and release it. Returns 1 if a frame
// was published, 0 if none was ready, <0 on a transport error.
int camera_publish_step(int sock, const char *subject);

#endif  // CAMERA_PUBLISH_H
