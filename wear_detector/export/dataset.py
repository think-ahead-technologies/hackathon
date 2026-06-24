# ABOUTME: Turns the real recordings into [49,40] spectrograms + healthy/fault labels + unit id.
# ABOUTME: Healthy-only windows train the AE; the full set drives the embedding-distance eval.
import os

import numpy as np

from wear_detector.export import spectro
from wear_detector.evaluate import HEALTHY, FAULT, LADDER, DATA
from wear_detector.io_imu import iter_windows, load_imu, infer_fs

# All current recordings are one rig, so the per-unit baseline anchors per *session*
# (each session == one observed unit-condition). The machinery is per-unit either way.
HEALTHY_KEEP = lambda l: l in ("normal", None)
FAULT_KEEP = lambda l: l == "fault"


def session_spectrograms(session, keep):
    """List of (spectrogram[49,40] float32, session) for windows passing keep(label)."""
    path = os.path.join(DATA, session)
    out = []
    if not os.path.isdir(path):
        return out
    for label, accel, gyro, fs in iter_windows(path, window_s=spectro.WINDOW_S, overlap=0.5):
        if keep(label):
            out.append((spectro.accel_to_spectrogram(accel, fs), session))
    return out


def _collect(sessions, keep):
    specs, units = [], []
    for s in sessions:
        for spec, unit in session_spectrograms(s, keep):
            specs.append(spec)
            units.append(unit)
    if not specs:
        return np.empty((0, spectro.N_FRAMES, spectro.N_BANDS), np.float32), []
    return np.stack(specs).astype(np.float32), units


def data_fs():
    """Inferred sample rate of the recordings (drives the front-end geometry)."""
    return infer_fs(load_imu(os.path.join(DATA, HEALTHY[0]))[0])


def build(seed=0, train_frac=0.7):
    """Healthy/fault spectrogram tensors + per-unit grouping for the train split.

    Returns a dict:
      X_train          healthy windows the AE learns to reconstruct  [n,49,40]
      units_train      session/unit id per train window (per-unit centroid grouping)
      X_healthy_test   held-out healthy windows (negatives in eval)
      X_fault          fault windows (positives in eval)
      fs               inferred sample rate
    """
    healthy, h_units = _collect(HEALTHY, HEALTHY_KEEP)
    fault, _ = _collect(FAULT, FAULT_KEEP)
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(healthy))
    cut = int(train_frac * len(healthy))
    tr, te = order[:cut], order[cut:]
    return {
        "X_train": healthy[tr],
        "units_train": [h_units[i] for i in tr],
        "X_healthy_test": healthy[te],
        "X_fault": fault,
        "fs": data_fs(),
    }


def ladder_spectrograms():
    """Severity-ladder fault sets (2/4/8 bearings) for the no-monotonic-grading check."""
    return {name: _collect([sess], FAULT_KEEP)[0] for name, sess in LADDER}
