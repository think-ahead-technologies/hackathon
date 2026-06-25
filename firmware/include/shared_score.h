// ABOUTME: Cross-core mailbox — CM55 (NPU inference) <-> CM33-NS (NATS publish + model control).
// ABOUTME: Two fixed-address overlays in the m33_m55_shared SOCMEM region, mapped by both cores.

#ifndef SHARED_SCORE_H
#define SHARED_SCORE_H

#include <stdint.h>

#include "features.h"   // FEAT_OUT_LEN — the int8 spectrogram window size
#include "score.h"      // score_params_t, SCORE_EMBED_DIM — scoring travels with the model

// Both cores' linkers map m33_m55_shared at this absolute SOCMEM address (0x40000 reserved).
// Placing the mailboxes here is safe: the BSP only reserves the region (no real data lands there),
// so a fixed-address overlay is the intended cross-core use. The score mailbox sits at the region
// start; the model-control mailbox at a fixed offset comfortably past it (see SHARED_CTRL_OFFSET).
#define SHARED_SOCMEM_BASE   0x262fc000u
#define SHARED_SCORE_MAGIC   0x57454152u   // 'WEAR' — set by CM55 once inference is live

// CM55 -> CM33. Carries the ACTIVE model score always, plus the CANDIDATE score while shadowing.
// The bearing_rf_* fields carry the board-side RandomForest result for the latest completed
// 1 s numeric IMU window; CM33 publishes those fields to NATS without disturbing the NPU shadow path.
typedef struct {
    volatile uint32_t magic;            // SHARED_SCORE_MAGIC once CM55 has produced a score
    volatile uint32_t seq;              // incremented each CM55 inference (liveness)
    volatile float    score;            // latest dwell-smoothed anomaly score (ACTIVE model)
    volatile float    candidate_score;  // latest CANDIDATE score; meaningful only when have_candidate
    volatile uint32_t have_candidate;   // 1 while a candidate model is loaded and being shadowed
    volatile uint32_t bearing_rf_seq;            // increments after each RF inference window
    volatile uint32_t bearing_rf_window_ms;      // monotonic end time of the 1 s window
    volatile uint32_t bearing_rf_status;         // bearing_rf_status_t as uint32_t
    volatile float    bearing_rf_score;          // raw RF probability-like score
    volatile float    bearing_rf_fault_percent;  // raw score as 0..100 percent
    // Latest int8 feature window CM55 fed the model — mirrored here so CM33 can publish it for
    // Contract E training capture (the features live on CM55, where the IMU + NPU are).
    volatile int8_t   features[FEAT_OUT_LEN];
} shared_score_t;

#define SHARED_SCORE ((volatile shared_score_t *)SHARED_SOCMEM_BASE)

// CM33 -> CM55 model control. CM33 bumps cmd_seq to issue a command; CM55 sets ack_seq = cmd_seq
// once it has handled it (and status to the result). The model BYTES never cross this mailbox —
// they live in QSPI flash, which both cores reach via SMIF XIP; only the slot offset crosses.
typedef enum {
    MC_NONE = 0,
    MC_LOAD_ACTIVE,      // load target_offset as the active model (params apply to it)
    MC_LOAD_CANDIDATE,   // load target_offset as the candidate model to shadow
    MC_PROMOTE,          // candidate becomes active; candidate slot cleared
    MC_CLEAR_CAND,       // discard the candidate; keep the incumbent active model
} mc_cmd_t;

typedef struct {
    volatile uint32_t       cmd_seq;        // CM33 increments to post a command
    volatile uint32_t       cmd;            // mc_cmd_t
    volatile uint32_t       target_offset;  // QSPI flash offset of the slot to load (XIP base + this)
    volatile uint32_t       target_len;     // flatbuffer length (cache-invalidate range on CM55)
    volatile score_params_t params;         // scoring for the slot being loaded (LOAD_* commands)
    volatile uint32_t       ack_seq;        // CM55 sets = cmd_seq when the command is handled
    volatile int32_t        status;         // 0 = OK, <0 = command failed (e.g. model load failed)
} shared_model_ctrl_t;

// Fixed offset of the control mailbox within the shared region. 4 KB in — well past shared_score_t
// (~2 KB with the feature window). Both cores must agree; keep in sync if the score struct grows.
#define SHARED_CTRL_OFFSET   0x1000u
#define SHARED_MODEL_CTRL \
    ((volatile shared_model_ctrl_t *)(SHARED_SOCMEM_BASE + SHARED_CTRL_OFFSET))

#endif  // SHARED_SCORE_H
