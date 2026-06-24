// ABOUTME: Shadow-mode drift accumulation + promote/rollback verdict — pure float logic.

#include <math.h>

#include "shadow.h"

void shadow_reset(shadow_stats_t *s) {
    s->n = 0;
    s->sum_abs_delta = 0.0f;
    s->max_abs_delta = 0.0f;
}

void shadow_observe(shadow_stats_t *s, float old_score, float new_score) {
    float delta = fabsf(new_score - old_score);
    s->n++;
    s->sum_abs_delta += delta;
    if (delta > s->max_abs_delta) {
        s->max_abs_delta = delta;
    }
}

shadow_verdict_t shadow_decide(const shadow_stats_t *s, const shadow_policy_t *p) {
    // Any single window that diverges wildly is disqualifying on its own — no need to
    // wait out the full shadow window for an obviously-broken candidate.
    if (s->n > 0 && s->max_abs_delta > p->max_single_abs_delta) {
        return SHADOW_ROLLBACK;
    }
    // Otherwise hold judgement until we have a representative sample.
    if (s->n < p->min_windows) {
        return SHADOW_PENDING;
    }
    float mean = s->sum_abs_delta / (float)s->n;
    return (mean <= p->max_mean_abs_delta) ? SHADOW_PROMOTE : SHADOW_ROLLBACK;
}
