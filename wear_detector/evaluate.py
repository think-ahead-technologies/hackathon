# ABOUTME: End-to-end evaluation of the per-unit baseline detector on real recordings.
# ABOUTME: Fits on held-out healthy windows, reports AUC + TPR/FPR, turn-trap FPR, severity-ladder scores.
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wear_detector import features
from wear_detector.io_imu import iter_windows

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "thinkathon_kickstart", "data")

HEALTHY = [
    "Session-2026-06-17--10-46-14_normal",
    "Session-2026-06-17--11-25-33_all_normal",
    "Session-2026-06-17--11-26-26_all_normal",
    "Session-2026-06-18--10-27-16-normal-data-without-defective-parts",
]
FAULT = [
    "Session-2026-06-17--12-05-06_faulty_bearing",
    "Session-2026-06-17--12-06-11_faulty_bearing",
    "Session-2026-06-17--12-08-11_faulty_bearing",
    "Session-2026-06-17--12-10-03_faulty_bearing",
    "Session-2026-06-17--13-49-38_faulty_bearing",
    "Session-2026-06-17--18-24-09-faulty-bearing",
]
LADDER = [
    ("2 broken", "Session-2026-06-17--19-25-09-additional-2-broken-bearings"),
    ("4 defective", "Session-2026-06-17--18-53-07-additional-4-defective-idler-wheel-bearings"),
    ("8 cumulated", "Session-2026-06-17--19-11-06-cummulated-8-defective-idler-wheel-bearings"),
]


def collect(sessions, keep):
    """Feature dicts for windows whose label satisfies keep(label)."""
    out = []
    for s in sessions:
        path = os.path.join(DATA, s)
        if not os.path.isdir(path):
            continue
        for label, accel, gyro, fs in iter_windows(path):
            if keep(label):
                out.append(features.extract(accel, gyro, fs))
    return out


def collect_streams(sessions, keep):
    """Per-session, time-ordered feature dicts — for temporal (dwell) evaluation."""
    streams = []
    for s in sessions:
        path = os.path.join(DATA, s)
        if not os.path.isdir(path):
            continue
        seq = [features.extract(a, g, fs) for lab, a, g, fs in iter_windows(path) if keep(lab)]
        if seq:
            streams.append(seq)
    return streams


def rolling_mean(x, w):
    if len(x) < w:
        return np.array([np.mean(x)]) if len(x) else np.array([])
    c = np.cumsum(np.insert(x, 0, 0.0))
    return (c[w:] - c[:-w]) / w


def auc(pos, neg):
    pos, neg = np.asarray(pos), np.asarray(neg)
    order = np.argsort(np.concatenate([pos, neg]))
    ranks = np.empty(len(order)); ranks[order] = np.arange(1, len(order) + 1)
    r_pos = ranks[:len(pos)].sum()
    a = (r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))
    return max(a, 1 - a)


def build_detector(train, fs, method="directed"):
    from wear_detector.detector import PerUnitBaselineDetector
    names = features.detector_feature_names(fs)
    return PerUnitBaselineDetector(names, method=method).fit(train)


def main():
    rng = np.random.default_rng(0)
    healthy = collect(HEALTHY, lambda l: l in ("normal", None))
    rng.shuffle(healthy)
    cut = int(0.7 * len(healthy))
    train, healthy_test = healthy[:cut], healthy[cut:]
    fault = collect(FAULT, lambda l: l == "fault")

    from wear_detector.io_imu import load_imu, infer_fs
    fs = infer_fs(load_imu(os.path.join(DATA, HEALTHY[0]))[0])
    profile = "full spectral+envelope" if fs >= features.SPECTRAL_FS_THRESHOLD else "energy-only"
    print(f"sample rate           : {fs:.0f} Hz  -> {profile} profile "
          f"({len(features.detector_feature_names(fs))} features)")
    print(f"train healthy windows : {len(train)}")
    print(f"test  healthy windows : {len(healthy_test)}")
    print(f"fault windows         : {len(fault)}")

    print(f"\n{'mode':14}{'AUC':>7}{'TPR@99':>9}{'FPR@99':>9}")
    for method in ("mahalanobis", "directed"):
        det = build_detector(train, fs, method=method)
        a = auc(det.raw_scores(fault), det.raw_scores(healthy_test))
        tpr = det.predict(fault).mean()
        fpr = det.predict(healthy_test).mean()
        print(f"{method:14}{a:7.3f}{tpr:9.3f}{fpr:9.3f}")

    det = build_detector(train, fs, method="directed")  # the shipped detector
    turns = collect(HEALTHY, lambda l: l == "turn_table")
    if turns:
        print(f"\nturn-trap FPR (n={len(turns)}) : {det.predict(turns).mean():.3f}  "
              f"(gyro excluded from detector)")

    print("\nseverity ladder (median normalized score, directed):")
    print(f"  {'0 healthy':16} {np.median(det.score(healthy_test)):.3f}")
    for name, sess in LADDER:
        rows = collect([sess], lambda l: l == "fault")
        if rows:
            print(f"  {name:16} {np.median(det.score(rows)):.3f}  (n={len(rows)})")

    # --- temporal (dwell) detection: wear persists, so aggregate the normalized score ---
    W = 10  # ~5 s at 1 s windows, 50% overlap (spec §5 dwell)
    h_streams = collect_streams(HEALTHY, lambda l: l in ("normal", None))
    f_streams = collect_streams(FAULT, lambda l: l == "fault")
    h_roll = np.concatenate([rolling_mean(det.score(s), W) for s in h_streams])
    f_roll = np.concatenate([rolling_mean(det.score(s), W) for s in f_streams])
    thr = float(np.percentile(h_roll, 95.0))  # tune to 5% healthy false-alarm
    print(f"\ndwell detection (rolling mean of normalized score over {W} windows ~5s):")
    print(f"  AUC                 : {auc(f_roll, h_roll):.3f}")
    print(f"  threshold@5%-FPR    : {thr:.3f}")
    print(f"  TPR (fault windows) : {(f_roll >= thr).mean():.3f}")
    print(f"  FPR (healthy)       : {(h_roll >= thr).mean():.3f}")


if __name__ == "__main__":
    main()
