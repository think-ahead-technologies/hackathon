// ABOUTME: Contract C wire framing — fixed frame header + chunked-flatbuffer reassembly state.
// ABOUTME: Pure logic (header parse, contiguity/capacity/completion); flash writes are the HAL.

#ifndef DEPLOY_H
#define DEPLOY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define DEPLOY_MAGIC 0x43444331u  // 'C''D''C''1' — Contract-C framing v1

typedef enum {
    DEPLOY_PART_MANIFEST = 1,  // the Contract A manifest (small; one or few chunks)
    DEPLOY_PART_SIG = 2,       // detached ECDSA-P256 sig over the manifest (64 bytes)
    DEPLOY_PART_MODEL = 3,     // the flatbuffer (large; streamed in chunks)
} deploy_part_t;

#define DEPLOY_FLAG_LAST 0x0001u  // set on the final chunk of a part

#define DEPLOY_HDR_BYTES 20u

// Fixed 20-byte little-endian frame header; chunk_len payload bytes follow it in the message.
typedef struct {
    uint32_t magic;
    uint16_t part;       // deploy_part_t
    uint16_t flags;
    uint32_t total_len;  // total bytes of this part across all its chunks
    uint32_t offset;     // byte offset of this chunk within the part
    uint32_t chunk_len;  // payload bytes carried in this frame
} deploy_hdr_t;

// Parse a frame header from a received message. Validates magic, a known part, and that the
// declared chunk_len actually fits in msg_len. Returns false otherwise.
bool deploy_parse_header(const uint8_t *msg, size_t msg_len, deploy_hdr_t *out);

// Reassembly state for the (potentially large, chunked) MODEL flatbuffer.
typedef struct {
    uint32_t total;     // expected total bytes (taken from the first chunk); 0 until set
    uint32_t received;  // contiguous bytes accepted so far
    bool     started;
} deploy_rx_t;

void deploy_rx_reset(deploy_rx_t *rx);

// Accept one MODEL chunk, enforcing contiguous in-range writes:
//   - the first chunk sets total (rejected if 0 or > capacity)
//   - offset must equal received (no gaps, no overlap, no reorder)
//   - offset + chunk_len must not exceed total
// On success advances received and returns true (the caller writes the payload at h->offset).
bool deploy_rx_accept(deploy_rx_t *rx, const deploy_hdr_t *h, uint32_t capacity);

// True once all `total` bytes have been received contiguously.
bool deploy_rx_complete(const deploy_rx_t *rx);

#endif  // DEPLOY_H
