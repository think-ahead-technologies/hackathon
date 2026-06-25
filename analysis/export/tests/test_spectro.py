# ABOUTME: Locks the spectrogram front-end to the device input contract [49,40], fs-aware.
# ABOUTME: Pure numpy — runs in any venv; mirrors the firmware FFT geometry.
import numpy as np

import spectro


def _rand_imu(fs, seed):
    rng = np.random.default_rng(seed)
    n = int(spectro.WINDOW_S * fs)
    return rng.standard_normal((n, 3)), rng.standard_normal((n, 3))


def test_output_shape_is_device_contract():
    accel, gyro = _rand_imu(50.0, 0)
    spec = spectro.imu_to_spectrogram(accel, gyro, 50.0)
    assert spec.shape == (spectro.N_FRAMES, spectro.N_BANDS, spectro.N_CHANNELS)
    assert spec.dtype == np.float32


def test_shape_holds_across_sample_rates():
    for fs in (50.0, 100.0, 1600.0):
        accel, gyro = _rand_imu(fs, 1)
        assert spectro.imu_to_spectrogram(accel, gyro, fs).shape == \
            (spectro.N_FRAMES, spectro.N_BANDS, spectro.N_CHANNELS)


def test_feature_config_is_serializable_and_complete():
    cfg = spectro.feature_config(50.0)
    for k in ("window_s", "fs", "n_fft", "hop", "n_frames", "n_bands", "n_channels", "scale_eps"):
        assert k in cfg
    assert cfg["n_frames"] == spectro.N_FRAMES and cfg["n_bands"] == spectro.N_BANDS
    assert cfg["n_channels"] == spectro.N_CHANNELS
    assert set(cfg["scale_eps"]) == {"accel", "gyro"}


def test_monotonic_in_power():
    # Louder vibration -> not-smaller filterbank energy (log1p is monotonic in power).
    accel, gyro = _rand_imu(50.0, 2)
    quiet = spectro.imu_to_spectrogram(accel, gyro, 50.0)
    loud = spectro.imu_to_spectrogram(accel * 5.0, gyro, 50.0)
    assert loud[..., 0].sum() >= quiet[..., 0].sum()  # accel channel


def test_gyro_channel_is_independent_of_accel():
    # Channel 1 (gyro) must respond to gyro, not accel — that's the turn-disambiguation signal.
    accel, gyro = _rand_imu(50.0, 3)
    base = spectro.imu_to_spectrogram(accel, gyro, 50.0)
    louder_gyro = spectro.imu_to_spectrogram(accel, gyro * 5.0, 50.0)
    assert louder_gyro[..., 1].sum() >= base[..., 1].sum()
    assert np.allclose(louder_gyro[..., 0], base[..., 0])  # accel channel unchanged
