// ABOUTME: camera-data wire header encoder — see camera_proto.h. Pure little-endian byte packing.
// ABOUTME: Host-tested (test/test_camera_proto.c); shared by the on-target camera publisher.

#include "camera_proto.h"

static void put_u16le(uint8_t *p, uint16_t v) {
    p[0] = (uint8_t)(v & 0xFFu);
    p[1] = (uint8_t)((v >> 8) & 0xFFu);
}

static void put_u32le(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)(v & 0xFFu);
    p[1] = (uint8_t)((v >> 8) & 0xFFu);
    p[2] = (uint8_t)((v >> 16) & 0xFFu);
    p[3] = (uint8_t)((v >> 24) & 0xFFu);
}

void cam_encode_header(uint8_t out[CAM_PROTO_HDR_LEN], const hal_cam_meta_t *m) {
    put_u32le(out + 0, m->frame_id);
    put_u16le(out + 4, m->width);
    put_u16le(out + 6, m->height);
    put_u32le(out + 8, m->t_us);
}
