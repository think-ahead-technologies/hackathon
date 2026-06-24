// ABOUTME: Power-fail-atomic metadata store — two flash copies + monotonic sequence + CRC.
// ABOUTME: Pure selection logic (which copy is authoritative / where to write next); I/O is the HAL.

#ifndef META_STORE_H
#define META_STORE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "model_slot.h"

#define META_BLOB_MAGIC 0x4D444C32u  // "MDL2"

// One on-flash metadata copy. Two of these (A/B) give power-fail-atomic updates: always write
// the NON-authoritative copy with seq+1; the highest valid sequence wins on read. A half-written
// copy fails its CRC and is ignored, so the previous state always survives a crash mid-write.
typedef struct {
    uint32_t     magic;
    uint32_t     seq;   // monotonic; higher = newer
    model_meta_t meta;
    uint32_t     crc;   // CRC32 over everything before this field (magic, seq, meta)
} meta_blob_t;

// Standard CRC32 (reflected poly 0xEDB88320, init/final 0xFFFFFFFF) — matches zlib's crc32.
uint32_t meta_crc32(const uint8_t *data, size_t len);

// True iff magic matches AND the stored crc equals the recomputed crc.
bool meta_blob_valid(const meta_blob_t *b);

// Stamp magic + crc into a blob whose `seq` and `meta` are already set.
void meta_blob_finalize(meta_blob_t *b);

// Pick the newest valid copy of (a, b): returns 0 or 1 and fills *out; -1 if neither is valid.
int meta_select_newest(const meta_blob_t *a, const meta_blob_t *b, model_meta_t *out);

// Pick which copy to write next + the sequence to stamp. Always the copy that is NOT currently
// authoritative, so the live copy is never the one being erased. Returns 0 or 1; *next_seq is the
// sequence to write (1 when neither copy is currently valid — a fresh device).
int meta_select_write_target(const meta_blob_t *a, const meta_blob_t *b, uint32_t *next_seq);

#endif  // META_STORE_H
