# ABOUTME: Train a multimodal fault classifier from clip-grid labels (audio + IMU feature bank).
# ABOUTME: Pure-numpy logistic regression; leave-one-recording-out CV so it generalizes across runs.
import sys

import numpy as np

from wear_detector import audio, label_eval
from wear_detector.imu_band_probe import auc
from wear_detector.io_imu import load_merged_csv_bursts


def build_dataset(recordings):
    """recordings: [(name, labels_csv, wav, imu_csv), ...] -> (X, y, groups, feature_names).

    Per labeled clip with IMU coverage, concatenate the audio + IMU feature banks. `groups`
    holds the recording index per row, for leave-one-recording-out CV.
    """
    rows, y, groups = [], [], []
    feat_names = None
    for gi, (_name, labels_csv, wav, imu_csv) in enumerate(recordings):
        labels = label_eval.load_labels(labels_csv)
        x, fs_a = audio.load_wav(wav)
        t_wall, acc, gyro, fs_i, t_dev = load_merged_csv_bursts(imu_csv)
        for _c, t0, t1, fault, _t, _n in labels:
            af = label_eval.clip_features(x, fs_a, t0, t1)
            imf = label_eval.imu_clip_features(t_wall, acc, gyro, t_dev, fs_i, t0, t1)
            if imf is None:
                continue
            merged = {**{f"a_{k}": v for k, v in af.items()},
                      **imf}
            if feat_names is None:
                feat_names = list(merged.keys())
            rows.append([merged[k] for k in feat_names])
            y.append(fault)
            groups.append(gi)
    return np.array(rows, float), np.array(y, int), np.array(groups, int), feat_names


def _standardize(X, mean, std):
    return (X - mean) / std


def fit_logreg(X, y, l2=1.0, iters=800, lr=0.3):
    """Standardized logistic regression (numpy GD). Returns (weights, mean, std)."""
    mean = X.mean(0)
    std = X.std(0) + 1e-9
    Xs = _standardize(X, mean, std)
    Xb = np.hstack([Xs, np.ones((len(Xs), 1))])
    w = np.zeros(Xb.shape[1])
    n = len(Xb)
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-(Xb @ w)))
        reg = np.r_[w[:-1], 0.0]            # no penalty on bias
        w -= lr * (Xb.T @ (p - y) / n + l2 * reg / n)
    return w, mean, std


def predict_proba(X, w, mean, std):
    Xb = np.hstack([_standardize(X, mean, std), np.ones((len(X), 1))])
    return 1.0 / (1.0 + np.exp(-(Xb @ w)))


def cross_val_auc(X, y, groups, **fit_kw):
    """Leave-one-group-out CV AUC (LOO per-row if a single group). Pooled over held-out preds."""
    uniq = np.unique(groups)
    folds = [(groups == g) for g in uniq] if len(uniq) > 1 else \
            [(np.arange(len(y)) == i) for i in range(len(y))]
    preds = np.full(len(y), np.nan)
    for test in folds:
        tr = ~test
        if len(np.unique(y[tr])) < 2:
            continue
        w, m, s = fit_logreg(X[tr], y[tr], **fit_kw)
        preds[test] = predict_proba(X[test], w, m, s)
    ok = ~np.isnan(preds)
    return auc(preds[ok][y[ok] == 1], preds[ok][y[ok] == 0])


def train(recordings, l2=1.0):
    X, y, groups, names = build_dataset(recordings)
    cv = cross_val_auc(X, y, groups, l2=l2)
    w, mean, std = fit_logreg(X, y, l2=l2)
    weights = sorted(zip(names, w[:-1]), key=lambda kv: -abs(kv[1]))
    return {"n": len(y), "n_fault": int(y.sum()), "n_recordings": len(np.unique(groups)),
            "cv_auc": cv, "feature_names": names, "weights": weights,
            "model": {"w": w, "mean": mean, "std": std}}


def main(*argv):
    # argv as repeated 4-tuples: name labels.csv wav imu.csv
    recs = [tuple(argv[i:i + 4]) for i in range(0, len(argv), 4)]
    if not recs:
        print("usage: train.py <name> <labels.csv> <wav> <imu.csv> [<name> ...]")
        return
    r = train(recs)
    print(f"trained on {r['n']} labeled clips ({r['n_fault']} fault) "
          f"from {r['n_recordings']} recording(s)")
    print(f"cross-val AUC ({'leave-one-recording-out' if r['n_recordings']>1 else 'leave-one-out'})"
          f" = {r['cv_auc']:.2f}\n")
    print("top feature weights:")
    for name, wt in r["weights"][:8]:
        print(f"  {name:>18} {wt:+.2f}")


if __name__ == "__main__":
    main(*sys.argv[1:])
