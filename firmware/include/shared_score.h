// ABOUTME: Cross-core score mailbox — CM55 (NPU inference) -> CM33-NS (NATS publish).
// ABOUTME: Fixed struct at the start of the m33_m55_shared SOCMEM region, mapped by both cores.

#ifndef SHARED_SCORE_H
#define SHARED_SCORE_H

#include <stdint.h>

#include "features.h"   // FEAT_OUT_LEN — the int8 spectrogram window size

// Both cores' linkers map m33_m55_shared at this absolute SOCMEM address (0x40000 reserved).
// Placing the mailbox at the region start is safe: the BSP only reserves the region (no real
// data lands there), so a fixed-address overlay is the intended cross-core use.
#define SHARED_SOCMEM_BASE   0x262fc000u
#define SHARED_SCORE_MAGIC   0x57454152u   // 'WEAR' — set by CM55 once inference is live

typedef struct {
    volatile uint32_t magic;   // SHARED_SCORE_MAGIC once CM55 has produced a score
    volatile uint32_t seq;     // incremented each CM55 inference (liveness)
    volatile float    score;   // latest dwell-smoothed anomaly score from the NPU
    // Latest int8 feature window CM55 fed the model — mirrored here so CM33 can publish it for
    // Contract E training capture (the features live on CM55, where the IMU + NPU are).
    volatile int8_t   features[FEAT_OUT_LEN];
} shared_score_t;

#define SHARED_SCORE ((volatile shared_score_t *)SHARED_SOCMEM_BASE)

#endif  // SHARED_SCORE_H
