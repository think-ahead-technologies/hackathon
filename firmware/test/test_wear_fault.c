// ABOUTME: Unit tests for the conservative single-window wear fault detector.
// ABOUTME: Exercises invalid input, quiet windows, high-band faults, and low-band non-faults.

#include "test_util.h"

#include <math.h>

#include "wear_fault.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

static wear_fault_baseline_t tiny_baseline(void) {
    wear_fault_baseline_t b;
    b.a_env_rms = 0.01f;
    b.a_env_p2p = 0.01f;
    b.a_jerk_mad = 0.01f;
    b.a_rms = 0.01f;
    b.a_std = 0.01f;
    b.a_p2p = 0.01f;
    b.a_rms_z = 0.01f;
    return b;
}

static void fill_flat(wear_fault_sample_t *samples, size_t n) {
    for (size_t i = 0; i < n; i++) {
        samples[i].ax = 0.0f;
        samples[i].ay = 0.0f;
        samples[i].az = 9.80665f;
    }
}

static void fill_z_sine(wear_fault_sample_t *samples, size_t n, float fs,
                        float freq, float amp) {
    for (size_t i = 0; i < n; i++) {
        const double phase = 2.0 * M_PI * (double)freq * (double)i / (double)fs;
        samples[i].ax = 0.0f;
        samples[i].ay = 0.0f;
        samples[i].az = 9.80665f + amp * (float)sin(phase);
    }
}

static void fill_z_high_band_burst(wear_fault_sample_t *samples, size_t n, float fs) {
    for (size_t i = 0; i < n; i++) {
        const float amp = i < n / 2U ? 3.0f : 0.0f;
        const double phase = 2.0 * M_PI * 16.0 * (double)i / (double)fs;
        samples[i].ax = 0.0f;
        samples[i].ay = 0.0f;
        samples[i].az = 9.80665f + amp * (float)sin(phase);
    }
}

void run_wear_fault_tests(void) {
    wear_fault_sample_t samples[64];
    wear_fault_baseline_t baseline = tiny_baseline();

    wear_fault_result_t r = wear_fault_detect_window(0, 64, 64.0f, &baseline);
    CHECK(r.status == WEAR_FAULT_STATUS_INVALID_INPUT);

    fill_flat(samples, 64);
    r = wear_fault_detect_window(samples, 64, 64.0f, &baseline);
    CHECK(r.status == WEAR_FAULT_STATUS_OK);
    CHECK(fabsf(r.fault_percent) < 0.001f);
    CHECK(fabsf(r.anomaly_score) < 0.001f);

    fill_z_high_band_burst(samples, 64, 64.0f);
    r = wear_fault_detect_window(samples, 64, 64.0f, &baseline);
    CHECK(r.status == WEAR_FAULT_STATUS_FAULT);
    CHECK(r.fault_percent == 100.0f);
    CHECK(r.anomaly_score >= 1.0f);

    fill_z_sine(samples, 64, 64.0f, 2.0f, 3.0f);
    r = wear_fault_detect_window(samples, 64, 64.0f, &baseline);
    CHECK(r.status == WEAR_FAULT_STATUS_OK);
    CHECK(r.fault_percent < 100.0f);
}