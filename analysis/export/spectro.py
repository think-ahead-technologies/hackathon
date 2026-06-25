# ABOUTME: Device-contract spectrogram front-end: accel+gyro window -> 2-channel log filterbank [49,40,2].
# ABOUTME: Gyro channel disambiguates faults (vibration on a straight) from turns; firmware FFT must mirror this.
import numpy as np

# Device input contract is [1, N_FRAMES, N_BANDS, N_CHANNELS]; these are fixed by the model.
N_FRAMES = 49
N_BANDS = 40
N_CHANNELS = 2  # [accel, gyro]: gyro lets the model tell faults apart from turns (analysis README)
CHANNELS = ("accel", "gyro")

# Inference window in seconds. Long enough that 49 frames carry real content even at
# 50 Hz (coarse there, rich at 3200 Hz) — same "scales with fs, no code change" stance
# as the rest of the detector. The firmware buffers this many seconds before inference.
WINDOW_S = 4.0

# STFT frame length as a fraction of a second, clamped. At 3200 Hz this lands a real
# FFT (n_fft 64); at 50 Hz it floors to the minimum so the path still produces [49,40].
_FRAME_S = 0.02
_NFFT_MIN = 16
_NFFT_MAX = 256

# log1p(power / scale_eps): a fixed dB-like gain. Band power is tiny so raw log1p collapses
# into int8's first quantum and the model sees noise. Dividing by a fixed floor spreads the
# energies across the int8 range while staying *monotonic in power* — so the broadband-energy
# fault signal is preserved, not normalized away. Constant, so the device computes the identical
# transform. Per channel: accel dynamic power runs ~1e-3 g²; gyro runs ~835× higher (dps², turns
# dominate), so a separate gyro floor keeps both channels in the same log range — without it the
# single int8 input scale would squash the small accel channel. Floors are median-matched on the
# training recordings (accel median 5.5e-4, gyro median 0.46 -> log1p maps both to ~0.44).
SCALE_EPS_ACC = 1e-3
SCALE_EPS_GYRO = 0.85
SCALE_EPS = (SCALE_EPS_ACC, SCALE_EPS_GYRO)  # per-channel, indexed by CHANNELS order


def _next_pow2(x):
    return 1 << max(0, int(np.ceil(np.log2(max(1, x)))))


def _n_fft(fs):
    return int(np.clip(_next_pow2(fs * _FRAME_S), _NFFT_MIN, _NFFT_MAX))


def _hop(fs, n_samples, n_fft):
    """Hop that maps the given window onto ~N_FRAMES frames (cropped/padded to exact)."""
    return max(1, (n_samples - n_fft) // (N_FRAMES - 1))


def feature_config(fs):
    """Reproducible front-end geometry for this fs — baked into the model manifest so
    the on-device FFT extractor matches the trained input exactly."""
    n = max(int(round(WINDOW_S * fs)), _NFFT_MIN + (N_FRAMES - 1))
    n_fft = _n_fft(fs)
    return {
        "window_s": WINDOW_S,
        "fs": float(fs),
        "n_fft": n_fft,
        "hop": _hop(fs, n, n_fft),
        "n_frames": N_FRAMES,
        "n_bands": N_BANDS,
        "n_channels": N_CHANNELS,
        "channels": list(CHANNELS),
        "fb": "linear-tri",
        "log": "log1p(power/scale_eps)",
        "scale_eps": {"accel": SCALE_EPS_ACC, "gyro": SCALE_EPS_GYRO},
    }


def _dynamic_magnitude(accel):
    """Vector magnitude with per-window mean removed (drops gravity/orientation)."""
    mag = np.sqrt(np.sum(accel * accel, axis=1))
    return mag - mag.mean()


def _tri_filterbank(n_freq, nyq, n_bands):
    """Triangular filterbank, linearly spaced over [0, nyq] — matches the linear FFT
    bands the detector already uses; broadband bearing energy is not mel-shaped."""
    freqs = np.linspace(0.0, nyq, n_freq)
    edges = np.linspace(0.0, nyq, n_bands + 2)
    fb = np.zeros((n_bands, n_freq), dtype=np.float64)
    for b in range(n_bands):
        lo, ctr, hi = edges[b], edges[b + 1], edges[b + 2]
        left = (freqs - lo) / (ctr - lo + 1e-12)
        right = (hi - freqs) / (hi - ctr + 1e-12)
        fb[b] = np.clip(np.minimum(left, right), 0.0, None)
    return fb


def _channel_spectrogram(channel, fs, scale_eps):
    """3-axis sensor window -> log filterbank energies, shape (N_FRAMES, N_BANDS) float64.

    Deterministic and dependency-light (numpy only) so the same logic is portable to
    the firmware FFT path. fs-aware: n_fft and the band layout scale with the sample rate.
    """
    channel = np.asarray(channel, dtype=np.float64)
    n = channel.shape[0]
    n_fft = _n_fft(fs)
    if n < n_fft:
        raise ValueError(f"window too short: {n} samples < n_fft {n_fft}")
    sig = _dynamic_magnitude(channel)
    hop = _hop(fs, n, n_fft)
    win = np.hanning(n_fft)
    nyq = fs / 2.0
    n_freq = n_fft // 2 + 1
    fb = _tri_filterbank(n_freq, nyq, N_BANDS)

    frames = []
    start = 0
    while start + n_fft <= n and len(frames) < N_FRAMES:
        seg = sig[start:start + n_fft] * win
        power = np.abs(np.fft.rfft(seg)) ** 2
        frames.append(fb @ power)
        start += hop
    if not frames:  # window shorter than one hop past n_fft — single frame
        seg = sig[:n_fft] * win
        frames.append(fb @ (np.abs(np.fft.rfft(seg)) ** 2))

    band = np.asarray(frames)
    # Pad/crop to exactly N_FRAMES so the output shape is the contract regardless of fs.
    if band.shape[0] < N_FRAMES:
        pad = np.repeat(band[-1:], N_FRAMES - band.shape[0], axis=0)
        band = np.concatenate([band, pad], axis=0)
    else:
        band = band[:N_FRAMES]
    return np.log1p(band / scale_eps)  # dB-like; monotonic in power, finite at zero


def imu_to_spectrogram(accel, gyro, fs):
    """accel + gyro windows -> 2-channel log filterbank, shape (N_FRAMES, N_BANDS, 2) float32.

    Channel 0 is accel (the vibration/fault energy), channel 1 is gyro (turn energy) — the
    model uses the gyro channel to tell a real fault (vibration on a straight) apart from a
    turn (vibration while turning), per the analysis README. Each channel gets its own
    log floor so the units (g vs dps) share the int8 input range.
    """
    acc = _channel_spectrogram(accel, fs, SCALE_EPS_ACC)
    gyr = _channel_spectrogram(gyro, fs, SCALE_EPS_GYRO)
    return np.stack([acc, gyr], axis=-1).astype(np.float32)
