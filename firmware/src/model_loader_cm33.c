// ABOUTME: CM33-side model-loader driver — turns the model_loader.h calls into SHARED_MODEL_CTRL
// ABOUTME: commands to CM55 (which owns the NPU) and reads scores back from the shared mailbox.

// CM33 does not run inference in the CM55 architecture: it owns NATS, flash, signature verification
// and the shadow verdict, while CM55 runs the active + candidate models on the Ethos-U55. So the
// model_loader.h surface that device_main.c calls is implemented here as cross-core control:
//   load_active / load_candidate  -> MC_LOAD_* (slot offset + len + scoring params)
//   clear_candidate               -> MC_CLEAR_CAND
//   infer                         -> read the latest scores CM55 published into SHARED_SCORE
// The model BYTES never cross the mailbox — both cores reach them in QSPI via SMIF XIP; only the
// slot offset, length and scoring params are posted. See firmware/docs/cm55-model-loading.md.

#include "model_loader.h"

#include <stddef.h>
#include <stdint.h>

#include "platform_hal.h"    // hal_meta_read, hal_sleep_ms
#include "score.h"           // score_params_t, score_default_params
#include "shared_score.h"    // SHARED_SCORE, SHARED_MODEL_CTRL, mc_cmd_t

// Cross-core memory ordering: publish the command fields BEFORE the cmd_seq bump CM55 polls on, and
// order the ack read after it. __DMB() on-target; a no-op where the intrinsic is unavailable (this
// file is built on-target only, alongside the rest of the CM33-NS app).
#if defined(__has_include)
#  if __has_include("cmsis_compiler.h")
#    include "cmsis_compiler.h"
#    define MODEL_CTRL_DMB() __DMB()
#  endif
#endif
#ifndef MODEL_CTRL_DMB
#  define MODEL_CTRL_DMB() ((void)0)
#endif

// How long CM33 waits for CM55 to acknowledge a command. A model load on CM55 (XIP map +
// mtb_ml_model_init) is tens of ms; 2 s of 1 ms polls is comfortably generous.
#define CTRL_ACK_POLL_MS    1u
#define CTRL_ACK_MAX_POLLS  2000u

// Scoring params for a slot: the model-carried set persisted at deploy time, or the compiled-in
// baseline for a slot that predates model-carried params (out_scale == 0 means "unset"; a real
// quant scale is never zero). A valid score never depends on stale build-time constants.
static void slot_score_params(const slot_meta_t *s, score_params_t *out) {
    if (s->score.out_scale != 0.0f) {
        *out = s->score;
    } else {
        score_default_params(out);
    }
}

// Post one command to CM55 and block until it acknowledges. Returns CM55's status (0 = OK,
// <0 = command failed) or -1 on ack timeout.
static int ctrl_post(mc_cmd_t cmd, uint32_t offset, uint32_t len, const score_params_t *params) {
    volatile shared_model_ctrl_t *c = SHARED_MODEL_CTRL;

    c->cmd           = (uint32_t)cmd;
    c->target_offset = offset;
    c->target_len    = len;
    if (params != NULL) {
        c->params = *params;
    }
    uint32_t seq = c->cmd_seq + 1u;
    MODEL_CTRL_DMB();          // fields visible to CM55 before the trigger
    c->cmd_seq = seq;          // the bump CM55 polls on

    for (uint32_t i = 0; i < CTRL_ACK_MAX_POLLS; i++) {
        if (c->ack_seq == seq) {
            MODEL_CTRL_DMB();  // order the status read after seeing the ack
            return (int)c->status;
        }
        hal_sleep_ms(CTRL_ACK_POLL_MS);
    }
    return -1;                 // CM55 never acknowledged
}

// Tell CM55 to run `slot` as its active model. If the slot has no flash-resident model (the
// connectivity-first build synthesizes an empty slot A), CM55 keeps the baked-in boot model and
// we report success — the device still comes up and infers.
bool model_loader_load_active(slot_id_t slot) {
    model_meta_t m;
    if (!hal_meta_read(&m)) {
        return false;
    }
    const slot_meta_t *s = &m.slot[slot];
    if (!s->valid || s->len == 0) {
        return true;   // no flash model -> CM55's baked fallback stays active
    }
    score_params_t p;
    slot_score_params(s, &p);
    return ctrl_post(MC_LOAD_ACTIVE, s->flash_offset, s->len, &p) == 0;
}

// Tell CM55 to load `slot` as the candidate model to shadow against the active one. There is no
// candidate to shadow unless the slot holds a written, verified model.
bool model_loader_load_candidate(slot_id_t slot) {
    model_meta_t m;
    if (!hal_meta_read(&m)) {
        return false;
    }
    const slot_meta_t *s = &m.slot[slot];
    if (!s->valid || s->len == 0) {
        return false;
    }
    score_params_t p;
    slot_score_params(s, &p);
    return ctrl_post(MC_LOAD_CANDIDATE, s->flash_offset, s->len, &p) == 0;
}

// Inference runs on CM55; CM33 just reports the latest scores from the shared mailbox. Returns the
// active score (0 until CM55 is live); when a candidate is being shadowed, *candidate_out gets its
// score and *have_candidate is set.
float model_loader_infer(const int8_t *features, size_t len,
                         float *candidate_out, bool *have_candidate) {
    (void)features;
    (void)len;   // the IMU + feature extractor + NPU are all on CM55
    volatile shared_score_t *s = SHARED_SCORE;
    bool live = (s->magic == SHARED_SCORE_MAGIC);
    if (have_candidate != NULL) {
        *have_candidate = live && (s->have_candidate != 0u);
    }
    if (candidate_out != NULL) {
        *candidate_out = live ? s->candidate_score : 0.0f;
    }
    return live ? s->score : 0.0f;
}

// Make CM55's candidate its active model (pointer-swap, no flash reload), clearing the candidate
// role. device_main flips the persistent metadata before calling this. Returns true on ack OK.
bool model_loader_promote(void) {
    return ctrl_post(MC_PROMOTE, 0, 0, NULL) == 0;
}

// Drop the candidate on CM55 (after a rollback decision); the active model is untouched.
void model_loader_clear_candidate(void) {
    ctrl_post(MC_CLEAR_CAND, 0, 0, NULL);
}
