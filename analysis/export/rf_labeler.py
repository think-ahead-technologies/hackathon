# ABOUTME: Wraps analysis/features.py's bearing RandomForest reference detector as an IMU labeler.
# ABOUTME: Trains on the human-labeled sessions, then emits fault time-spans for any IMU recording.
import os
import sys

import numpy as np
from sklearn.ensemble import RandomForestClassifier

# analysis/ (parent) holds features (extract_windows, window_labels, session lists) + imloader.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import features as F  # noqa: E402
import imloader as L  # noqa: E402

# These silver labels are only as good as the reference RF, which reads the SAME vibration
# signal the on-device model does. Use them to distil the reference detector into the model
# and as a cross-recording referee — NOT as independent physical ground truth.
#
# Operating point matters by use: as TRAINING truth we want precision (clean positives), so the
# default is Youden-optimal (balances TPR-FPR), not high-recall. High recall is a good screen but
# its ~24% FPR (README) makes noisy training labels that teach the model to over-fire.
HIGH_RECALL_FNR = 0.10
LOW_FPR = 0.05          # precision operating point for silver training labels
SILVER_MIN_RUN = 3      # persistence: require this many consecutive flagged windows (event-level)


def _rf():
    return RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                  random_state=0, n_jobs=-1)


def reference_dataset():
    """Pooled bearing features + labels + per-session group over the human-labeled sessions.

    Mirrors analysis/03_feature_distributions.py (the cached dataset.npz), rebuilt here so
    the labeler is self-contained and uses the live feature code.
    """
    X, y, grp, hop_s = [], [], [], None
    for si, name in enumerate(F.FAULT_SESSIONS + F.NORMAL_SESSIONS):
        imu = L.load_imu(os.path.join(L.DATA_ROOT, name))
        if imu is None:
            continue
        fs = L.imu_fs(imu)
        feats, centers = F.extract_windows(imu, fs)
        if len(feats) == 0:
            continue
        labels = L.load_labels(os.path.join(L.DATA_ROOT, name))
        X.append(feats)
        y.append(F.window_labels(centers, labels))
        grp.append(np.full(len(feats), si))
    return np.vstack(X), np.concatenate(y), np.concatenate(grp)


def loso_operating_points(X, y, grp):
    """Leave-one-session-out OOF probabilities -> {op: threshold} + AUC.

    Honest operating points: thresholds are chosen on held-out predictions, then frozen and
    applied to new recordings. The AUC is the sanity check that this reproduces the reference.
    """
    oof = np.full(len(y), np.nan)
    for held in sorted(set(grp[y == 1])):
        tr, te = grp != held, grp == held
        oof[te] = _rf().fit(X[tr], y[tr]).predict_proba(X[te])[:, 1]
    m = ~np.isnan(oof)
    yb, sb = y[m], oof[m]
    ts = np.unique(sb)
    fnrs = np.array([np.mean(sb[yb == 1] < t) for t in ts])
    fprs = np.array([np.mean(sb[yb == 0] >= t) for t in ts])
    ok = np.where(fnrs <= HIGH_RECALL_FNR)[0]
    lo = np.where(fprs <= LOW_FPR)[0]
    ops = {
        "youden": float(ts[np.argmax((1 - fnrs) - fprs)]),          # max TPR-FPR (balanced)
        "high_recall": float(ts[ok[np.argmin(fprs[ok])]]) if len(ok) else float(np.median(ts)),
        "low_fpr": float(ts[lo[np.argmin(fnrs[lo])]]) if len(lo) else float(np.max(ts)),  # precision
    }
    pos, neg = sb[yb == 1], sb[yb == 0]
    auc = float(np.mean(neg[None, :] < pos[:, None]) + 0.5 * np.mean(neg[None, :] == pos[:, None]))
    return ops, auc


def train_labeler(op="youden"):
    """Return (clf trained on all human-labeled windows, frozen threshold for `op`, loso_auc)."""
    X, y, grp = reference_dataset()
    ops, auc = loso_operating_points(X, y, grp)
    return _rf().fit(X, y), ops[op], auc


def _windows_to_spans(centers, fault, hop_s, min_run):
    """Merge consecutive fault windows into (start, end) spans; keep only runs >= min_run.

    The persistence (min_run) filter is the event-level step from analysis/12_event_level.py:
    isolated single-window flags are the detector's false alarms, so requiring a sustained
    run of flagged windows is what turns the noisy per-window screen into clean fault events.
    """
    spans, i, n = [], 0, len(fault)
    while i < n:
        if not fault[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and (fault[j + 1] or (j + 2 < n and fault[j + 2])):
            j += 1
        # Count *actually* flagged windows in the run (not the bridged length) so scattered
        # single-window false alarms can't be stitched into a spurious event.
        if int(np.sum(fault[i:j + 1])) >= min_run:
            spans.append((float(centers[i] - hop_s), float(centers[j] + hop_s)))
        i = j + 1
    return spans


def label_imu(accel, gyro, t, fs, clf, thr, min_run=SILVER_MIN_RUN):
    """Run the reference RF over an IMU recording -> list of (start_s, end_s) fault spans."""
    import pandas as pd
    imu = pd.DataFrame({"t": t, "ax": accel[:, 0], "ay": accel[:, 1], "az": accel[:, 2],
                        "gx": gyro[:, 0], "gy": gyro[:, 1], "gz": gyro[:, 2]})
    feats, centers = F.extract_windows(imu, fs)
    if len(feats) == 0:
        return []
    prob = clf.predict_proba(feats)[:, 1]
    return _windows_to_spans(centers, prob >= thr, F.HOP_S, min_run)


if __name__ == "__main__":
    X, y, grp = reference_dataset()
    ops, auc = loso_operating_points(X, y, grp)
    print(f"reference bearing RF: LOSO window-level AUC {auc:.3f} (analysis README 0.89)")
    print(f"frozen thresholds: youden {ops['youden']:.3f} | high_recall {ops['high_recall']:.3f}")
