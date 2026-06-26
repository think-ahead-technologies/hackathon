// ABOUTME: Contract C framing — parse the fixed frame header, reassemble the chunked flatbuffer.
// ABOUTME: Pure logic; the flash write of each accepted chunk is done by the caller via the HAL.

#include "deploy.h"

static uint32_t rd_u32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static uint16_t rd_u16(const uint8_t *p) {
    return (uint16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8));
}

bool deploy_parse_header(const uint8_t *msg, size_t msg_len, deploy_hdr_t *out) {
    if (msg_len < DEPLOY_HDR_BYTES) {
        return false;
    }
    out->magic = rd_u32(msg + 0);
    out->part = rd_u16(msg + 4);
    out->flags = rd_u16(msg + 6);
    out->total_len = rd_u32(msg + 8);
    out->offset = rd_u32(msg + 12);
    out->chunk_len = rd_u32(msg + 16);

    if (out->magic != DEPLOY_MAGIC) {
        return false;
    }
    if (out->part < DEPLOY_PART_MANIFEST || out->part > DEPLOY_PART_MODEL) {
        return false;
    }
    // uint64 add: size_t is 32-bit on the target, so a crafted chunk_len near UINT32_MAX would
    // wrap a 32-bit sum below msg_len and pass this gate, breaking the "payload fits" guarantee
    // every downstream consumer relies on. Same guard the reassembly path uses below.
    if ((uint64_t)DEPLOY_HDR_BYTES + out->chunk_len > msg_len) {
        return false;  // declared payload doesn't fit the message
    }
    return true;
}

void deploy_rx_reset(deploy_rx_t *rx) {
    rx->total = 0;
    rx->received = 0;
    rx->started = false;
}

bool deploy_rx_accept(deploy_rx_t *rx, const deploy_hdr_t *h, uint32_t capacity) {
    if (h->part != DEPLOY_PART_MODEL) {
        return false;
    }
    if (!rx->started) {
        if (h->total_len == 0 || h->total_len > capacity) {
            return false;  // empty, or won't fit the fixed slot
        }
        rx->total = h->total_len;
        rx->received = 0;
        rx->started = true;
    } else if (h->total_len != rx->total) {
        return false;  // total changed mid-stream
    }
    if (h->offset != rx->received) {
        return false;  // must be contiguous: no gaps, overlaps, or reorders
    }
    if ((uint64_t)h->offset + h->chunk_len > rx->total) {
        return false;  // would overrun the declared total
    }
    rx->received += h->chunk_len;
    return true;
}

bool deploy_rx_complete(const deploy_rx_t *rx) {
    return rx->started && rx->received == rx->total;
}
