// ABOUTME: A/B slot promote/rollback state machine — pure logic over the metadata struct.
// ABOUTME: The atomic flash write of the flipped metadata is hal_meta_write() (platform_hal.h).

#include "model_slot.h"

slot_id_t slot_inactive(slot_id_t active) {
    return (active == SLOT_A) ? SLOT_B : SLOT_A;
}

const slot_meta_t *meta_active(const model_meta_t *m) {
    return &m->slot[m->active];
}

bool meta_promote(model_meta_t *m, slot_id_t to) {
    // A slot only goes live once it has been written AND verified (valid). This is what
    // stops an un-verified or half-written model from ever becoming active.
    if (!m->slot[to].valid) {
        return false;
    }
    m->active = to;
    return true;
}

bool meta_rollback(model_meta_t *m) {
    slot_id_t prev = slot_inactive(m->active);
    if (!m->slot[prev].valid) {
        return false;  // nothing safe to fall back to
    }
    m->active = prev;
    return true;
}
