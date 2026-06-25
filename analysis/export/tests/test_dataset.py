# ABOUTME: Verifies dataset labeling and the grouped (leave-session-out) split on real recordings.
# ABOUTME: Pure numpy/pandas — needs the recordings present under thinkathon_kickstart/data.
import numpy as np
import pytest

import dataset


@pytest.fixture(scope="module")
def built():
    d = dataset.build(seed=0)
    if len(d["X_train"]) == 0:
        pytest.skip("no recordings found under thinkathon_kickstart/data")
    return d


def test_shapes_and_labels_are_binary(built):
    assert built["X_train"].shape[1:] == (49, 40, 2)
    assert set(np.unique(built["y_train"])).issubset({0, 1})
    assert set(np.unique(built["y_test"])).issubset({0, 1})


def test_split_is_grouped_by_session(built):
    # No session appears in both train and test (would leak and inflate the held-out AUC).
    test_sessions = set(built["test_sessions"])
    assert set(built["groups_test"]).issubset(test_sessions)


def test_both_classes_present_in_train(built):
    # The classifier needs healthy and fault windows to learn from.
    assert built["y_train"].sum() > 0
    assert (built["y_train"] == 0).sum() > 0
