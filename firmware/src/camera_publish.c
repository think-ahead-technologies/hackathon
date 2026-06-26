// ABOUTME: camera-data publisher — see camera_publish.h. Scatter-sends JPEG frames to edge.camera.
// ABOUTME: On-target only (calls platform_hal); compiled by the app, not the host logic test build.

#include "camera_publish.h"

#include "nats_proto.h"
#include "platform_hal.h"

int camera_publish_frame(int sock, const char *subject, const hal_cam_meta_t *meta,
                         const uint8_t *jpeg, uint32_t jpeg_len) {
    uint8_t hdr[CAM_PROTO_HDR_LEN];
    cam_encode_header(hdr, meta);

    // One PUB whose payload is the binary frame header + the JPEG. Emitting the PUB header line
    // separately lets us send the JPEG straight from HAL/SOCMEM memory without copying ~tens of KB
    // into a contiguous frame buffer.
    char pub[96];
    int pn = nats_build_pub_header(pub, sizeof(pub), subject,
                                   (size_t)CAM_PROTO_HDR_LEN + jpeg_len);
    if (pn < 0) return 0;   // subject too long for the header buffer — skip, not a transport error

    int r;
    if ((r = hal_tcp_send(sock, (const uint8_t *)pub, (size_t)pn)) < 0) return r;
    if ((r = hal_tcp_send(sock, hdr, CAM_PROTO_HDR_LEN)) < 0) return r;
    if ((r = hal_tcp_send(sock, jpeg, jpeg_len)) < 0) return r;
    if ((r = hal_tcp_send(sock, (const uint8_t *)"\r\n", 2)) < 0) return r;
    return r;
}

int camera_publish_step(int sock, const char *subject) {
    const uint8_t *jpeg = NULL;
    uint32_t       len  = 0u;
    hal_cam_meta_t meta;

    if (!hal_camera_frame_get(&jpeg, &len, &meta)) {
        return 0;   // nothing new to publish
    }

    int r = camera_publish_frame(sock, subject, &meta, jpeg, len);
    hal_camera_frame_release();   // always release the slot, even on a transport error
    return (r < 0) ? r : 1;
}
