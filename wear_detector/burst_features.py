# ABOUTME: Per-segment spectral extractor for high-rate IMU delivered in contiguous capture segments.
# ABOUTME: Welch within each 1600 Hz segment (no FFT across a segment boundary), then average across.
import numpy as np

from wear_detector.features import N_BANDS, spectral_shape

# Why: the device samples at a true 1600 Hz (its clock t_dev steps a uniform 625 us) but only
# during a handful of contiguous capture segments — t_dev resets between them, and the host
# t_rel just drains the buffer slowly. So the signal is contiguous *within* a segment but has
# real time gaps *between* segments. Running one FFT over the glued stream mixes signal across
# those gaps; instead Welch each contiguous segment (overlapping windows) and average the PSDs.

_DEFAULT_NFFT = 256   # at 1600 Hz: 6.25 Hz bins to the 800 Hz Nyquist
_MIN_SEG = 8          # samples; shorter fragments can't carry a usable spectrum


def split_bursts(t_dev_us, min_len=_MIN_SEG):
    """Index slices [(start, end), ...] of contiguous capture segments.

    A boundary is where the device clock resets or steps back (t_dev_us[i] <= [i-1]);
    fragments shorter than min_len (recording edges) are dropped. Within a returned slice
    the samples are uniformly 1/fs apart, so an FFT over them is gap-free.
    """
    t = np.asarray(t_dev_us)
    if len(t) == 0:
        return []
    starts = [0] + [i for i in range(1, len(t)) if t[i] <= t[i - 1]]
    bounds = starts + [len(t)]
    out = []
    for k in range(len(starts)):
        s, e = bounds[k], bounds[k + 1]
        if e - s >= min_len:
            out.append((s, e))
    return out


def welch_psd(sig, bursts, fs, n_fft=_DEFAULT_NFFT, overlap=0.5):
    """Welch PSD over contiguous segments -> (freqs, psd) on the rfft grid.

    Within each segment, average overlapping Hann-windowed n_fft FFTs (real spectral
    resolution, no boundary crossing); a segment shorter than n_fft is mean-removed,
    Hann-windowed and zero-padded once. PSDs are averaged across all windows of all segments.
    """
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / fs)
    acc = np.zeros(len(freqs), dtype=np.float64)
    windows = 0
    hop = max(1, int(n_fft * (1.0 - overlap)))
    for s, e in bursts:
        seg = np.asarray(sig[s:e], dtype=np.float64)
        if len(seg) < _MIN_SEG:
            continue
        seg = seg - seg.mean()
        if len(seg) >= n_fft:
            win = np.hanning(n_fft)
            for start in range(0, len(seg) - n_fft + 1, hop):
                acc += np.abs(np.fft.rfft(seg[start:start + n_fft] * win)) ** 2
                windows += 1
        else:
            padded = np.zeros(n_fft)
            padded[:len(seg)] = seg * np.hanning(len(seg))
            acc += np.abs(np.fft.rfft(padded)) ** 2
            windows += 1
    if windows:
        acc /= windows
    return freqs, acc


def _dynamic_magnitude(accel):
    mag = np.sqrt(np.sum(accel * accel, axis=1))
    return mag - mag.mean()


def burst_spectral_features(accel, t_dev_us, fs, n_fft=_DEFAULT_NFFT, bursts=None):
    """Burst-aware spectral feature dict (same keys as features.spectral_shape).

    Drop-in for the smeared contiguous spectral features on bursty high-rate data: builds
    the PSD from per-burst FFTs (no gap smearing), then the shared spectral_shape() math.
    Falls back to an all-zero spectrum's features if no usable burst is present.
    """
    sig = _dynamic_magnitude(np.asarray(accel, dtype=np.float64))
    if bursts is None:
        bursts = split_bursts(t_dev_us)
    freqs, psd = welch_psd(sig, bursts, fs, n_fft)
    return spectral_shape(psd, freqs, fs)


# Feature-name list (a_-prefixed) the burst spectral path contributes, for callers that
# want to assemble a detector vector from burst features.
def feature_names():
    dummy = np.zeros((512, 3))
    dummy[:, 2] = 1.0
    keys = burst_spectral_features(dummy, np.arange(512) * 625.0, 1600.0).keys()
    return sorted(f"a_{k}" for k in keys)


def main(csv_path):
    """Print the contiguous-segment breakdown and per-segment spectral summary for a CSV."""
    from wear_detector.io_imu import load_merged_csv_bursts
    _t_wall, accel, _gyro, fs, t_dev = load_merged_csv_bursts(csv_path)
    bursts = split_bursts(t_dev)
    dev_s = sum(e - s for s, e in bursts) / fs
    freqs, _ = welch_psd(np.sqrt((accel * accel).sum(1)), bursts, fs)
    print(f"csv      : {csv_path}")
    print(f"segments : {len(bursts)} contiguous 1600 Hz captures, {dev_s:.1f}s of device time")
    print(f"PSD      : {len(freqs)} bins, df={freqs[1]:.1f} Hz, reaches {freqs[-1]:.0f} Hz\n")
    print(f"{'seg':>3} {'n':>6} {'dur_s':>6} {'centroid':>9} {'hf_ratio':>8} {'peak_band':>9}")
    for i, (s, e) in enumerate(bursts):
        f = burst_spectral_features(accel[s:e], t_dev[s:e], fs)
        bands = [f[f"band{b}"] for b in range(N_BANDS)]
        print(f"{i:>3} {e-s:>6} {(e-s)/fs:>6.1f} {f['centroid']:>9.1f} "
              f"{f['hf_ratio']:>8.3f} {int(np.argmax(bands)):>9}")


if __name__ == "__main__":
    import sys
    main(sys.argv[1])
