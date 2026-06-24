# ABOUTME: Tests for fault location clustering — the pure correlation/grouping math (no camera needed).
# ABOUTME: image_feature() needs Pillow and is exercised only where available; the math is CI-safe.
import numpy as np

from wear_detector import fault_location


def test_cluster_groups_similar_and_splits_different():
    rng = np.random.default_rng(0)
    a = rng.standard_normal(64)           # location A pattern
    b = rng.standard_normal(64)           # location B pattern (independent)
    rows = [a, a + 0.01 * rng.standard_normal(64),   # two views of A
            b + 0.01 * rng.standard_normal(64),       # one view of B
            a + 0.01 * rng.standard_normal(64)]        # another A
    labels = fault_location.cluster_by_similarity(rows, threshold=0.5)
    assert labels[0] == labels[1] == labels[3]        # all A together
    assert labels[2] != labels[0]                     # B is its own location


def test_cluster_all_distinct_when_uncorrelated():
    rng = np.random.default_rng(1)
    rows = [rng.standard_normal(64) for _ in range(4)]
    labels = fault_location.cluster_by_similarity(rows, threshold=0.5)
    assert len(set(labels)) == 4                      # nothing correlates -> 4 locations


def test_cluster_empty():
    assert fault_location.cluster_by_similarity([]) == []
