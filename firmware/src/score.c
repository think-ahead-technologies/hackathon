// ABOUTME: Anomaly scoring — dequantize encoder embedding, L2 distance to centroid, dwell smooth.
// ABOUTME: Pure C port of baseline.distances + the device scoring described in model-meta.json.

#include "score.h"

#include <math.h>

// Model output quantization (quant.json) and the per-unit healthy baseline (baseline.json).
#define SCORE_OUT_SCALE 0.16173580288887024
#define SCORE_OUT_ZP    (-21)

const float SCORE_CENTROID[SCORE_EMBED_DIM] = {
    -2.4810235500335693f, 2.413456916809082f,  2.5946342945098877f, 3.3870949745178223f,
     2.060364007949829f,  2.1817684173583984f, 2.080946207046509f,  3.583448648452759f,
};
const float SCORE_THRESHOLD = 21.127588272094727f;

float score_distance(const int8_t embedding[SCORE_EMBED_DIM]) {
    double sumsq = 0.0;
    for (int k = 0; k < SCORE_EMBED_DIM; k++) {
        double e = ((double)embedding[k] - (double)SCORE_OUT_ZP) * SCORE_OUT_SCALE;
        double d = e - (double)SCORE_CENTROID[k];
        sumsq += d * d;
    }
    return (float)sqrt(sumsq);
}

float score_dwell(dwell_t *st, float distance) {
    st->buf[st->count % SCORE_DWELL_W] = distance;
    st->count++;
    int n = st->count < SCORE_DWELL_W ? st->count : SCORE_DWELL_W;
    double sum = 0.0;
    for (int i = 0; i < n; i++) {
        sum += st->buf[i];
    }
    return (float)(sum / n);
}
