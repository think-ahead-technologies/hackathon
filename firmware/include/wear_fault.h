// ABOUTME: Single-window IMU bearing/wear fault detector using conservative baseline ratios.
// ABOUTME: Pure C reference path for the analysis/fault_slice_error.py threshold criteria.

#ifndef WEAR_FAULT_H
#define WEAR_FAULT_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define WEAR_FAULT_MAX_SAMPLES 4096U

typedef enum {
    WEAR_FAULT_STATUS_OK = 0,
    WEAR_FAULT_STATUS_FAULT = 1,
    WEAR_FAULT_STATUS_INVALID_INPUT = 2,
} wear_fault_status_t;

typedef struct {
    float ax;
    float ay;
    float az;
} wear_fault_sample_t;

typedef struct {
    float a_env_rms;
    float a_env_p2p;
    float a_jerk_mad;
    float a_rms;
    float a_std;
    float a_p2p;
    float a_rms_z;
} wear_fault_baseline_t;

typedef struct {
    wear_fault_status_t status;
    float fault_percent;  // 0..100 progress to the conservative FAULT threshold.
    float anomaly_score;  // Unclamped progress ratio; 1.0 means the FAULT threshold is met.
} wear_fault_result_t;

// Evaluate one completed accelerometer window. Baseline values are healthy-window medians
// from the same unit/condition. A FAULT requires all conservative criteria to pass:
// env RMS >= 2.8x, env P2P >= 2.8x, jerk >= 2.4x, broadband energy >= 2.1x.
wear_fault_result_t wear_fault_detect_window(const wear_fault_sample_t *samples,
                                             size_t sample_count,
                                             float sample_rate_hz,
                                             const wear_fault_baseline_t *baseline);

#ifdef __cplusplus
}
#endif

#endif  // WEAR_FAULT_H