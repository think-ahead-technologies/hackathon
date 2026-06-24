// ABOUTME: Anomaly scoring — int8 encoder embedding -> L2 distance to healthy centroid -> dwell/alert.
// ABOUTME: Pure C, host-tested. Mirrors baseline.distances + the device scoring in model-meta.json.

#ifndef SCORE_H
#define SCORE_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SCORE_EMBED_DIM 8

// Per-unit commissioning baseline (baseline.json). The centroid is this unit's healthy
// mean embedding; threshold is the (1-fpr) healthy-distance quantile. Recomputed per board
// at commissioning WITHOUT retraining — here we ship the reference unit's values as defaults.
extern const float SCORE_CENTROID[SCORE_EMBED_DIM];
extern const float SCORE_THRESHOLD;     // alert when the dwell-smoothed distance exceeds this
#define SCORE_DWELL_W 3                  // windows averaged before thresholding

// Model-carried scoring parameters. A deployed model has its OWN output quantization and its OWN
// embedding space, so the centroid + output quant travel WITH the model (parsed from the manifest)
// rather than being compiled in. The compiled-in constants above are only the boot-fallback values.
typedef struct {
    float   centroid[SCORE_EMBED_DIM];  // per-unit healthy mean embedding
    float   threshold;                  // alert when the dwell-smoothed distance exceeds this
    float   out_scale;                  // model output dequantization scale
    int32_t out_zero_point;             // model output dequantization zero point
} score_params_t;

// Fill `out` with the firmware's compiled-in reference-unit defaults (the constants above plus the
// reference output quant). Used for the boot fallback and to seed before a manifest set arrives.
void score_default_params(score_params_t *out);

// Dequantize the int8 embedding (model output quant) and return its L2 distance to the
// healthy centroid — the raw anomaly score (matches baseline.distances).
float score_distance(const int8_t embedding[SCORE_EMBED_DIM]);

// Same as score_distance but using model-carried params instead of the compiled-in baseline.
// This is the path CM55 uses per model role (active / candidate) once models load from a slot.
float score_distance_with(const int8_t embedding[SCORE_EMBED_DIM], const score_params_t *p);

// Dwell smoothing: a trailing mean over the last SCORE_DWELL_W distances. Feed each window's
// raw distance; returns the smoothed value. State lives in `st` (zero-initialize before use).
typedef struct {
    float buf[SCORE_DWELL_W];
    int   count;   // total observations (saturates conceptually; index uses % W)
} dwell_t;

float score_dwell(dwell_t *st, float distance);

// Alert when the smoothed distance exceeds the unit threshold.
static inline bool score_alert(float smoothed) { return smoothed > SCORE_THRESHOLD; }

#ifdef __cplusplus
}
#endif

#endif  // SCORE_H
