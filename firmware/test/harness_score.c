// ABOUTME: Host harness for scoring — reads 8 int8 embedding values on stdin, prints L2 distance.
// ABOUTME: Driven by test/score_cross_test.py to cross-check against baseline.distances.

#include <stdio.h>

#include "score.h"

int main(void) {
    int8_t emb[SCORE_EMBED_DIM];
    for (int i = 0; i < SCORE_EMBED_DIM; i++) {
        int v;
        if (scanf("%d", &v) != 1) return 2;
        emb[i] = (int8_t)v;
    }
    printf("%.7f\n", (double)score_distance(emb));
    return 0;
}
