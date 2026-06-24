// ABOUTME: Ethos-U55 NPU inference (CM55) — runs wear models via ml-middleware, from flash slots.
// ABOUTME: Holds active + candidate models for A/B shadow; on-target only (needs ml-middleware + U55).

#ifndef NPU_INFER_H
#define NPU_INFER_H

#include <stdbool.h>
#include <stdint.h>

#include "score.h"   // score_params_t — scoring travels with each model role

#ifdef __cplusplus
extern "C" {
#endif

// The two model roles CM55 can hold at once: the live model and a candidate being shadowed.
typedef enum { NPU_ACTIVE = 0, NPU_CANDIDATE = 1 } npu_role_t;

// Bring up the NPU runtime (mtb_ml_init wires the Ethos-U55 completion IRQ + enables the U55) and
// load the firmware's baked-in Vela model as the ACTIVE role with the compiled-in default scoring
// params. This is the boot fallback / factory image: a board with empty flash still infers. Returns
// false if NPU bring-up or the baked model load fails. Call once at startup.
bool npu_infer_init(void);

// Load a model for `role` from QSPI flash via SMIF XIP: the bytes at flash `offset` (length `len`)
// are mapped read-in-place and handed to ml-middleware; `params` is the model's scoring set (from
// its manifest). Replaces any model already in that role. Returns false on failure (the prior model
// in that role is dropped regardless). The caller (CM33) guarantees the slot is written + verified
// and SMIF is in XIP mode before issuing this.
bool npu_load_slot(npu_role_t role, uint32_t offset, uint32_t len, const score_params_t *params);

// Candidate becomes the active model (and its params), then the candidate role is cleared. Used on
// a shadow PROMOTE verdict. No-op if there is no candidate.
void npu_promote_candidate(void);

// Drop the candidate model, keeping the active model untouched. Used on a shadow ROLLBACK verdict.
void npu_clear_candidate(void);

// True if a model is currently loaded in `role`.
bool npu_role_loaded(npu_role_t role);

// Run one inference for `role`: `features` is the int8 [49*40] spectrogram (FEAT_OUT_LEN). Returns
// the anomaly score (L2 distance of the dequantized embedding to that role's centroid), or a
// negative value on error / if the role is not loaded.
float npu_run(npu_role_t role, const int8_t *features, int len);

// Convenience: run the ACTIVE role. Equivalent to npu_run(NPU_ACTIVE, ...). Kept so the existing
// CM55 task loop compiles unchanged until it is updated to drive both roles.
float npu_infer(const int8_t *features, int len);

#ifdef __cplusplus
}
#endif

#endif  // NPU_INFER_H
