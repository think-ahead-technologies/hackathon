# ABOUTME: Acoustic wear path — load the 16 kHz recording, per-window band energies, self-baseline.
# ABOUTME: No healthy data needed: fit the robust baseline on the session's own (mostly-normal) run.
import wave

import numpy as np

from wear_detector.detector import PerUnitBaselineDetector
from wear_detector.export.spectro import _tri_filterbank

# Audio front-end geometry. 16 kHz gives a 8 kHz Nyquist where bearing/track-wear
# acoustics live — the spectral resolution the 50/100 Hz IMU never had.
N_BANDS = 32
_NFFT = 1024            # ~64 ms sub-frames at 16 kHz; Welch-averaged within a window
_EPS = 1e-9             # log1p(power/eps): dB-like, monotonic in power, finite at zero

_PCM_DTYPE = {1: "<i1", 2: "<i2", 4: "<i4"}


def load_wav(path):
    """Return (x_mono float64 in [-1, 1], fs). Stereo is averaged to mono."""
    with wave.open(str(path), "rb") as w:
        fs = w.getframerate()
        channels = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width not in _PCM_DTYPE:
        raise ValueError(f"unsupported PCM sample width: {width} bytes")
    x = np.frombuffer(raw, dtype=_PCM_DTYPE[width]).astype(np.float64)
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    full_scale = float(np.iinfo(np.dtype(_PCM_DTYPE[width])).max)
    return x / full_scale, fs


def feature_names(n_bands=N_BANDS):
    return [f"band_{i:02d}" for i in range(n_bands)]


def band_features(frame, fs, n_bands=N_BANDS):
    """Welch-averaged linear-tri band energies for one window -> {band_ii: log-energy}.

    Linear (not mel) bands and log1p compression match the IMU front-end's reasoning:
    broadband wear energy is not mel-shaped, and the directed detector wants features
    that rise monotonically with added energy. Returns a dict keyed by feature_names().
    """
    frame = np.asarray(frame, dtype=np.float64)
    n_fft = min(_NFFT, len(frame))
    if n_fft < 8:
        raise ValueError(f"audio window too short: {len(frame)} samples")
    win = np.hanning(n_fft)
    nyq = fs / 2.0
    n_freq = n_fft // 2 + 1
    fb = _tri_filterbank(n_freq, nyq, n_bands)

    hop = n_fft // 2
    powers = []
    start = 0
    while start + n_fft <= len(frame):
        seg = frame[start:start + n_fft] * win
        powers.append(np.abs(np.fft.rfft(seg)) ** 2)
        start += hop
    if not powers:
        seg = np.zeros(n_fft)
        seg[:len(frame)] = frame
        powers.append(np.abs(np.fft.rfft(seg * win)) ** 2)

    mean_power = np.mean(powers, axis=0)
    bands = np.log1p((fb @ mean_power) / _EPS)
    names = feature_names(n_bands)
    return {names[i]: float(bands[i]) for i in range(n_bands)}


def iter_audio_windows(x, fs, window_s=0.5, hop_s=0.25):
    """Yield (t_start_s, frame) sliding windows over a mono signal."""
    n = max(8, int(round(window_s * fs)))
    step = max(1, int(round(hop_s * fs)))
    for i in range(0, len(x) - n + 1, step):
        yield i / fs, x[i:i + n]


def detect_session(path, window_s=0.5, hop_s=0.25, n_bands=N_BANDS,
                   threshold_pct=95.0):
    """Self-baseline acoustic anomaly scan over one recording.

    With no healthy recording available, fit the robust per-unit baseline on this
    session's own windows: the run is mostly nominal, so median/MAD centering is
    dominated by normal operation and the built-in track errors surface as the
    high-energy minority. Returns per-window times, 0..1 scores (session-empirical
    CDF), raw directed scores, and boolean flags at the chosen percentile.
    """
    x, fs = load_wav(path)
    times, feats = [], []
    for t, frame in iter_audio_windows(x, fs, window_s, hop_s):
        times.append(t)
        feats.append(band_features(frame, fs, n_bands))
    if not feats:
        raise ValueError("recording too short for one window")

    det = PerUnitBaselineDetector(feature_names(n_bands), method="directed",
                                  threshold_pct=threshold_pct).fit(feats)
    return {
        "fs": fs,
        "window_s": window_s,
        "hop_s": hop_s,
        "times": times,
        "scores": det.score(feats),
        "raw": det.raw_scores(feats),
        "flags": det.predict(feats),
        "detector": det,
    }
