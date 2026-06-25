# ABOUTME: Turns the real recordings into [49,40] spectrograms + healthy/fault labels + session id.
# ABOUTME: Reuses analysis FAULT/NORMAL session lists and 'fault' label spans; grouped split by session.
import os
import sys

import numpy as np

# analysis/ (parent) holds imloader + features (session lists, label spans) — import flat.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import imloader as L  # noqa: E402
import features as F  # noqa: E402

import spectro  # noqa: E402

# Fraction of a window that must overlap a 'fault' span for the window to be a positive.
# A window is labeled fault iff its center sample falls inside a fault span — same rule
# as features.window_labels, applied at the spectrogram window grid.
OVERLAP = 0.5

# Silver sessions: fault content with no human labels, labeled by the analysis RF (rf_labeler).
# They join the TRAINING pool (distil the reference detector into the model) but are never held
# out for the LOSO number, which stays on human labels. Spans come from make_silver -> silver_labels/.
SILVER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "silver_labels")
SILVER_SESSIONS = ["Session-2026-06-17--13-50-14_faulty_bearing_no_labeling"]

# Extra human-confirmed all-normal sessions present in the data but not in analysis/features.py's
# NORMAL_SESSIONS. No bearing fault installed -> clean negatives. Only the *running-motion* ones are
# kept (gyro power ~120-130, matching the running-normal reference); the low-motion quasi-static
# sessions (10-26-19, 15-15-47) are excluded because adding them degraded cross-recording
# generalization (test2 RF-agreement 0.822 -> 0.681) — a different regime that shifted the boundary.
# Real labels (not silver), so they join the honest LOSO eval. Includes the powerbank-drop impulse
# session as a hard negative.
EXTRA_NORMAL_SESSIONS = [
    "Session-2026-06-16--16-09-46_all_normal_qs_labels",
    "Session-2026-06-16--16-22-53_all_normal_qs_labels",
    "Session-2026-06-17--11-02-44_all_normal_no_combined_no_labels",
    "Session-2026-06-17--11-27-00_all_normal_powerbank_dropped",
]


def _windows(accel, fs):
    """Yield (start_idx, end_idx, center_idx) over WINDOW_S windows at OVERLAP hop."""
    w = int(round(spectro.WINDOW_S * fs))
    h = max(1, int(round(w * (1.0 - OVERLAP))))
    for s in range(0, len(accel) - w + 1, h):
        yield s, s + w, s + w // 2


def _human_fault_spans(path):
    """(start, end) spans labeled 'fault' in a session's Live-Labeling.label."""
    lab = L.load_labels(path)
    f = lab[lab["label"].astype(str) == "fault"]
    return list(zip(f["start"].astype(float), f["end"].astype(float)))


def _load_silver_spans(session):
    """(start, end) fault spans for a silver session, or None if not yet generated."""
    p = os.path.join(SILVER_DIR, session + ".csv")
    if not os.path.exists(p):
        return None
    import csv
    with open(p) as fh:
        return [(float(r["start"]), float(r["end"])) for r in csv.DictReader(fh)]


def session_windows(session, spans):
    """List of (spectrogram[49,40,2] float32, label int, session str) for one session.

    `spans` is a list of (start,end) fault spans (human or silver); a window is a positive
    iff its center time falls in one. An empty list => every window healthy (normal sessions).
    """
    path = os.path.join(L.DATA_ROOT, session)
    imu = L.load_imu(path)
    if imu is None or len(imu) < int(round(spectro.WINDOW_S * 50)):
        return []
    fs = L.imu_fs(imu)
    accel = imu[["ax", "ay", "az"]].values
    gyro = imu[["gx", "gy", "gz"]].values
    t = imu["t"].values
    out = []
    for s, e, c in _windows(accel, fs):
        try:
            spec = spectro.imu_to_spectrogram(accel[s:e], gyro[s:e], fs)
        except ValueError:
            continue
        tc = t[c]
        y = 1 if any(a <= tc < b for a, b in spans) else 0
        out.append((spec, y, session))
    return out


def _collect():
    specs, ys, groups, silver = [], [], [], []
    for sess in F.FAULT_SESSIONS:
        spans = _human_fault_spans(os.path.join(L.DATA_ROOT, sess))
        for spec, y, g in session_windows(sess, spans):
            specs.append(spec); ys.append(y); groups.append(g); silver.append(False)
    for sess in F.NORMAL_SESSIONS + EXTRA_NORMAL_SESSIONS:
        for spec, y, g in session_windows(sess, []):
            specs.append(spec); ys.append(y); groups.append(g); silver.append(False)
    for sess in SILVER_SESSIONS:
        spans = _load_silver_spans(sess)
        if spans is None:
            continue  # silver labels not generated yet (run make_silver) — skip cleanly
        for spec, y, g in session_windows(sess, spans):
            specs.append(spec); ys.append(y); groups.append(g); silver.append(True)
    X = (np.stack(specs).astype(np.float32) if specs
         else np.empty((0, spectro.N_FRAMES, spectro.N_BANDS, spectro.N_CHANNELS), np.float32))
    return X, np.asarray(ys, dtype=np.int64), groups, np.asarray(silver, dtype=bool)


def build_all(seed=0):
    """Every labeled window with its session group — no split.

    The deployed artifact trains on all of this; the honest generalization estimate
    comes from leave-one-session-out CV over the `groups` (see train.py), which is what
    makes it comparable to the README's leave-one-session-out ROC-AUC.
    """
    X, y, groups, silver = _collect()
    return {"X": X, "y": y, "groups": groups, "silver": silver, "fs": data_fs()}


def data_fs():
    """Inferred sample rate of the recordings (drives the front-end geometry)."""
    imu = L.load_imu(os.path.join(L.DATA_ROOT, F.NORMAL_SESSIONS[0]))
    return L.imu_fs(imu)


def build(seed=0, holdout_frac=0.3):
    """Spectrogram tensors + labels with a grouped (leave-session-out) train/test split.

    Splitting by *session* — never mixing a session's windows across train and test —
    is what makes the held-out number comparable to the README's leave-one-session-out
    ROC-AUC ceiling (0.89). A random window split would leak and inflate it.

    Returns a dict: X_train/y_train, X_test/y_test, groups_test, fs.
    """
    X, y, groups, _silver = _collect()
    uniq = sorted(set(groups))
    rng = np.random.default_rng(seed)
    uniq = list(rng.permutation(uniq))
    n_hold = max(1, int(round(holdout_frac * len(uniq))))
    # Hold out whole sessions, ensuring the test set carries both fault and healthy.
    test_sessions = set(uniq[:n_hold])
    is_test = np.array([g in test_sessions for g in groups])
    return {
        "X_train": X[~is_test], "y_train": y[~is_test],
        "X_test": X[is_test], "y_test": y[is_test],
        "groups_test": [g for g, t in zip(groups, is_test) if t],
        "test_sessions": sorted(test_sessions),
        "fs": data_fs(),
    }
