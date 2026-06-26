// ABOUTME: Power-fail-atomic metadata selection over two copies — CRC, validity, newest/target.
// ABOUTME: Pure logic; the flash reads/writes of the two copies live in the HAL.

#include <stddef.h>

#include "meta_store.h"

uint32_t meta_crc32(const uint8_t *data, size_t len) {
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int k = 0; k < 8; k++) {
            crc = (crc >> 1) ^ ((crc & 1u) ? 0xEDB88320u : 0u);
        }
    }
    return ~crc;
}

// CRC covers every byte before the crc field (magic, seq, meta, and any padding).
static uint32_t blob_crc(const meta_blob_t *b) {
    return meta_crc32((const uint8_t *)b, offsetof(meta_blob_t, crc));
}

bool meta_blob_valid(const meta_blob_t *b) {
    if (b->magic != META_BLOB_MAGIC) {
        return false;
    }
    return b->crc == blob_crc(b);
}

void meta_blob_finalize(meta_blob_t *b) {
    b->magic = META_BLOB_MAGIC;
    b->crc = blob_crc(b);
}

// `seq` is treated as strictly increasing: a plain `>` picks the newer copy. It is uint32_t and
// bumped once per metadata write, so it would only wrap after ~4 billion deploys — at which point
// the wrapped copy would read as older. That is far beyond any device's service life; if a use
// ever approaches it, switch these comparisons to serial-number arithmetic (signed wrap delta).
int meta_select_newest(const meta_blob_t *a, const meta_blob_t *b, model_meta_t *out) {
    bool va = meta_blob_valid(a);
    bool vb = meta_blob_valid(b);
    if (va && vb) {
        int idx = (b->seq > a->seq) ? 1 : 0;
        *out = (idx == 1) ? b->meta : a->meta;
        return idx;
    }
    if (va) {
        *out = a->meta;
        return 0;
    }
    if (vb) {
        *out = b->meta;
        return 1;
    }
    return -1;
}

int meta_select_write_target(const meta_blob_t *a, const meta_blob_t *b, uint32_t *next_seq) {
    bool va = meta_blob_valid(a);
    bool vb = meta_blob_valid(b);
    if (!va && !vb) {
        *next_seq = 1u;  // fresh device — start the sequence
        return 0;
    }
    int newest;
    uint32_t newest_seq;
    if (va && vb) {
        newest = (b->seq > a->seq) ? 1 : 0;
        newest_seq = (newest == 1) ? b->seq : a->seq;
    } else if (va) {
        newest = 0;
        newest_seq = a->seq;
    } else {
        newest = 1;
        newest_seq = b->seq;
    }
    *next_seq = newest_seq + 1u;
    return 1 - newest;  // write the non-authoritative copy
}
