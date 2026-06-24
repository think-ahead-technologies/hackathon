// ABOUTME: A/B flash model-slot metadata and the atomic promote/rollback state machine.
// ABOUTME: Vendor-neutral logic (no flash I/O here) — pairs with platform_hal.h for the writes.

#ifndef MODEL_SLOT_H
#define MODEL_SLOT_H

#include <stdbool.h>
#include <stdint.h>

// Two slots give atomic promotion + instant rollback: always write the INACTIVE
// slot, flip `active` only after verification, flip back to recover. (model-pipeline.md Part 2.)
typedef enum {
    SLOT_A = 0,
    SLOT_B = 1,
    SLOT_COUNT = 2,
} slot_id_t;

// One model slot's metadata, mirrored in the tiny atomic metadata region of QSPI flash.
typedef struct {
    uint32_t flash_offset;  // where this slot's flatbuffer lives in QSPI
    uint32_t len;           // flatbuffer length in bytes
    uint8_t  sha256[32];    // expected digest of the flatbuffer
    uint8_t  sig[64];       // detached ECDSA-P256 sig over the model's manifest (deploy-time auth)
    char     version[48];   // e.g. "pdm-anomaly@2026.06.15-a3f1"
    bool     valid;         // written + verified; only a valid slot may be promoted
} slot_meta_t;

typedef struct {
    slot_id_t   active;
    slot_meta_t slot[SLOT_COUNT];
} model_meta_t;

// The slot a new model must be written to: always the one that is NOT active.
slot_id_t slot_inactive(slot_id_t active);

// The currently-active slot's metadata.
const slot_meta_t *meta_active(const model_meta_t *m);

// Promote `to`: make it active. Refuses (returns false, leaves `active` unchanged)
// unless that slot is marked valid — a model that failed verification never goes live.
bool meta_promote(model_meta_t *m, slot_id_t to);

// Roll back to the other slot. Refuses (returns false) unless that slot is still valid.
bool meta_rollback(model_meta_t *m);

#endif  // MODEL_SLOT_H
