// ABOUTME: Smoke tests for the exported bearing RandomForest C inference.
// ABOUTME: Exact sklearn parity is covered by test/bearing_rf_cross_test.py.

#include "test_util.h"

#include "bearing_rf.h"

void run_bearing_rf_tests(void) {
    float zeros[BEARING_RF_FEATURE_COUNT] = {0};
    bearing_rf_result_t r = bearing_rf_detect_features(0);
    CHECK(r.status == BEARING_RF_STATUS_INVALID_INPUT);
    CHECK(r.score == 0.0f);
    CHECK(r.fault_percent == 0.0f);

    r = bearing_rf_detect_features(zeros);
    CHECK(r.status == BEARING_RF_STATUS_OK || r.status == BEARING_RF_STATUS_FAULT);
    CHECK(r.score >= 0.0f);
    CHECK(r.score <= 1.0f);
    CHECK(r.fault_percent >= 0.0f);
    CHECK(r.fault_percent <= 100.0f);
}