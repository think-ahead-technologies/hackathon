// ABOUTME: Cross-language harness for bearing_rf.c; reads feature vectors and prints RF scores.
// ABOUTME: Driven by test/bearing_rf_cross_test.py against sklearn on real windows.

#include <stdio.h>

#include "bearing_rf.h"

int main(void) {
    int cases = 0;
    if (scanf("%d", &cases) != 1 || cases < 0) {
        return 2;
    }
    for (int c = 0; c < cases; c++) {
        float features[BEARING_RF_FEATURE_COUNT];
        for (int i = 0; i < BEARING_RF_FEATURE_COUNT; i++) {
            if (scanf("%f", &features[i]) != 1) {
                return 3;
            }
        }
        bearing_rf_result_t r = bearing_rf_detect_features(features);
        printf("%d %.9g %.9g\n", (int)r.status, r.score, r.fault_percent);
    }
    return 0;
}