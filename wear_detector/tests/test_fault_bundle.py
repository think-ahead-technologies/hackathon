# ABOUTME: Test the fault-bundle audio clip slicer — correct duration and preserved WAV format.
# ABOUTME: The full build() needs a recording; this covers the one piece with real logic.
import wave

import numpy as np

from wear_detector import fault_bundle


def _write_wav(path, secs=10.0, fs=16000, channels=2):
    n = int(secs * fs)
    x = (0.1 * np.sin(2 * np.pi * 440 * np.arange(n) / fs) * 32767).astype("<i2")
    if channels > 1:
        x = np.repeat(x[:, None], channels, axis=1).reshape(-1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(x.tobytes())


def test_write_clip_duration_and_format(tmp_path):
    src = tmp_path / "src.wav"
    _write_wav(src, secs=10.0, fs=16000, channels=2)
    out = tmp_path / "clip.wav"
    secs = fault_bundle.write_clip(src, out, 4.0, 7.0)
    assert abs(secs - 3.0) < 1e-3
    with wave.open(str(out), "rb") as w:
        assert w.getframerate() == 16000
        assert w.getnchannels() == 2
        assert abs(w.getnframes() / 16000 - 3.0) < 1e-3


def test_write_clip_clamps_to_bounds(tmp_path):
    src = tmp_path / "src.wav"
    _write_wav(src, secs=5.0)
    out = tmp_path / "clip.wav"
    # request past the end -> clamped, never negative or out of range
    secs = fault_bundle.write_clip(src, out, 4.0, 9.0)
    assert 0.0 < secs <= 1.0 + 1e-3
