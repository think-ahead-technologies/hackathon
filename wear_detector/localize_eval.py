# ABOUTME: Phase 4 demo — recover figure-8 laps from gyro, build per-variant track-health maps.
# ABOUTME: Validates phase machinery on turn energy (sharp peaks) then maps fault anomaly (flat=onboard).
import os
import sys
from collections import Counter

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wear_detector import features, localize
from wear_detector.emit_contract_b import fit_baseline
from wear_detector.io_imu import infer_fs, load_imu

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "thinkathon_kickstart", "data")
HEALTHY = [os.path.join(DATA, d) for d in (
    "Session-2026-06-17--11-25-33_all_normal",
    "Session-2026-06-17--11-26-26_all_normal",
)]


def window_centers(t, fs):
    n = max(2, int(round(fs)))
    step = max(1, n // 2)
    idx = list(range(0, len(t) - n + 1, step))
    return n, step, idx


def build_maps(session, det):
    t, accel, gyro = load_imu(session)
    fs = infer_fs(t)
    turns = localize.detect_turns(t, gyro, fs)
    landmarks = localize.crossover_landmarks(turns)
    laps = localize.segment_laps(landmarks)
    variants = localize.cluster_route_variants(laps)
    dom = Counter(variants).most_common(1)[0][0] if variants else None
    dom_laps = [lap for lap, v in zip(laps, variants) if v == dom]

    env = localize.yaw_envelope(gyro, fs)
    n, step, idx = window_centers(t, fs)
    energy_map = localize.TrackHealthMap()   # validation: turn energy
    anomaly_map = localize.TrackHealthMap()  # fault anomaly score
    for i in idx:
        c = i + n // 2
        tc = t[c]
        lap = next((lp for lp in dom_laps if lp[0] <= tc < lp[1]), None)
        if lap is None:
            continue
        ph = localize.landmark_phase(tc, lap[0], lap[1])
        energy_map.add(ph, float(env[i:i + n].mean()))
        v = float(det.score([features.extract(accel[i:i + n], gyro[i:i + n], fs)])[0])
        anomaly_map.add(ph, v)
    return turns, laps, variants, dom_laps, energy_map, anomaly_map


def main():
    fs = infer_fs(load_imu(HEALTHY[0])[0])
    det = fit_baseline(HEALTHY, fs)
    for tag, sess in [("HEALTHY", "Session-2026-06-17--11-25-33_all_normal"),
                      ("FAULT (onboard bearing)", "Session-2026-06-17--13-49-38_faulty_bearing"),
                      ("VARIABLE-ROUTE", "Session-2026-06-18--10-27-16-normal-data-without-defective-parts")]:
        turns, laps, variants, dom_laps, emap, amap = build_maps(os.path.join(DATA, sess), det)
        durs = [round(e - s) for s, e in laps]
        nv = len(set(variants))
        print(f"\n=== {tag}: {sess[:42]} ===")
        print(f"  turns={len(turns)}  laps={len(laps)}  route-variants={nv}  lap-dur={durs}")
        print(f"  dominant variant: {len(dom_laps)} laps")
        print(f"  turn-ENERGY map  : contrast={emap.spatial_contrast():.1f}  "
              f"peak@phase={emap.peak_phase():.2f}  coverage={emap.coverage():.2f}  "
              f"-> machinery resolves position (turns are sharp)")
        print(f"  fault-ANOMALY map: contrast={amap.spatial_contrast():.2f}  "
              f"locus={amap.classify_locus()}  coverage={amap.coverage():.2f}")


if __name__ == "__main__":
    main()
