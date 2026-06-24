# ABOUTME: Loads IMU recordings (Imagimob dirs + merged-recorder CSV), yields labeled windows.
# ABOUTME: Sample-rate-agnostic — infers ODR from timestamps so 50/100/3200 Hz data all work.
import csv
import glob
import os

import numpy as np

# Column order in IMU-Data.data:
# Time (seconds),Accel_X,Accel_Y,Accel_Z,Gyro_X,Gyro_Y,Gyro_Z
ACCEL_COLS = (1, 2, 3)
GYRO_COLS = (4, 5, 6)

# Merged-recorder CSV (data/test1/*.csv): SI columns we consume (units already g / dps).
CSV_ACCEL_COLS = ("acc_x_g", "acc_y_g", "acc_z_g")
CSV_GYRO_COLS = ("gyr_x_dps", "gyr_y_dps", "gyr_z_dps")


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


def _read_merged_csv(path):
    """Parse a merged-recorder CSV -> (accel[N,3], gyro[N,3], t_dev_us[N], t_rel_s[N])."""
    acc, gyr, t_dev_us, t_rel_s = [], [], [], []
    with open(path) as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            acc.append([float(row[c]) for c in CSV_ACCEL_COLS])
            gyr.append([float(row[c]) for c in CSV_GYRO_COLS])
            t_dev_us.append(float(row["t_dev_us"]))
            t_rel_s.append(float(row["t_rel_s"]))
    return (np.asarray(acc, dtype=np.float64), np.asarray(gyr, dtype=np.float64),
            np.asarray(t_dev_us, dtype=np.float64), np.asarray(t_rel_s, dtype=np.float64))


def load_merged_csv(path):
    """Return (t_wall, accel[N,3], gyro[N,3], fs) for a merged-recorder CSV.

    t_wall is the host wall clock (t_rel_s) zeroed to the first sample — the timeline
    to align against other modalities (audio / video) recorded in the same session.
    fs is the nominal IMU ODR recovered from the device clock (see load_imu_csv).
    """
    accel, gyro, t_dev_us, t_rel = _read_merged_csv(path)
    fs = _nominal_fs_from_dev_clock(t_dev_us)
    t_wall = t_rel - t_rel[0]
    return t_wall, accel, gyro, fs


def load_merged_csv_bursts(path):
    """Return (t_wall, accel, gyro, fs, t_dev_us) — same as load_merged_csv plus the raw
    device clock, which marks the high-rate bursts (it resets each burst). Burst-aware
    spectral extraction needs this; the uniform t from load_imu_csv would hide the gaps."""
    accel, gyro, t_dev_us, t_rel = _read_merged_csv(path)
    fs = _nominal_fs_from_dev_clock(t_dev_us)
    t_wall = t_rel - t_rel[0]
    return t_wall, accel, gyro, fs, t_dev_us


def load_imu_csv(path):
    """Return (t, accel[N,3], gyro[N,3]) for a merged-recorder CSV.

    The recorder delivers samples in small bursts: t_rel_s repeats within a burst
    and the device clock t_dev_us resets across bursts, so neither column is a clean
    monotonic timeline on its own. We recover the sensor's nominal ODR from the
    dominant positive t_dev_us step and hand back a uniform time vector — that is the
    only timing the spectral feature path needs, and it keeps infer_fs() exact.
    Use load_merged_csv when you need the real wall clock for cross-modal alignment.
    """
    _t_wall, accel, gyro, fs = load_merged_csv(path)
    t = np.arange(len(accel), dtype=np.float64) / fs
    return t, accel, gyro


def _nominal_fs_from_dev_clock(t_dev_us):
    """Nominal ODR (Hz) from the most common positive device-clock step (microseconds)."""
    d = np.diff(t_dev_us)
    d = d[d > 0]
    if d.size == 0:
        raise ValueError("no positive device-clock steps to infer fs")
    step_us = float(np.median(d))
    return 1e6 / step_us


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


def _is_csv_session(session):
    """True for the merged-recorder format: a path to a .csv, or a dir holding one."""
    if session.endswith(".csv"):
        return True
    return os.path.isdir(session) and bool(glob.glob(os.path.join(session, "*.csv")))


def _csv_path(session):
    if session.endswith(".csv"):
        return session
    return sorted(glob.glob(os.path.join(session, "*.csv")))[0]


def iter_windows(session_dir, window_s=1.0, overlap=0.5, with_labels=True,
                 session_label=None):
    """Yield (label, accel_win[n,3], gyro_win[n,3], fs) over a session.

    Window length in *samples* is derived from the inferred fs, so the same
    code yields 50-sample windows at 50 Hz and 100/3200-sample windows at 100/3200 Hz.

    Two input formats are accepted: an Imagimob session directory (per-segment
    *.label files) and the merged-recorder CSV (one path, no per-segment labels —
    pass ``session_label`` to tag every window, e.g. "fault" for a fault recording).
    """
    if _is_csv_session(session_dir):
        t, accel, gyro = load_imu_csv(_csv_path(session_dir))
        segs = []
    else:
        t, accel, gyro = load_imu(session_dir)
        segs = load_labels(session_dir) if with_labels else []
    fs = infer_fs(t)
    n = max(2, int(round(window_s * fs)))
    step = max(1, int(round(n * (1.0 - overlap))))
    for i in range(0, len(t) - n + 1, step):
        sl = slice(i, i + n)
        if segs:
            label = window_label(t[i], t[i + n - 1], segs)
        else:
            label = session_label
        yield label, accel[sl], gyro[sl], fs
