# ABOUTME: Unit tests for sample-rate-aware feature extraction.
# ABOUTME: Verifies determinism, fs-scaling of spectral bands, and gyro exclusion.
import numpy as np
import pytest

from wear_detector import features


def _signal(fs, n, freq, amp=1.0):
    t = np.arange(n) / fs
    a = np.zeros((n, 3))
    a[:, 2] = 9.81 + amp * np.sin(2 * np.pi * freq * t)  # tone on Z + gravity
    g = np.zeros((n, 3))
    return a, g


def test_feature_names_stable_and_no_gyro_by_default():
    names = features.feature_names()
    assert "g_rms" not in names
    assert "a_rms" in names and "a_kurtosis" in names
    assert names == sorted(names)


def test_gravity_removed_dynamic_rms_small_for_static():
    a = np.zeros((50, 3)); a[:, 2] = 9.81
    f = features.extract(a, np.zeros((50, 3)), fs=50.0)
    assert f["a_rms"] < 1e-6  # constant gravity -> ~zero dynamic energy


def test_high_tone_lands_in_high_band_at_high_fs():
    # A 1200 Hz tone at 3200 Hz fs must put energy in an upper band, not band0.
    a, g = _signal(3200, 3200, freq=1200.0, amp=1.0)
    f = features.extract(a, g, fs=3200.0)
    assert f["a_hf_ratio"] > 0.5
    assert f["a_band0"] < f["a_band6"]


def test_low_tone_lands_in_low_band():
    a, g = _signal(3200, 3200, freq=5.0, amp=1.0)
    f = features.extract(a, g, fs=3200.0)
    assert f["a_band0"] > f["a_hf_ratio"]


def test_include_gyro_adds_turn_feature():
    a = np.zeros((50, 3)); a[:, 2] = 9.81
    g = np.zeros((50, 3)); g[:, 0] = np.linspace(0, 100, 50)
    f = features.extract(a, g, fs=50.0, include_gyro=True)
    assert "g_rms" in f and f["g_rms"] > 0


def test_low_fs_uses_energy_profile_only():
    names = features.detector_feature_names(fs=50.0)
    assert "a_jerk_mad" in names
    assert not any(n.startswith("a_band") for n in names)  # spectral bands excluded
    assert "a_centroid" not in names


def test_high_fs_unlocks_full_spectral_profile():
    low = set(features.detector_feature_names(fs=50.0))
    high = set(features.detector_feature_names(fs=3200.0))
    assert low < high  # strict superset at high fs
    assert any(n.startswith("a_band") for n in high)
    assert "a_centroid" in high


def test_envelope_guard_on_short_window():
    a = np.random.default_rng(0).normal(size=(8, 3)); a[:, 2] += 9.81
    f = features.extract(a, np.zeros((8, 3)), fs=50.0)
    assert f["a_env_rms"] == 0.0  # too short to filter -> guarded zero
