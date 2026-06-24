// ABOUTME: Host harness for the feature extractor — reads accel floats on stdin, prints int8 output.
// ABOUTME: Driven by test/feature_cross_test.py to cross-check against spectro.py byte-for-byte.

#include <stdio.h>
#include <stdlib.h>

#include "features.h"

// stdin:  one line "N" (number of samples), then N*3 floats (x y z interleaved), whitespace-sep.
// stdout: FEAT_OUT_LEN int8 values, one per line.
int main(void) {
    int n = 0;
    if (scanf("%d", &n) != 1 || n <= 0 || n > FEAT_WINDOW_SAMPLES) {
        return 2;
    }
    float *accel = malloc((size_t)n * 3 * sizeof(float));
    if (!accel) return 3;
    for (int i = 0; i < n * 3; i++) {
        if (scanf("%f", &accel[i]) != 1) {
            free(accel);
            return 4;
        }
    }
    int8_t out[FEAT_OUT_LEN];
    int rc = features_from_accel(accel, n, out);
    free(accel);
    if (rc != 0) return 5;
    for (int i = 0; i < FEAT_OUT_LEN; i++) {
        printf("%d\n", (int)out[i]);
    }
    return 0;
}
