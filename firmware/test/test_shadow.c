// ABOUTME: Tests for the shadow-mode drift comparison and promote/rollback verdict.

#include "shadow.h"
#include "test_util.h"

static shadow_policy_t policy(void) {
    shadow_policy_t p = {0};
    p.min_windows = 5;
    p.max_mean_abs_delta = 0.05f;
    p.max_single_abs_delta = 0.30f;
    return p;
}

void run_shadow_tests(void) {
    shadow_stats_t s;
    shadow_reset(&s);
    CHECK(s.n == 0);
    CHECK(s.sum_abs_delta == 0.0f);
    CHECK(s.max_abs_delta == 0.0f);

    shadow_policy_t p = policy();

    // Not enough windows yet -> PENDING.
    shadow_observe(&s, 0.20f, 0.21f);
    CHECK(s.n == 1);
    CHECK(shadow_decide(&s, &p) == SHADOW_PENDING);

    // Candidate that tracks the incumbent closely -> PROMOTE once min_windows reached.
    shadow_reset(&s);
    for (int i = 0; i < 6; i++) {
        shadow_observe(&s, 0.20f, 0.22f);  // delta 0.02 < 0.05 mean bound
    }
    CHECK(shadow_decide(&s, &p) == SHADOW_PROMOTE);

    // Candidate that consistently diverges past the mean bound -> ROLLBACK.
    shadow_reset(&s);
    for (int i = 0; i < 6; i++) {
        shadow_observe(&s, 0.20f, 0.30f);  // delta 0.10 > 0.05 mean bound
    }
    CHECK(shadow_decide(&s, &p) == SHADOW_ROLLBACK);

    // A single wild divergence rejects immediately, even before min_windows.
    shadow_reset(&s);
    shadow_observe(&s, 0.20f, 0.90f);  // delta 0.70 > 0.30 single bound
    CHECK(s.n == 1);
    CHECK(shadow_decide(&s, &p) == SHADOW_ROLLBACK);
}
