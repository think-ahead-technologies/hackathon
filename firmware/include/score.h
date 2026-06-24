// ABOUTME: Anomaly scoring — int8 encoder embedding -> L2 distance to healthy centroid -> dwell/alert.
// ABOUTME: Pure C, host-tested. Mirrors baseline.distances + the device scoring in model-meta.json.

#ifndef SCORE_H
#define SCORE_H

#include <stdbool.h>
#include <stdint.h>

#define SCORE_EMBED_DIM 8

// Per-unit commissioning baseline (baseline.json). The centroid is this unit's healthy
// mean embedding; threshold is the (1-fpr) healthy-distance quantile. Recomputed per board
// at commissioning WITHOUT retraining — here we ship the reference unit's values as defaults.
extern const float SCORE_CENTROID[SCORE_EMBED_DIM];
extern const float SCORE_THRESHOLD;     // alert when the dwell-smoothed distance exceeds this
#define SCORE_DWELL_W 3                  // windows averaged before thresholding

// Dequantize the int8 embedding (model output quant) and return its L2 distance to the
// healthy centroid — the raw anomaly score (matches baseline.distances).
float score_distance(const int8_t embedding[SCORE_EMBED_DIM]);

// Dwell smoothing: a trailing mean over the last SCORE_DWELL_W distances. Feed each window's
// raw distance; returns the smoothed value. State lives in `st` (zero-initialize before use).
typedef struct {
    float buf[SCORE_DWELL_W];
    int   count;   // total observations (saturates conceptually; index uses % W)
} dwell_t;

float score_dwell(dwell_t *st, float distance);

// Alert when the smoothed distance exceeds the unit threshold.
static inline bool score_alert(float smoothed) { return smoothed > SCORE_THRESHOLD; }

#endif  // SCORE_H
