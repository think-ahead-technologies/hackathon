# ABOUTME: Tests for the device-contract spectrogram front-end (IMU -> [49,40]).
# ABOUTME: Locks shape, determinism, fs-scaling, and frequency localization.
import numpy as np
import pytest

from wear_detector.export import spectro


def _accel_sine(freq_hz, fs, dur_s, amp=1.0):
    """3-axis accel whose dynamic magnitude is a clean tone at freq_hz.

    A DC-dominant x axis (10 g) plus a small sine keeps the magnitude in the linear
    regime (|10+sin| ~= 10+sin), so the fundamental is preserved — a sine *on top of an
    orthogonal gravity* would rectify to 2*freq and alias.
    """
    n = int(round(fs * dur_s))
    t = np.arange(n) / fs
    a = np.zeros((n, 3))
    a[:, 0] = 10.0 + amp * np.sin(2 * np.pi * freq_hz * t)
    return a


def test_output_shape_is_contract_at_50hz():
    a = _accel_sine(5.0, fs=50.0, dur_s=spectro.WINDOW_S)
    s = spectro.accel_to_spectrogram(a, fs=50.0)
    assert s.shape == (spectro.N_FRAMES, spectro.N_BANDS) == (49, 40)
    assert s.dtype == np.float32


def test_output_shape_is_contract_at_3200hz():
    a = _accel_sine(800.0, fs=3200.0, dur_s=spectro.WINDOW_S)
    s = spectro.accel_to_spectrogram(a, fs=3200.0)
    assert s.shape == (49, 40)


def test_deterministic():
    a = _accel_sine(7.0, fs=50.0, dur_s=spectro.WINDOW_S)
    s1 = spectro.accel_to_spectrogram(a, fs=50.0)
    s2 = spectro.accel_to_spectrogram(a, fs=50.0)
    np.testing.assert_array_equal(s1, s2)


def test_finite_on_silence():
    a = np.zeros((int(50 * spectro.WINDOW_S), 3))
    a[:, 2] = 9.81  # pure gravity, no dynamics
    s = spectro.accel_to_spectrogram(a, fs=50.0)
    assert np.all(np.isfinite(s))


def test_frequency_localizes_to_upper_band_at_high_fs():
    # A tone near 0.8*Nyquist should put its peak in the upper half of the bands.
    fs = 3200.0
    a = _accel_sine(0.8 * fs / 2.0, fs=fs, dur_s=spectro.WINDOW_S)
    s = spectro.accel_to_spectrogram(a, fs=fs)
    peak_band = int(np.argmax(s.mean(axis=0)))
    assert peak_band > spectro.N_BANDS // 2


def test_feature_config_scales_nfft_with_fs():
    lo = spectro.feature_config(50.0)
    hi = spectro.feature_config(3200.0)
    for cfg in (lo, hi):
        assert cfg["n_frames"] == 49 and cfg["n_bands"] == 40
        assert cfg["fb"] == "linear-tri"
    assert hi["n_fft"] > lo["n_fft"]


def test_rejects_too_short_window():
    with pytest.raises(ValueError):
        spectro.accel_to_spectrogram(np.zeros((4, 3)), fs=50.0)
