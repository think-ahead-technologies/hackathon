// ABOUTME: Cross-language harness for wear_fault.c; reads windows on stdin, prints C verdicts.
// ABOUTME: Driven by test/wear_fault_cross_test.py against real Imagimob session data.

#include <stdio.h>
#include <stdlib.h>

#include "wear_fault.h"

int main(void) {
    wear_fault_baseline_t baseline;
    int cases = 0;
    if (scanf("%f %f %f %f %f %f %f", &baseline.a_env_rms, &baseline.a_env_p2p,
              &baseline.a_jerk_mad, &baseline.a_rms, &baseline.a_std,
              &baseline.a_p2p, &baseline.a_rms_z) != 7) {
        return 2;
    }
    if (scanf("%d", &cases) != 1 || cases < 0) {
        return 3;
    }

    for (int c = 0; c < cases; c++) {
        int n = 0;
        float fs = 0.0f;
        if (scanf("%d %f", &n, &fs) != 2 || n <= 0 || n > (int)WEAR_FAULT_MAX_SAMPLES) {
            return 4;
        }
        wear_fault_sample_t *samples = (wear_fault_sample_t *)calloc((size_t)n, sizeof(*samples));
        if (!samples) {
            return 5;
        }
        for (int i = 0; i < n; i++) {
            if (scanf("%f %f %f", &samples[i].ax, &samples[i].ay, &samples[i].az) != 3) {
                free(samples);
                return 6;
            }
        }
        wear_fault_result_t r = wear_fault_detect_window(samples, (size_t)n, fs, &baseline);
        free(samples);
        printf("%d %.9g %.9g\n", (int)r.status, r.fault_percent, r.anomaly_score);
    }
    return 0;
}