# ABOUTME: Tests the RF labeler's event-level span logic — the persistence filter that turns the
# ABOUTME: noisy per-window screen into clean fault events. Pure (no sklearn/data needed).
import numpy as np

import rf_labeler


def _spans(flags, min_run, hop=0.5):
    centers = np.arange(len(flags)) * hop
    return rf_labeler._windows_to_spans(centers, np.array(flags, bool), hop, min_run)


def test_isolated_flags_are_dropped_as_false_alarms():
    # Single-window flags (the detector's ~20% FPR) must not become fault events.
    flags = [0, 1, 0, 0, 1, 0, 1, 0]
    assert _spans(flags, min_run=3) == []


def test_sustained_run_becomes_one_event():
    flags = [0, 1, 1, 1, 1, 0, 0]
    spans = _spans(flags, min_run=3)
    assert len(spans) == 1
    start, end = spans[0]
    assert start < end


def test_min_run_threshold_is_enforced():
    flags = [1, 1, 0, 0, 0, 0]  # run of 2
    assert _spans(flags, min_run=3) == []
    assert len(_spans(flags, min_run=2)) == 1


def test_single_window_gaps_are_bridged():
    # A 1-window dropout inside a real event shouldn't split it (matches the merge rule).
    flags = [1, 1, 0, 1, 1]
    spans = _spans(flags, min_run=3)
    assert len(spans) == 1
