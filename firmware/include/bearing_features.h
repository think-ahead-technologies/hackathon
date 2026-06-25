// ABOUTME: Numeric IMU window adapter for the bearing RandomForest detector.
// ABOUTME: Produces the 10 analysis/features.py-compatible inputs for bearing_rf.c.

#ifndef BEARING_FEATURES_H
#define BEARING_FEATURES_H

#include <stdbool.h>
#include <stdint.h>

#include "bearing_rf.h"

#ifdef __cplusplus
extern "C" {
#endif

#define BEARING_SENSOR_HZ 50u
#define BEARING_SENSOR_WINDOW_MS 1000u
#define BEARING_SENSOR_WINDOW_SAMPLES 50u

typedef struct {
    uint32_t t_ms;
    float ax_ms2;
    float ay_ms2;
    float az_ms2;
    float gx_dps;
    float gy_dps;
    float gz_dps;
} bearing_sensor_sample_t;

typedef struct {
    bearing_sensor_sample_t samples[BEARING_SENSOR_WINDOW_SAMPLES];
    uint32_t write_index;
    uint32_t count;
    uint32_t total_pushed;
} bearing_sensor_window_t;

void bearing_window_init(bearing_sensor_window_t *window);
bool bearing_window_push(bearing_sensor_window_t *window,
                         const bearing_sensor_sample_t *sample,
                         bearing_sensor_sample_t *overwritten);
bool bearing_window_ready(const bearing_sensor_window_t *window);
uint32_t bearing_window_count(const bearing_sensor_window_t *window);
uint32_t bearing_window_end_ms(const bearing_sensor_window_t *window);

// Convert the latest 1 second numeric IMU window into BEARING_RF_FEATURE_COUNT floats.
// This intentionally stores only the current rolling accel/gyro samples; no audio/video or
// historical windows are retained. The filter stage uses the same Butterworth coefficients as
// analysis/features.py at 50 Hz, applied forward/backward inside the current window. SciPy's
// reference applies filtfilt over the full session before slicing windows, so edge transients can
// differ slightly until a full-session streaming filter state is ported.
bool bearing_extract_features(const bearing_sensor_window_t *window,
                              float features[BEARING_RF_FEATURE_COUNT]);

#ifdef __cplusplus
}
#endif

#endif  // BEARING_FEATURES_H