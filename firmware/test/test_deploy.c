// ABOUTME: Tests for Contract C framing — header parsing and chunked-flatbuffer reassembly.

#include <string.h>

#include "deploy.h"
#include "test_util.h"

static void put_u32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)(v);
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}
static void put_u16(uint8_t *p, uint16_t v) {
    p[0] = (uint8_t)(v);
    p[1] = (uint8_t)(v >> 8);
}
static void make_hdr(uint8_t *buf, uint16_t part, uint16_t flags,
                     uint32_t total, uint32_t off, uint32_t clen) {
    put_u32(buf + 0, DEPLOY_MAGIC);
    put_u16(buf + 4, part);
    put_u16(buf + 6, flags);
    put_u32(buf + 8, total);
    put_u32(buf + 12, off);
    put_u32(buf + 16, clen);
}

void run_deploy_tests(void) {
    uint8_t buf[64];

    // ---- header parsing ----
    make_hdr(buf, DEPLOY_PART_MODEL, DEPLOY_FLAG_LAST, 250, 200, 50);
    deploy_hdr_t h;
    CHECK(deploy_parse_header(buf, DEPLOY_HDR_BYTES + 50, &h) == true);
    CHECK(h.part == DEPLOY_PART_MODEL);
    CHECK(h.flags == DEPLOY_FLAG_LAST);
    CHECK(h.total_len == 250);
    CHECK(h.offset == 200);
    CHECK(h.chunk_len == 50);

    CHECK(deploy_parse_header(buf, DEPLOY_HDR_BYTES, &h) == false);  // payload doesn't fit
    CHECK(deploy_parse_header(buf, 8, &h) == false);                 // shorter than the header

    uint8_t bad[64];
    memcpy(bad, buf, sizeof(bad));
    bad[0] ^= 0xFF;  // corrupt magic
    CHECK(deploy_parse_header(bad, sizeof(bad), &h) == false);

    make_hdr(buf, 99 /*unknown part*/, 0, 10, 0, 10);
    CHECK(deploy_parse_header(buf, DEPLOY_HDR_BYTES + 10, &h) == false);

    // chunk_len near UINT32_MAX must not wrap (size_t)DEPLOY_HDR_BYTES + chunk_len below msg_len
    // on a 32-bit target and slip past the "payload fits" gate.
    make_hdr(buf, DEPLOY_PART_MODEL, 0, 250, 0, 0xFFFFFFF0u);
    CHECK(deploy_parse_header(buf, DEPLOY_HDR_BYTES + 50, &h) == false);
    make_hdr(buf, DEPLOY_PART_MODEL, 0, 250, 0, 0xFFFFFFFFu);
    CHECK(deploy_parse_header(buf, DEPLOY_HDR_BYTES + 50, &h) == false);

    // ---- reassembly: a clean 3-chunk stream ----
    deploy_rx_t rx;
    deploy_rx_reset(&rx);
    CHECK(deploy_rx_complete(&rx) == false);

    CHECK(deploy_rx_accept(&rx, &(deploy_hdr_t){.part = DEPLOY_PART_MODEL, .total_len = 250,
                                                .offset = 0, .chunk_len = 100}, 1000) == true);
    CHECK(deploy_rx_complete(&rx) == false);
    CHECK(deploy_rx_accept(&rx, &(deploy_hdr_t){.part = DEPLOY_PART_MODEL, .total_len = 250,
                                                .offset = 100, .chunk_len = 100}, 1000) == true);
    CHECK(deploy_rx_accept(&rx, &(deploy_hdr_t){.part = DEPLOY_PART_MODEL, .total_len = 250,
                                                .offset = 200, .chunk_len = 50}, 1000) == true);
    CHECK(deploy_rx_complete(&rx) == true);

    // ---- reassembly rejects: gap, total>capacity, overrun, wrong part ----
    deploy_rx_reset(&rx);
    CHECK(deploy_rx_accept(&rx, &(deploy_hdr_t){.part = DEPLOY_PART_MODEL, .total_len = 250,
                                                .offset = 100, .chunk_len = 50}, 1000) == false);  // gap
    deploy_rx_reset(&rx);
    CHECK(deploy_rx_accept(&rx, &(deploy_hdr_t){.part = DEPLOY_PART_MODEL, .total_len = 2000,
                                                .offset = 0, .chunk_len = 100}, 1000) == false);   // > cap
    deploy_rx_reset(&rx);
    CHECK(deploy_rx_accept(&rx, &(deploy_hdr_t){.part = DEPLOY_PART_MODEL, .total_len = 100,
                                                .offset = 0, .chunk_len = 200}, 1000) == false);   // overrun
    deploy_rx_reset(&rx);
    CHECK(deploy_rx_accept(&rx, &(deploy_hdr_t){.part = DEPLOY_PART_MANIFEST, .total_len = 100,
                                                .offset = 0, .chunk_len = 100}, 1000) == false);   // not MODEL
}
