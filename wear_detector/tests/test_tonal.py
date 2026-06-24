# ABOUTME: Tests for the tonal/squeal detector — a narrowband tone scores high, broadband does not.
# ABOUTME: Synthetic WAVs (noise vs noise+tone) keep it hermetic.
import wave

import numpy as np

from wear_detector import tonal


def _write_wav(path, x, fs=16000):
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    pcm = np.repeat(pcm[:, None], 2, axis=1).reshape(-1)   # stereo, like the recorder
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(fs)
        w.writeframes(pcm.tobytes())
    return str(path)


def test_tonal_score_high_for_tone_low_for_noise():
    fs = 16000
    rng = np.random.default_rng(0)
    noise = 0.1 * rng.standard_normal(fs)
    tone = noise + 0.3 * np.sin(2 * np.pi * 3000.0 * np.arange(fs) / fs)
    assert tonal.tonal_score(tone, fs) > 10 * tonal.tonal_score(noise, fs)
    assert tonal.tonal_score(noise, fs) < tonal.TONAL_FLOOR


def test_peak_freq_finds_the_tone():
    fs = 16000
    x = np.sin(2 * np.pi * 3000.0 * np.arange(fs) / fs)
    assert abs(tonal.peak_freq(x, fs) - 3000.0) < 30.0


def test_detect_tonal_flags_squeal_windows(tmp_path):
    fs = 16000
    rng = np.random.default_rng(1)
    x = 0.05 * rng.standard_normal(20 * fs)            # 20 s quiet broadband
    for c in (5.0, 12.0):                              # two squeals at 2.5 kHz
        a, b = int((c - 0.3) * fs), int((c + 0.3) * fs)
        x[a:b] += 0.4 * np.sin(2 * np.pi * 2500.0 * np.arange(b - a) / fs)
    res = tonal.detect_tonal(_write_wav(tmp_path / "sq.wav", x, fs))
    ts = [e["t"] for e in res["events"]]
    for c in (5.0, 12.0):
        assert any(abs(t - c) < 0.5 for t in ts), f"missed squeal at {c}s"
    assert all(abs(e["pitch_hz"] - 2500.0) < 60.0 for e in res["events"])


def test_detect_tonal_quiet_recording_has_no_false_squeals(tmp_path):
    fs = 16000
    rng = np.random.default_rng(2)
    x = 0.05 * rng.standard_normal(10 * fs)            # broadband only, no tone
    res = tonal.detect_tonal(_write_wav(tmp_path / "q.wav", x, fs))
    assert res["events"] == []
