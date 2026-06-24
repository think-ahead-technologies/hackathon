// ABOUTME: Tests for the power-fail-atomic metadata store — CRC, validity, copy selection.
// ABOUTME: Includes a crash-mid-write simulation proving the previous state always survives.

#include <string.h>

#include "meta_store.h"
#include "test_util.h"

static meta_blob_t make_blob(uint32_t seq, slot_id_t active) {
    meta_blob_t b;
    memset(&b, 0, sizeof(b));
    b.seq = seq;
    b.meta.active = active;
    b.meta.slot[SLOT_A].valid = true;
    b.meta.slot[SLOT_B].valid = true;
    meta_blob_finalize(&b);
    return b;
}

void run_meta_store_tests(void) {
    // CRC32 against the canonical check value for "123456789".
    CHECK(meta_crc32((const uint8_t *)"123456789", 9) == 0xCBF43926u);

    // finalize -> valid; a payload change without re-finalize -> invalid; bad magic -> invalid.
    meta_blob_t b = make_blob(1, SLOT_A);
    CHECK(meta_blob_valid(&b) == true);
    meta_blob_t stale = b;
    stale.meta.active = SLOT_B;  // mutate payload, leave the old crc
    CHECK(meta_blob_valid(&stale) == false);
    meta_blob_t badmagic = b;
    badmagic.magic = 0;
    CHECK(meta_blob_valid(&badmagic) == false);

    // newest selection by sequence.
    model_meta_t out;
    meta_blob_t a5 = make_blob(5, SLOT_A);
    meta_blob_t b6 = make_blob(6, SLOT_B);
    CHECK(meta_select_newest(&a5, &b6, &out) == 1);
    CHECK(out.active == SLOT_B);
    meta_blob_t a7 = make_blob(7, SLOT_A);
    CHECK(meta_select_newest(&a7, &b6, &out) == 0);
    CHECK(out.active == SLOT_A);

    // one valid copy wins; neither valid -> -1.
    meta_blob_t invalid;
    memset(&invalid, 0xFF, sizeof(invalid));  // bad magic + crc
    CHECK(meta_select_newest(&a5, &invalid, &out) == 0);
    CHECK(meta_select_newest(&invalid, &b6, &out) == 1);
    CHECK(meta_select_newest(&invalid, &invalid, &out) == -1);

    // write target is never the authoritative copy; sequence increments.
    uint32_t seq = 0;
    CHECK(meta_select_write_target(&a5, &b6, &seq) == 0);  // b6 newest -> write A
    CHECK(seq == 7);
    CHECK(meta_select_write_target(&a7, &b6, &seq) == 1);  // a7 newest -> write B
    CHECK(seq == 8);
    CHECK(meta_select_write_target(&invalid, &invalid, &seq) == 0);  // fresh device
    CHECK(seq == 1);
    CHECK(meta_select_write_target(&a5, &invalid, &seq) == 1);  // only A valid -> write B
    CHECK(seq == 6);

    // Power-fail safety: a crash mid-write leaves the target corrupt, previous state survives.
    meta_blob_t copy[2];
    copy[0] = make_blob(10, SLOT_A);          // authoritative
    memset(&copy[1], 0xFF, sizeof(copy[1]));  // never written
    int tgt = meta_select_write_target(&copy[0], &copy[1], &seq);  // -> 1, seq 11
    memset(&copy[tgt], 0x00, sizeof(copy[tgt]));  // crash: partial garbage, bad crc
    CHECK(meta_select_newest(&copy[0], &copy[1], &out) == 0);  // old copy still live
    CHECK(out.active == SLOT_A);

    // Clean completion of the same write makes the new state authoritative.
    meta_blob_t fresh;
    memset(&fresh, 0, sizeof(fresh));
    fresh.seq = seq;
    fresh.meta.active = SLOT_B;
    meta_blob_finalize(&fresh);
    copy[tgt] = fresh;
    CHECK(meta_select_newest(&copy[0], &copy[1], &out) == tgt);
    CHECK(out.active == SLOT_B);
}
