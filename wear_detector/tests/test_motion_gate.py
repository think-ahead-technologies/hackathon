# ABOUTME: Tests for the motion gate — suppress anomalies while the unit isn't moving (parked/handled).
# ABOUTME: Synthetic gyro (parked half vs in-transit half) drives deterministic asserts.
import numpy as np

from wear_detector import motion_gate


def _parked_then_moving(fs=100.0, half_s=10.0):
    """gyro: ~0 dps for the first half (parked/handled), ~20 dps for the second (transit)."""
    n = int(half_s * fs)
    t = np.arange(2 * n) / fs
    gyro = np.zeros((2 * n, 3))
    gyro[n:, 2] = 20.0 + np.sin(np.arange(n) / 3.0)   # rotating in transit
    gyro[:n, 2] = 0.1                                  # essentially still
    return t, gyro


def test_window_gyro_rms_low_when_parked_high_when_moving():
    t, gyro = _parked_then_moving()
    e = motion_gate.window_gyro_rms(t, gyro, np.array([5.0, 15.0]), win_s=1.0)
    assert e[0] < 1.0          # parked
    assert e[1] > 10.0         # transit


def test_window_gyro_rms_nan_when_no_coverage():
    t, gyro = _parked_then_moving()
    e = motion_gate.window_gyro_rms(t, gyro, np.array([999.0]), win_s=1.0)
    assert np.isnan(e[0])


def test_in_motion_splits_parked_from_transit():
    t, gyro = _parked_then_moving()
    centres = np.array([2.0, 5.0, 8.0, 12.0, 15.0, 18.0])
    # Explicit threshold between the parked (~0.1) and transit (~20) modes.
    mask, energies, thr = motion_gate.in_motion(t, gyro, centres, win_s=1.0, threshold=1.0)
    assert list(mask) == [False, False, False, True, True, True]
    assert thr == 1.0


def test_suggest_threshold_lands_between_bimodal_modes():
    # Half dwell windows near 0.3 dps, half transit near 18 dps: the 50th pct interpolates
    # across the gap and lands between the modes.
    energies = np.array([0.3] * 50 + [18.0] * 50)
    thr = motion_gate.suggest_threshold(energies, pct=50.0)
    assert 0.3 < thr < 18.0


def test_in_motion_keep_missing_controls_uncovered_windows():
    t, gyro = _parked_then_moving()
    centres = np.array([15.0, 999.0])           # second has no IMU coverage
    keep, _, _ = motion_gate.in_motion(t, gyro, centres, keep_missing=True)
    drop, _, _ = motion_gate.in_motion(t, gyro, centres, keep_missing=False)
    assert keep[1] == True and drop[1] == False  # only the missing one flips


def test_gate_events_suppresses_parked_events():
    t, gyro = _parked_then_moving()
    reference = np.arange(1.0, 19.0, 0.5)                 # full window distribution
    events = [2.0, 5.0, 12.0, 15.0]                       # two parked, two in transit
    # Explicit threshold between the parked (~0.1) and transit (~20) modes.
    out = motion_gate.gate_events(t, gyro, events, reference, threshold=1.0)
    assert list(out["keep"]) == [False, False, True, True]
    assert out["suppressed"] == 2


def test_gate_events_keeps_missing_coverage_events():
    t, gyro = _parked_then_moving()
    reference = np.arange(1.0, 19.0, 0.5)
    events = [15.0, 999.0]                                # second has no IMU coverage
    keep = motion_gate.gate_events(t, gyro, events, reference, keep_missing=True)["keep"]
    drop = motion_gate.gate_events(t, gyro, events, reference, keep_missing=False)["keep"]
    assert keep[1] == True and drop[1] == False
