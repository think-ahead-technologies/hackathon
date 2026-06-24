# ABOUTME: Integration test for the dataset builder against the real recordings.
# ABOUTME: Locks spectrogram shape/dtype, healthy/fault presence, and per-unit grouping.
import os

import numpy as np
import pytest

from wear_detector.export import dataset, spectro

# The recordings are large and gitignored, so they're absent in CI — skip there, run locally.
pytestmark = pytest.mark.skipif(
    not os.path.isdir(dataset.DATA),
    reason=f"recordings not present at {dataset.DATA} (large, gitignored)")


def test_build_shapes_and_presence():
    d = dataset.build(seed=0)
    for key in ("X_train", "X_healthy_test", "X_fault"):
        x = d[key]
        assert x.dtype == np.float32
        assert x.ndim == 3 and x.shape[1:] == (spectro.N_FRAMES, spectro.N_BANDS)
    assert len(d["X_train"]) > 0, "no healthy training windows"
    assert len(d["X_fault"]) > 0, "no fault windows"
    assert len(d["units_train"]) == len(d["X_train"])


def test_train_units_are_healthy_sessions():
    d = dataset.build(seed=0)
    assert set(d["units_train"]).issubset(set(dataset.HEALTHY))


def test_split_is_deterministic():
    a = dataset.build(seed=0)["X_train"]
    b = dataset.build(seed=0)["X_train"]
    np.testing.assert_array_equal(a, b)
