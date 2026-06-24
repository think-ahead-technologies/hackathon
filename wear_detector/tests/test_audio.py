# ABOUTME: Tests for the acoustic anomaly path — WAV load, band features, self-baseline detection.
# ABOUTME: Synthetic fixtures only (a written WAV + injected bursts) so they're fast and hermetic.
import wave

import numpy as np

from wear_detector import audio


def _write_wav(path, x, fs=16000, channels=1):
    """Write float samples in [-1, 1] as int16 PCM (duplicated across channels)."""
    pcm = np.clip(x, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    if channels > 1:
        pcm = np.repeat(pcm[:, None], channels, axis=1).reshape(-1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(pcm.tobytes())
    return str(path)


def test_load_wav_mixes_stereo_to_mono(tmp_path):
    fs = 16000
    x = 0.2 * np.sin(2 * np.pi * 440.0 * np.arange(fs) / fs)
    path = _write_wav(tmp_path / "s.wav", x, fs=fs, channels=2)
    y, got_fs = audio.load_wav(path)
    assert got_fs == fs
    assert y.shape == (fs,)                       # stereo collapsed to mono
    assert np.abs(y).max() <= 1.0 + 1e-6          # normalized to full scale
    assert np.abs(y).max() > 0.1                  # signal preserved


def test_band_features_localize_a_tone(tmp_path):
    fs = 16000
    n_bands = 32
    # A 2 kHz tone should put its peak in the band spanning 2 kHz.
    f0 = 2000.0
    frame = 0.5 * np.sin(2 * np.pi * f0 * np.arange(fs) / fs)
    feats = audio.band_features(frame, fs, n_bands=n_bands)
    names = audio.feature_names(n_bands)
    vec = np.array([feats[k] for k in names])
    peak_band = int(np.argmax(vec))
    band_hz = (fs / 2.0) / n_bands
    expected_band = int(f0 // band_hz)
    assert abs(peak_band - expected_band) <= 1


def test_detect_session_flags_injected_track_errors(tmp_path):
    # No healthy reference: a mostly-quiet run with a few loud broadband bursts
    # standing in for the built-in track errors. The self-baseline must rank the
    # burst windows at the top — that is the whole "no healthy data" claim.
    fs = 16000
    rng = np.random.default_rng(0)
    dur_s = 40
    x = 0.02 * rng.standard_normal(dur_s * fs)     # quiet broadband "normal"
    burst_centers_s = [10.0, 20.0, 30.0]
    for c in burst_centers_s:
        a = int((c - 0.25) * fs)
        b = int((c + 0.25) * fs)
        x[a:b] += 0.4 * rng.standard_normal(b - a)  # loud broadband fault
    path = _write_wav(tmp_path / "fault.wav", x, fs=fs, channels=2)

    res = audio.detect_session(path, window_s=0.5, hop_s=0.25, n_bands=32,
                               threshold_pct=90.0)
    assert res["fs"] == fs
    assert len(res["times"]) == len(res["scores"]) == len(res["flags"])

    flagged_times = [t for t, f in zip(res["times"], res["flags"]) if f]
    # Every injected burst must have a flagged window within half a window of it.
    for c in burst_centers_s:
        assert any(abs(t + 0.25 - c) < 0.5 for t in flagged_times), \
            f"missed the track error near {c}s"
    # And the very top-scoring window must sit on a burst, not on quiet baseline.
    top_t = res["times"][int(np.argmax(res["scores"]))]
    assert min(abs(top_t + 0.25 - c) for c in burst_centers_s) < 0.5
