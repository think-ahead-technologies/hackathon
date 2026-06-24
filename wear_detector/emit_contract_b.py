# ABOUTME: Streams one recording session through the detector and prints §7 Contract B as NDJSON.
# ABOUTME: Usage: python -m wear_detector.emit_contract_b <session_dir> [--container ID]
import argparse
import json
import os
import sys
from collections import deque

import numpy as np

# ~5 s causal smoothing of the per-window score = the operational vibration score.
# Per-window 50 Hz detection is weak (AUC 0.79); this rolling mean is the AUC-0.873
# operating point, so staging runs on it. v_thr is its ~5%-healthy-FPR threshold.
SMOOTH_WINDOWS = 10
V_THRESHOLD = 0.85

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wear_detector import features, localize
from wear_detector.contract import StageMachine, contract_b
from wear_detector.detector import PerUnitBaselineDetector
from wear_detector.io_imu import infer_fs, iter_windows, load_imu

MODEL_VERSION = "wear_detector-imu-directed-0.1"


def fit_baseline(healthy_dirs, fs):
    names = features.detector_feature_names(fs)
    train = []
    for d in healthy_dirs:
        for lab, a, g, f in iter_windows(d):
            if lab in ("normal", None):
                train.append(features.extract(a, g, f))
    return PerUnitBaselineDetector(names, method="directed").fit(train)


def calibrate_threshold(det, healthy_dirs, fpr=0.05):
    """v_thr = the (1-fpr) percentile of the causal smoothed healthy score.

    Calibrating on healthy data makes the false-alarm rate ~fpr by construction,
    instead of guessing a fixed threshold. Per-unit: re-run per device.
    """
    smoothed = []
    for d in healthy_dirs:
        recent = deque(maxlen=SMOOTH_WINDOWS)
        for lab, a, g, f in iter_windows(d):
            if lab not in ("normal", None):
                continue
            recent.append(float(det.score([features.extract(a, g, f)])[0]))
            smoothed.append(sum(recent) / len(recent))
    return float(np.percentile(smoothed, 100 * (1 - fpr)))


def build_lap_index(t, gyro, fs):
    """Map each window center time to (lap_phase, route_variant) using the gyro turn-signal.

    Phase 4 (§6): signed-yaw turns -> figure-8 crossover landmarks -> laps segmented between
    crossovers (variable length) -> route variants by duration. track_position is the
    landmark-relative phase; fault_locus accumulates per variant across laps.
    """
    turns = localize.detect_turns(t, gyro, fs)
    laps = localize.segment_laps(localize.crossover_landmarks(turns))
    variants = localize.cluster_route_variants(laps)

    def lookup(tc):
        for lap, var in zip(laps, variants):
            if lap[0] <= tc < lap[1]:
                return localize.landmark_phase(tc, lap[0], lap[1]), var
        return None, None

    return lookup, set(variants)


def emit(session_dir, healthy_dirs, container_id, ts_base=0.0):
    fs = infer_fs(load_imu(session_dir)[0])
    det = fit_baseline(healthy_dirs, fs)
    v_thr = calibrate_threshold(det, healthy_dirs)
    machine = StageMachine(v_thr=v_thr, dwell=3, release=4)
    t, _, gyro = load_imu(session_dir)
    lookup, variant_ids = build_lap_index(t, gyro, fs)
    track_maps = {v: localize.TrackHealthMap() for v in variant_ids}
    n = max(2, int(round(fs)))            # 1 s window
    step = max(1, int(round(n * 0.5)))    # 50% overlap (matches iter_windows default)
    recent = deque(maxlen=SMOOTH_WINDOWS)
    records = []
    for i, (_, accel, gyro_w, f) in enumerate(iter_windows(session_dir)):
        raw = float(det.score([features.extract(accel, gyro_w, f)])[0])
        recent.append(raw)
        v = sum(recent) / len(recent)     # causal rolling-mean = operational score
        stage = machine.update(v)         # a=None: vibration-only today
        center = min(i * step + n // 2, len(t) - 1)
        phase, var = lookup(t[center])
        if var is not None:
            track_maps[var].add(phase, raw)       # accumulate the per-variant track map
            locus = track_maps[var].classify_locus()  # running verdict; unknown until covered
            track_position = round(float(phase), 3)
        else:
            locus, track_position = "unknown", None  # partial head/tail lap
        rec = contract_b(
            ts=round(ts_base + float(t[i * step] if i * step < len(t) else t[-1]), 3),
            container_id=container_id,
            model_version=MODEL_VERSION,
            v=v, a=None, stage=stage, trend=machine.trend(),
            track_position=track_position, fault_locus=locus,
        )
        records.append(rec)
    return records


def main():
    p = argparse.ArgumentParser()
    p.add_argument("session_dir")
    p.add_argument("--container", default="unit-01")
    p.add_argument("--limit", type=int, default=0, help="print only first N records")
    args = p.parse_args()

    data_root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "data", "thinkathon_kickstart", "data")
    healthy = [os.path.join(data_root, d) for d in (
        "Session-2026-06-17--10-46-14_normal",
        "Session-2026-06-17--11-25-33_all_normal",
        "Session-2026-06-17--11-26-26_all_normal",
        "Session-2026-06-18--10-27-16-normal-data-without-defective-parts",
    )]
    recs = emit(args.session_dir, healthy, args.container)
    out = recs if args.limit <= 0 else recs[:args.limit]
    for r in out:
        print(json.dumps(r))
    stages = [r["severity_stage"] for r in recs]
    sys.stderr.write(
        f"\n{len(recs)} records | stage hist: "
        + " ".join(f"{s}:{stages.count(s)}" for s in (0, 1, 2, 3)) + "\n")


if __name__ == "__main__":
    main()
