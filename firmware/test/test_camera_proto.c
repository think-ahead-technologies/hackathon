// ABOUTME: Tests for the camera-data binary wire header and the zero-copy NATS PUB header line.
// ABOUTME: Locks the field order/endianness the camera-data contract pins down before any firmware.

#include <string.h>

#include "camera_proto.h"
#include "nats_proto.h"
#include "test_util.h"

void run_camera_proto_tests(void) {
    // ---- binary frame header: 12 bytes, little-endian, order frame_id|width|height|t_us ----
    CHECK(CAM_PROTO_HDR_LEN == 12);

    hal_cam_meta_t m = {
        .frame_id = 0x04030201u,
        .width    = 0x0605u,
        .height   = 0x0807u,
        .t_us     = 0x0C0B0A09u,
    };
    uint8_t hdr[CAM_PROTO_HDR_LEN];
    cam_encode_header(hdr, &m);

    // Distinct bytes per field confirm offset + width + little-endian order in one shot.
    const uint8_t expect[12] = {
        0x01, 0x02, 0x03, 0x04,   // frame_id LE
        0x05, 0x06,               // width LE
        0x07, 0x08,               // height LE
        0x09, 0x0A, 0x0B, 0x0C,   // t_us LE
    };
    CHECK(memcmp(hdr, expect, sizeof(expect)) == 0);

    // ---- PUB header line for zero-copy scatter send of a large JPEG payload ----
    // payload = 12-byte cam header + JPEG; with jpeg_len = 5 the total is 17.
    char pub[64];
    int n = nats_build_pub_header(pub, sizeof(pub), "edge.camera.line1.c1",
                                  CAM_PROTO_HDR_LEN + 5u);
    CHECK(n > 0);
    CHECK_STR_EQ(pub, "PUB edge.camera.line1.c1 17\r\n");
    CHECK(n == (int)strlen("PUB edge.camera.line1.c1 17\r\n"));

    // Header-only: no payload bytes and no trailing CRLF are written here (the caller appends
    // the binary header, the JPEG, then "\r\n" itself).
    CHECK(pub[n - 2] == '\r' && pub[n - 1] == '\n');

    // Too small for even the header line -> refuse rather than overflow.
    CHECK(nats_build_pub_header(pub, 8, "edge.camera.line1.c1", 17u) == -1);
}
