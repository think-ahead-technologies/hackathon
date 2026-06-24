# ABOUTME: Probe whether the IMU "hears" the acoustic faults — high-band energy at events vs baseline.
# ABOUTME: Answers "is wear inaudible to the IMU?" empirically: it is not, in the >400 Hz band at 1600 Hz.
import numpy as np

from wear_detector import burst_features as bf
from wear_detector import frames
from wear_detector.io_imu import load_merged_csv_bursts

# The fault signature sits in the mid/high IMU bands (200-700 Hz on test2). Below this cutoff
# the carrier's bulk motion dominates; above it is where the contact/wear transient shows up —
# and which only exists once fs is high enough (100 Hz has no >400 Hz band at all).
HI_CUTOFF_HZ = 400.0


def high_band_fraction(psd, freqs, cutoff_hz=HI_CUTOFF_HZ):
    """Fraction of spectral energy at or above cutoff_hz (a shape feature, level-invariant)."""
    psd = np.asarray(psd, dtype=np.float64)
    freqs = np.asarray(freqs, dtype=np.float64)
    total = float(psd.sum()) + 1e-12
    return float(psd[freqs >= cutoff_hz].sum() / total)


def auc(positives, negatives):
    """Rank AUC = P(random positive > random negative), ties counted as 0.5."""
    pos = np.asarray(positives, dtype=np.float64)
    neg = np.asarray(negatives, dtype=np.float64)
    if pos.size == 0 or neg.size == 0:
        return float("nan")
    return float(np.mean([(neg < p).mean() + 0.5 * (neg == p).mean() for p in pos]))


def _window(t_wall, acc, t_dev, gyro, T, win_s):
    m = (t_wall >= T - win_s) & (t_wall < T + win_s)
    if int(m.sum()) < 32:
        return None
    return acc[m], t_dev[m], gyro[m]


def _hi_frac(acc_w, t_dev_w, fs, cutoff_hz):
    sig = np.sqrt((acc_w * acc_w).sum(axis=1))
    freqs, psd = bf.welch_psd(sig, bf.split_bursts(t_dev_w), fs, n_fft=128)
    return high_band_fraction(psd, freqs, cutoff_hz)


def probe(csv_path, wav_path, win_s=0.75, cutoff_hz=HI_CUTOFF_HZ,
          motion_min_dps=1.0, guard_s=3.0):
    """Compare the IMU high-band fraction at motion-gated acoustic events vs in-motion baseline.

    Baseline windows are restricted to in-motion (gyro RMS > motion_min_dps) and kept guard_s
    away from any event, so the contrast is fault-vs-normal-operation, not moving-vs-parked.
    Returns the event/baseline high-band fractions, event percentiles, and the rank AUC.
    """
    t_wall, acc, gyro, fs, t_dev = load_merged_csv_bursts(csv_path)
    events = [t for t, _ in frames.anomaly_event_times(wav_path, gate_csv=csv_path)]

    def in_motion(T):
        m = (t_wall >= T - win_s) & (t_wall < T + win_s)
        return int(m.sum()) >= 32 and np.sqrt((gyro[m] ** 2).sum(axis=1).mean()) > motion_min_dps

    base = []
    for T in np.arange(t_wall[0] + 1.0, t_wall[-1] - 1.0, 1.0):
        if events and min(abs(T - e) for e in events) < guard_s:
            continue
        if not in_motion(T):
            continue
        w = _window(t_wall, acc, t_dev, gyro, T, win_s)
        if w:
            base.append(_hi_frac(w[0], w[1], fs, cutoff_hz))
    ev = []
    for T in events:
        w = _window(t_wall, acc, t_dev, gyro, T, win_s)
        if w:
            ev.append(_hi_frac(w[0], w[1], fs, cutoff_hz))

    base = np.asarray(base)
    ev = np.asarray(ev)
    return {
        "fs": fs,
        "cutoff_hz": cutoff_hz,
        "n_events": len(ev),
        "n_baseline": len(base),
        "event_hi": ev,
        "baseline_median": float(np.median(base)) if len(base) else float("nan"),
        "event_percentiles": [round(100 * float((base < e).mean())) for e in ev],
        "auc": auc(ev, base),
    }


def main(csv_path, wav_path):
    r = probe(csv_path, wav_path)
    print(f"IMU fs={r['fs']:.0f} Hz   >{r['cutoff_hz']:.0f} Hz energy fraction")
    print(f"events={r['n_events']}  baseline(in-motion)={r['n_baseline']}")
    print(f"baseline median = {r['baseline_median']:.4f}")
    print(f"event values    = {np.round(r['event_hi'], 4).tolist()}")
    print(f"event percentile= {r['event_percentiles']}")
    print(f"IMU-only AUC (event vs baseline) = {r['auc']:.2f}")
    if r["auc"] >= 0.7:
        print("-> the IMU DOES carry the fault, faintly, in the high band (not inaudible).")


if __name__ == "__main__":
    import sys
    main(sys.argv[1], sys.argv[2])
