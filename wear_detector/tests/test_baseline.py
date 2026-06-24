# ABOUTME: Tests the per-unit baseline math (centroid, threshold, distances, dequant).
# ABOUTME: Pure numpy, TF-free — the int8 embedding path is exercised by the e2e eval.
import numpy as np

from wear_detector.export import baseline


def test_centroid_is_mean():
    emb = np.array([[0.0, 0.0], [2.0, 0.0], [1.0, 3.0]])
    c, _ = baseline.centroid_threshold(emb, fpr=0.05)
    np.testing.assert_allclose(c, emb.mean(axis=0))


def test_threshold_gives_requested_fpr():
    rng = np.random.default_rng(0)
    emb = rng.normal(size=(10000, 4))
    c, thr = baseline.centroid_threshold(emb, fpr=0.05)
    fpr = (baseline.distances(emb, c) > thr).mean()
    assert abs(fpr - 0.05) < 0.01


def test_distances_known_values():
    d = baseline.distances([[3.0, 4.0], [0.0, 0.0]], [0.0, 0.0])
    np.testing.assert_allclose(d, [5.0, 0.0])


def test_dequant_affine():
    q = np.array([-26, 100], dtype=np.int8)
    np.testing.assert_allclose(baseline.dequant(q, scale=0.5, zero_point=-26),
                               [0.0, 63.0])
