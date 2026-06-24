# ABOUTME: Tests for the model trainer's ML core — logistic regression, CV, standardization.
# ABOUTME: Synthetic separable data; the feature extraction (audio/IMU) needs recordings, not here.
import numpy as np

from wear_detector import train


def _separable(n=80, seed=0):
    rng = np.random.default_rng(seed)
    X = np.vstack([rng.normal(-2, 1, (n, 3)), rng.normal(2, 1, (n, 3))])
    y = np.array([0] * n + [1] * n)
    return X, y


def test_logreg_learns_separable_data():
    X, y = _separable()
    w, m, s = train.fit_logreg(X, y)
    p = train.predict_proba(X, w, m, s)
    acc = np.mean((p > 0.5) == y)
    assert acc > 0.95


def test_predict_proba_in_unit_interval():
    X, y = _separable()
    w, m, s = train.fit_logreg(X, y)
    p = train.predict_proba(X, w, m, s)
    assert p.min() >= 0.0 and p.max() <= 1.0


def test_cross_val_auc_high_on_separable_two_groups():
    X, y = _separable()
    # two recordings, each with both classes -> leave-one-recording-out should still separate
    groups = np.array(([0, 1] * (len(y) // 2)))
    assert train.cross_val_auc(X, y, groups) > 0.9


def test_cross_val_auc_chance_on_noise():
    rng = np.random.default_rng(1)
    X = rng.standard_normal((60, 3))
    y = np.array([0, 1] * 30)
    groups = np.zeros(60, int)            # single group -> leave-one-out
    a = train.cross_val_auc(X, y, groups)
    assert 0.3 < a < 0.7                  # no real signal -> near 0.5
