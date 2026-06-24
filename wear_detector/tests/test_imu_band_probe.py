# ABOUTME: Tests for the IMU high-band probe — the pure scoring helpers (AUC, high-band fraction).
# ABOUTME: The full probe() needs the recording; these cover the math it rests on.
import numpy as np

from wear_detector import imu_band_probe as p


def test_high_band_fraction_counts_energy_above_cutoff():
    freqs = np.array([0.0, 100.0, 400.0, 700.0])
    psd = np.array([3.0, 1.0, 2.0, 2.0])      # 4 above 400 Hz out of 8 total
    assert abs(p.high_band_fraction(psd, freqs, cutoff_hz=400.0) - 0.5) < 1e-9
    assert abs(p.high_band_fraction(psd, freqs, cutoff_hz=1000.0) - 0.0) < 1e-9


def test_auc_perfect_separation():
    assert p.auc([5, 6, 7], [1, 2, 3]) == 1.0      # all positives above all negatives


def test_auc_reversed_is_zero():
    assert p.auc([1, 2], [5, 6, 7]) == 0.0


def test_auc_ties_count_half():
    assert abs(p.auc([2.0], [1.0, 2.0, 3.0]) - 0.5) < 1e-9  # below 3, equal 2, above 1


def test_auc_empty_is_nan():
    assert np.isnan(p.auc([], [1, 2, 3]))
