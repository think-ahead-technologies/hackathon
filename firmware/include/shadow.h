// ABOUTME: Shadow-mode drift comparison + promote/rollback decision for a newly-loaded model.
// ABOUTME: The self-healing beat — run old + new on live windows, promote only if they agree.

#ifndef SHADOW_H
#define SHADOW_H

#include <stdbool.h>
#include <stdint.h>

// Running comparison of the candidate model's scores against the incumbent's, over the
// first N live windows after a swap (model-pipeline.md Part 2, steps 7-8).
typedef struct {
    uint32_t n;              // windows compared so far
    float    sum_abs_delta;  // accumulated |new_score - old_score|
    float    max_abs_delta;  // worst single-window divergence
} shadow_stats_t;

// Bounds the candidate must stay within to be trusted.
typedef struct {
    uint32_t min_windows;          // don't decide until at least this many windows seen
    float    max_mean_abs_delta;   // average divergence allowed
    float    max_single_abs_delta; // any single window beyond this => reject outright
} shadow_policy_t;

typedef enum {
    SHADOW_PENDING,   // not enough windows yet — keep shadowing
    SHADOW_PROMOTE,   // candidate tracks the incumbent — safe to promote
    SHADOW_ROLLBACK,  // candidate drifted — flip active_slot back
} shadow_verdict_t;

void shadow_reset(shadow_stats_t *s);

// Feed one live window's pair of scores (incumbent vs candidate).
void shadow_observe(shadow_stats_t *s, float old_score, float new_score);

// Decide based on accumulated stats. A single wild divergence rejects immediately;
// otherwise we wait for min_windows, then promote iff mean drift is within bounds.
shadow_verdict_t shadow_decide(const shadow_stats_t *s, const shadow_policy_t *p);

#endif  // SHADOW_H
