# ABOUTME: Unit tests for the per-unit baseline Mahalanobis detector.
# ABOUTME: Synthetic healthy/anomaly clusters verify scoring, normalization, and thresholding.
import numpy as np
import pytest

from wear_detector.detector import PerUnitBaselineDetector

NAMES = ["f0", "f1", "f2"]


def _dicts(X):
    return [dict(zip(NAMES, row)) for row in X]


def _healthy(rng, n=500):
    return rng.normal(loc=[0, 0, 0], scale=[1, 1, 1], size=(n, 3))


def test_normalized_score_in_unit_interval():
    rng = np.random.default_rng(1)
    det = PerUnitBaselineDetector(NAMES).fit(_dicts(_healthy(rng)))
    s = det.score(_dicts(_healthy(rng, 100)))
    assert s.min() >= 0.0 and s.max() <= 1.0


def test_anomaly_scores_higher_than_healthy():
    rng = np.random.default_rng(2)
    det = PerUnitBaselineDetector(NAMES).fit(_dicts(_healthy(rng)))
    healthy = det.raw_scores(_dicts(_healthy(rng, 200)))
    anomaly = det.raw_scores(_dicts(rng.normal(loc=[6, 6, 6], scale=1, size=(200, 3))))
    assert anomaly.mean() > healthy.mean() * 3


def test_threshold_holds_false_positive_rate():
    rng = np.random.default_rng(3)
    det = PerUnitBaselineDetector(NAMES, threshold_pct=99.0).fit(_dicts(_healthy(rng, 2000)))
    fp = det.predict(_dicts(_healthy(rng, 2000))).mean()
    assert fp < 0.05  # ~1% expected at the 99th-pct threshold


def test_directed_mode_ignores_low_side_deviations():
    # Wear adds energy; "unusually quiet" must NOT score as anomalous in directed mode.
    rng = np.random.default_rng(5)
    det = PerUnitBaselineDetector(NAMES, method="directed").fit(_dicts(_healthy(rng, 1000)))
    low = det.raw_scores(_dicts(rng.normal(loc=[-6, -6, -6], scale=1, size=(200, 3))))
    high = det.raw_scores(_dicts(rng.normal(loc=[6, 6, 6], scale=1, size=(200, 3))))
    assert high.mean() > 5 * low.mean()  # one-sided: only the high side fires


def test_zero_variance_feature_does_not_break_fit():
    rng = np.random.default_rng(4)
    X = _healthy(rng, 300)
    X[:, 2] = 7.0  # constant column
    det = PerUnitBaselineDetector(NAMES).fit(_dicts(X))
    assert np.all(np.isfinite(det.raw_scores(_dicts(X[:10]))))
