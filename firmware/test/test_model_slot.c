// ABOUTME: Tests for the A/B slot promote/rollback state machine (no flash I/O).

#include "model_slot.h"
#include "test_util.h"

static model_meta_t two_valid_slots(void) {
    model_meta_t m = {0};
    m.active = SLOT_A;
    m.slot[SLOT_A].valid = true;
    m.slot[SLOT_B].valid = true;
    return m;
}

void run_model_slot_tests(void) {
    CHECK(slot_inactive(SLOT_A) == SLOT_B);
    CHECK(slot_inactive(SLOT_B) == SLOT_A);

    model_meta_t m = two_valid_slots();
    CHECK(meta_active(&m) == &m.slot[SLOT_A]);

    // Promote the inactive (valid) slot -> active flips, returns true.
    CHECK(meta_promote(&m, SLOT_B) == true);
    CHECK(m.active == SLOT_B);

    // Roll back -> returns to the still-valid slot A.
    CHECK(meta_rollback(&m) == true);
    CHECK(m.active == SLOT_A);

    // A model that failed verification (invalid slot) must never be promoted.
    m = two_valid_slots();
    m.slot[SLOT_B].valid = false;
    CHECK(meta_promote(&m, SLOT_B) == false);
    CHECK(m.active == SLOT_A);  // unchanged

    // Rollback refuses if the other slot is not valid (nothing safe to fall back to).
    m = two_valid_slots();
    m.active = SLOT_B;
    m.slot[SLOT_A].valid = false;
    CHECK(meta_rollback(&m) == false);
    CHECK(m.active == SLOT_B);  // unchanged
}
