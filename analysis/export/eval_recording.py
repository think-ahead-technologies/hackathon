# ABOUTME: Scores the deployed int8 model on a held-out merged-recorder recording + clip labels.
# ABOUTME: Cross-recording probe: resample accel to the model's native 50 Hz, window, label, score.
import json
import os
import sys

import numpy as np
import pandas as pd
import tensorflow as tf

import spectro
import metrics
from train import BUILD, _as_input
from evaluate import _int8_margins

TFLITE_PATH = os.path.join(BUILD, "model_int8.tflite")
META_PATH = os.path.join(BUILD, "model-meta.json")

# The model was trained on 50 Hz spectrograms; its filterbank bands are tied to that
# Nyquist. So a higher-rate recording is resampled onto a uniform 50 Hz wall-clock grid
# (decimating away the >25 Hz content the 50 Hz model never saw) before the front-end.
TARGET_FS = 50.0


def load_merged_imu(csv_path):
    """Return (t_wall, accel[N,3], gyro[N,3]) from a merged-recorder CSV, wall-clock zeroed."""
    df = pd.read_csv(csv_path)
    t = df["t_rel_s"].to_numpy(dtype=np.float64)
    acc = df[["acc_x_g", "acc_y_g", "acc_z_g"]].to_numpy(dtype=np.float64)
    gyr = df[["gyr_x_dps", "gyr_y_dps", "gyr_z_dps"]].to_numpy(dtype=np.float64)
    return t - t[0], acc, gyr


def resample_uniform(t, sig, fs):
    """Linear-interpolate bursty/duplicate-timestamp sensor data onto a uniform fs grid.

    The transfer is bursty (many samples share a wall-clock instant), so we average
    duplicate timestamps to one value, then interpolate to the uniform grid — the honest
    wall-clock view the 50 Hz model expects.
    """
    ut, inv = np.unique(t, return_inverse=True)
    counts = np.bincount(inv)
    avg = np.column_stack([np.bincount(inv, weights=sig[:, k]) / counts
                           for k in range(sig.shape[1])])
    grid = np.arange(0.0, ut[-1], 1.0 / fs)
    out = np.column_stack([np.interp(grid, ut, avg[:, k]) for k in range(sig.shape[1])])
    return grid, out


def windows(grid, acc, gyr, fs):
    """Yield (spectrogram[49,40,2], center_time_s) over WINDOW_S windows at 50% overlap."""
    w = int(round(spectro.WINDOW_S * fs))
    h = max(1, int(round(w * 0.5)))
    for s in range(0, len(acc) - w + 1, h):
        yield spectro.imu_to_spectrogram(acc[s:s + w], gyr[s:s + w], fs), grid[s + w // 2]


def label_windows(centers, labels):
    """1 if a window center falls in a clip with fault==1, else 0."""
    y = np.zeros(len(centers), dtype=int)
    faults = labels[labels["fault"] == 1]
    for _, r in faults.iterrows():
        y[(centers >= r["t_start_s"]) & (centers < r["t_end_s"])] = 1
    return y


def _report(name, margins, y, thr):
    """Print window-level AUC + the contract-threshold and recalibrated operating points."""
    if y.sum() == 0 or (y == 0).sum() == 0:
        print(f"  {name}: no both-class labels — skipped")
        return float("nan")
    auc = metrics.auc(margins, y)
    pred = margins >= thr
    fpr = float((pred & (y == 0)).sum() / max(1, (y == 0).sum()))
    tpr = float((pred & (y == 1)).sum() / max(1, (y == 1).sum()))
    op = metrics.operating_point(margins, y, 0.10)
    print(f"  vs {name} labels ({int(y.sum())}/{len(y)} fault): AUC {auc:.3f} | "
          f"@contract-thr FPR {fpr:.2f}/FNR {1-tpr:.2f} | recal FPR {op['fpr']:.2f}/FNR {op['fnr']:.2f}")
    return auc


def _rf_labels(grid, acc_u, gyr_u, centers):
    """IMU-native fault labels for the resampled recording, from the analysis RF (rf_labeler)."""
    import rf_labeler
    clf, thr, auc = rf_labeler.train_labeler()
    spans = rf_labeler.label_imu(acc_u, gyr_u, grid, TARGET_FS, clf, thr)
    y = np.zeros(len(centers), dtype=int)
    for a, b in spans:
        y[(centers >= a) & (centers < b)] = 1
    return y, auc, len(spans)


def main(csv_path, labels_path):
    t, acc, gyr = load_merged_imu(csv_path)
    grid, acc_u = resample_uniform(t, acc, TARGET_FS)
    _, gyr_u = resample_uniform(t, gyr, TARGET_FS)
    specs, centers = zip(*windows(grid, acc_u, gyr_u, TARGET_FS))
    X = np.stack(specs).astype(np.float32)
    centers = np.asarray(centers)
    margins = _int8_margins(TFLITE_PATH, _as_input(X))
    thr = json.load(open(META_PATH))["classifier"]["threshold"]

    print(f"recording span {grid[-1]:.0f}s -> {len(centers)} windows @ {TARGET_FS:.0f} Hz")

    # Referee 1: the human acoustic labels (rattle/squeal) — cross-modal, weak (see README).
    if os.path.exists(labels_path):
        y_ac = label_windows(centers, pd.read_csv(labels_path))
        _report("acoustic", margins, y_ac, thr)

    # Referee 2: IMU-native labels from the analysis RF reference detector. Measures
    # cross-recording NN<->RF agreement (both read the same vibration signal) — not
    # independent ground truth, but the apt referee for an IMU model.
    y_rf, rf_auc, n_spans = _rf_labels(grid, acc_u, gyr_u, centers)
    print(f"RF reference flagged {n_spans} fault spans on this recording")
    return _report("RF (IMU-native)", margins, y_rf, thr)


if __name__ == "__main__":
    csv_path = sys.argv[1] if len(sys.argv) > 1 else \
        os.path.join(os.path.dirname(BUILD), "..", "..", "data", "test2", "merged_1600hz.csv")
    labels_path = sys.argv[2] if len(sys.argv) > 2 else \
        os.path.join(os.path.dirname(csv_path), "labels.csv")
    main(os.path.abspath(csv_path), os.path.abspath(labels_path))
