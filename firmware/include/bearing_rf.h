// ABOUTME: Bearing RandomForest detector API exported from analysis/export_bearing_rf_c.py.
// ABOUTME: Scores the 10-window feature vector used by analysis/features.py.

#ifndef BEARING_RF_H
#define BEARING_RF_H

#ifdef __cplusplus
extern "C" {
#endif

#define BEARING_RF_FEATURE_COUNT 10
#define BEARING_RF_TREE_COUNT 150
#define BEARING_RF_NODE_COUNT 22382
#define BEARING_RF_THRESHOLD 0.20974355f

typedef enum {
    BEARING_RF_STATUS_OK = 0,
    BEARING_RF_STATUS_FAULT = 1,
    BEARING_RF_STATUS_INVALID_INPUT = 2,
} bearing_rf_status_t;

typedef struct {
    bearing_rf_status_t status;
    float fault_percent;
    float score;
} bearing_rf_result_t;

extern const char *const BEARING_RF_FEATURE_NAMES[BEARING_RF_FEATURE_COUNT];

float bearing_rf_score(const float features[BEARING_RF_FEATURE_COUNT]);
bearing_rf_result_t bearing_rf_detect_features(const float features[BEARING_RF_FEATURE_COUNT]);

#ifdef __cplusplus
}
#endif

#endif  // BEARING_RF_H
