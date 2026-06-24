# ABOUTME: Sample-rate-aware feature extraction for IMU wear detection.
# ABOUTME: Time, spectral, and envelope features; spectral/envelope bands scale with fs (50 Hz..3200 Hz).
import numpy as np
from scipy.signal import butter, hilbert, sosfiltfilt

# Number of linear FFT sub-bands spanning [0, Nyquist]. Each band's fraction of
# total spectral energy is a feature, so the band layout auto-scales with fs:
# at 50 Hz the top bands are near-empty; at 3200 Hz they carry the bearing/grind signal.
N_BANDS = 8

# Envelope band as a fraction of Nyquist — the high band where bearing-impact
# modulation lives once fs is high enough. Below this, envelope features are weak
# but the code path stays identical.
ENV_BAND = (0.5, 0.95)


def _dynamic_magnitude(xyz):
    """Vector magnitude with the per-window mean removed (drops gravity/orientation)."""
    mag = np.sqrt(np.sum(xyz * xyz, axis=1))
    return mag - mag.mean()


def _time_features(sig):
    n = len(sig)
    rms = float(np.sqrt(np.mean(sig * sig)))
    peak = float(np.max(np.abs(sig)))
    p2p = float(sig.max() - sig.min())
    std = float(sig.std())
    crest = peak / rms if rms > 1e-12 else 0.0
    # mean abs successive difference: high-frequency / jerk proxy
    jerk_mad = float(np.mean(np.abs(np.diff(sig)))) if n > 1 else 0.0
    if std > 1e-12:
        z = (sig - sig.mean()) / std
        kurtosis = float(np.mean(z ** 4))
        skew = float(np.mean(z ** 3))
    else:
        kurtosis, skew = 0.0, 0.0
    zcr = float(np.mean(np.abs(np.diff(np.sign(sig))) > 0)) if n > 1 else 0.0
    return {
        "rms": rms, "p2p": p2p, "std": std, "crest": crest,
        "jerk_mad": jerk_mad, "kurtosis": kurtosis, "skew": skew, "zcr": zcr,
    }


def _spectral_features(sig, fs):
    n = len(sig)
    win = np.hanning(n)
    spec = np.abs(np.fft.rfft(sig * win)) ** 2
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    total = float(spec.sum()) + 1e-12
    nyq = fs / 2.0

    feats = {}
    # band energy fractions across [0, Nyquist]
    edges = np.linspace(0.0, nyq, N_BANDS + 1)
    idx = np.digitize(freqs, edges) - 1
    for b in range(N_BANDS):
        feats[f"band{b}"] = float(spec[idx == b].sum() / total)
    # high-frequency ratio (energy above Nyquist/2)
    feats["hf_ratio"] = float(spec[freqs >= nyq / 2.0].sum() / total)
    # spectral centroid, spread, flatness, entropy, rolloff(85%)
    p = spec / total
    feats["centroid"] = float(np.sum(freqs * p))
    feats["spread"] = float(np.sqrt(np.sum(((freqs - feats["centroid"]) ** 2) * p)))
    geo = np.exp(np.mean(np.log(spec + 1e-12)))
    feats["flatness"] = float(geo / (spec.mean() + 1e-12))
    feats["entropy"] = float(-np.sum(p * np.log(p + 1e-12)))
    cum = np.cumsum(spec) / total
    roll_idx = int(np.searchsorted(cum, 0.85))
    feats["rolloff"] = float(freqs[min(roll_idx, len(freqs) - 1)])
    return feats


def _envelope_features(sig, fs):
    """Hilbert-envelope features of a high band — bearing-impact modulation energy.

    Meaningful only when the high band is well below Nyquist and the window is long
    enough to filter; otherwise returns zeros via the guard (e.g. very short windows).
    """
    n = len(sig)
    nyq = fs / 2.0
    lo, hi = ENV_BAND[0] * nyq, ENV_BAND[1] * nyq
    if n < 16 or lo <= 0 or hi >= nyq:
        return {"env_rms": 0.0, "env_kurtosis": 0.0, "env_p2p": 0.0}
    try:
        sos = butter(2, [lo / nyq, hi / nyq], btype="band", output="sos")
        band = sosfiltfilt(sos, sig)
    except ValueError:
        return {"env_rms": 0.0, "env_kurtosis": 0.0, "env_p2p": 0.0}
    env = np.abs(hilbert(band))
    env = env - env.mean()
    rms = float(np.sqrt(np.mean(env * env)))
    std = env.std()
    kurt = float(np.mean(((env - env.mean()) / std) ** 4)) if std > 1e-12 else 0.0
    return {"env_rms": rms, "env_kurtosis": kurt, "env_p2p": float(env.max() - env.min())}


def extract(accel, gyro, fs, include_gyro=False):
    """Feature dict for one window.

    Detector features are accel-only by default: at any fs, gyro energy is dominated
    by turns (gyro_rms spikes ~30x on curves), so it is excluded from anomaly scoring
    and exposed separately for turn detection / localization.
    """
    a = _dynamic_magnitude(accel)
    feats = {}
    for k, v in _time_features(a).items():
        feats[f"a_{k}"] = v
    feats.update({f"a_{k}": v for k, v in _spectral_features(a, fs).items()})
    feats.update({f"a_{k}": v for k, v in _envelope_features(a, fs).items()})
    # per-axis accel dynamic RMS
    for j, axis in enumerate("xyz"):
        col = accel[:, j] - accel[:, j].mean()
        feats[f"a_rms_{axis}"] = float(np.sqrt(np.mean(col * col)))
    # gyro magnitude RMS — turn signal, NOT a detector feature unless asked
    g = np.sqrt(np.sum(gyro * gyro, axis=1))
    feats["g_rms"] = float(np.sqrt(np.mean((g - g.mean()) ** 2)))
    if not include_gyro:
        feats.pop("g_rms")
    return feats


def feature_names(include_gyro=False):
    """Stable ordered feature names (matches extract())."""
    a = np.zeros((32, 3))
    a[:, 2] = 9.81
    return sorted(extract(a, np.zeros((32, 3)), fs=50.0, include_gyro=include_gyro).keys())


# Below this fs, Nyquist is too low for bearing/grinding spectral content: the band
# and spectral-shape features are noise (measured AUC ~0.55-0.66 at 50 Hz), so the
# detector uses only the broadband-energy features that actually separate (AUC ~0.80).
SPECTRAL_FS_THRESHOLD = 400.0

# Energy/jerk features that separate fault from healthy at any sample rate.
ENERGY_FEATURES = [
    "a_jerk_mad", "a_rms", "a_p2p", "a_std",
    "a_rms_x", "a_rms_y", "a_rms_z",
    "a_env_rms", "a_env_p2p",
]


def detector_feature_names(fs, include_gyro=False):
    """Feature subset the detector should use at this sample rate.

    Low fs -> energy profile only. At fs >= SPECTRAL_FS_THRESHOLD the full
    spectral + envelope set is included, because Nyquist then covers the
    bearing/grinding band where those features become discriminative. This is
    what makes the 3200 Hz upgrade pay off with no code change.
    """
    if fs >= SPECTRAL_FS_THRESHOLD:
        return feature_names(include_gyro=include_gyro)
    return [n for n in ENERGY_FEATURES if not n.startswith("g_")]
