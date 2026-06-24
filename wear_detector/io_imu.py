# ABOUTME: Loads Imagimob IMU recordings and live-labels, yields time-aligned labeled windows.
# ABOUTME: Sample-rate-agnostic — infers ODR from timestamps so 50 Hz and 3200 Hz data both work.
import csv
import glob
import os

import numpy as np

# Column order in IMU-Data.data:
# Time (seconds),Accel_X,Accel_Y,Accel_Z,Gyro_X,Gyro_Y,Gyro_Z
ACCEL_COLS = (1, 2, 3)
GYRO_COLS = (4, 5, 6)


def load_imu(session_dir):
    """Return (t, accel[N,3], gyro[N,3]) for one session directory."""
    path = os.path.join(session_dir, "IMU-Data.data")
    rows = []
    with open(path) as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split(",")
            rows.append([float(p) for p in parts[:7]])
    arr = np.asarray(rows, dtype=np.float64)
    t = arr[:, 0]
    accel = arr[:, ACCEL_COLS]
    gyro = arr[:, GYRO_COLS]
    return t, accel, gyro


def infer_fs(t):
    """Infer sampling rate (Hz) from the median timestamp delta."""
    if len(t) < 2:
        raise ValueError("need >=2 samples to infer fs")
    dt = float(np.median(np.diff(t)))
    if dt <= 0:
        raise ValueError("non-positive sample interval")
    return 1.0 / dt


def load_labels(session_dir):
    """Return list of (start_s, end_s, label) from every *.label file in the session."""
    segs = []
    for lf in glob.glob(os.path.join(session_dir, "*.label")):
        with open(lf) as fh:
            reader = csv.reader(fh)
            next(reader, None)  # header
            for row in reader:
                if len(row) < 3 or not row[2].strip():
                    continue
                start = float(row[0])
                length = float(row[1])
                segs.append((start, start + length, row[2].strip()))
    return segs


def window_label(t0, t1, segs, min_overlap=0.5):
    """Majority-overlap label for window [t0, t1]; None if no segment covers >= min_overlap."""
    best_label, best_overlap = None, 0.0
    span = t1 - t0
    for start, end, label in segs:
        overlap = max(0.0, min(t1, end) - max(t0, start))
        if overlap > best_overlap:
            best_overlap, best_label = overlap, label
    return best_label if best_overlap >= min_overlap * span else None


def iter_windows(session_dir, window_s=1.0, overlap=0.5, with_labels=True):
    """Yield (label, accel_win[n,3], gyro_win[n,3], fs) over a session.

    Window length in *samples* is derived from the inferred fs, so the same
    code yields 50-sample windows at 50 Hz and 3200-sample windows at 3200 Hz.
    """
    t, accel, gyro = load_imu(session_dir)
    fs = infer_fs(t)
    n = max(2, int(round(window_s * fs)))
    step = max(1, int(round(n * (1.0 - overlap))))
    segs = load_labels(session_dir) if with_labels else []
    for i in range(0, len(t) - n + 1, step):
        sl = slice(i, i + n)
        label = window_label(t[i], t[i + n - 1], segs) if with_labels else None
        yield label, accel[sl], gyro[sl], fs
