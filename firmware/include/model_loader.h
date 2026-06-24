// ABOUTME: TFLite Micro interpreter lifecycle over flash-resident model slots (active + candidate).
// ABOUTME: Implemented in model_loader.cc against the real TFLM C++ API — built on-target only.

#ifndef MODEL_LOADER_H
#define MODEL_LOADER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "model_slot.h"

#ifdef __cplusplus
extern "C" {
#endif

// Worst-case arena, fixed at build time. The CI Vela gate (dashboard/pipeline/gate.py)
// rejects any model whose arena_bytes exceeds this, so a deployed model always fits.
#define TENSOR_ARENA_BYTES (512u * 1024u)

// Verify and load the model in `slot` as the ACTIVE interpreter (signature -> GetModel ->
// AllocateTensors). Returns false if verification, schema, or allocation fails.
bool model_loader_load_active(slot_id_t slot);

// Load the model in `slot` as the CANDIDATE interpreter, for shadow comparison against
// the active one. Uses a second arena (a real memory cost — size for it up front).
bool model_loader_load_candidate(slot_id_t slot);

// Run one feature window through the active interpreter (and the candidate, if loaded).
// Returns the active model's anomaly score; when a candidate is loaded, *candidate_out
// receives its score and *have_candidate is set true.
float model_loader_infer(const int8_t *features, size_t len,
                         float *candidate_out, bool *have_candidate);

// Promote the candidate to active after the shadow verdict says so. The caller flips the
// persistent metadata first (so a reboot loads the new active); this makes the live runtime match —
// the candidate becomes active and the candidate role is cleared. Returns false if the runtime could
// not promote. (In the CM55 architecture this is a pointer-swap on CM55, not a flash reload.)
bool model_loader_promote(void);

// Drop the candidate interpreter (after a rollback decision).
void model_loader_clear_candidate(void);

#ifdef __cplusplus
}
#endif

#endif  // MODEL_LOADER_H
