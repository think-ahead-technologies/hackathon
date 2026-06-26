// ABOUTME: camera-data wire header — pack per-frame metadata into the binary NATS payload prefix.
// ABOUTME: Pure (no hardware); the complete JPEG bitstream follows the header on the wire.

#ifndef CAMERA_PROTO_H
#define CAMERA_PROTO_H

#include <stdint.h>

// Metadata the camera HAL reports for one captured frame (see hal_camera_frame_get in
// platform_hal.h). Encoded into the wire header below.
typedef struct {
    uint32_t frame_id;   // monotonic device frame counter
    uint16_t width;      // pixels
    uint16_t height;     // pixels
    uint32_t t_us;       // device capture time in microseconds (0 if unavailable)
} hal_cam_meta_t;

// Wire layout of the camera-data NATS message body (little-endian):
//   u32 frame_id | u16 width | u16 height | u32 t_us | u8[] jpeg
// The 12-byte header is immediately followed by the complete JPEG bitstream (SOI..EOI).
#define CAM_PROTO_HDR_LEN  (12u)

// Serialize `m` into the little-endian wire header. `out` must hold CAM_PROTO_HDR_LEN bytes.
void cam_encode_header(uint8_t out[CAM_PROTO_HDR_LEN], const hal_cam_meta_t *m);

#endif  // CAMERA_PROTO_H
