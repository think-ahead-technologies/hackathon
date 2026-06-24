# ABOUTME: Audio<->lap-position correlation — bin acoustic anomalies by figure-8 track phase.
# ABOUTME: Phase-locked acoustic faults -> high spatial contrast -> "track" (vs onboard) verdict.
import os
import sys
from collections import Counter

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wear_detector import audio, localize
from wear_detector.io_imu import load_merged_csv

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DEFAULT_CSV = os.path.join(DATA, "test1", "merged_20260623_17xx.csv")
DEFAULT_WAV = os.path.join(DATA, "test1", "merged_20260623_17xx.wav")


def dominant_laps(t_wall, gyro, fs):
    """Recover figure-8 laps from the gyro and return (laps, variants, dominant-variant laps).

    Times are in the IMU wall clock (load_merged_csv), the same origin as the audio, so a
    lap window [start, end] can be compared directly against an audio window's centre time.
    """
    turns = localize.detect_turns(t_wall, gyro, fs)
    landmarks = localize.crossover_landmarks(turns)
    laps = localize.segment_laps(landmarks)
    variants = localize.cluster_route_variants(laps)
    if not laps:
        return [], [], []
    dom = Counter(variants).most_common(1)[0][0]
    dom_laps = [lap for lap, v in zip(laps, variants) if v == dom]
    return laps, variants, dom_laps


def bin_audio_by_phase(dom_laps, times, values, n_bins=24):
    """Accumulate per-window audio anomaly `values` into a TrackHealthMap by lap phase.

    Each window centre `times[i]` is placed in the lap that contains it; its phase
    (0..1 between crossover landmarks) selects the bin. Windows outside every lap are
    skipped. A fault fixed to a track position lands in the same bin every lap (high
    contrast); one riding the unit spreads across bins (contrast ~1).
    """
    hmap = localize.TrackHealthMap(n_bins)
    for tc, v in zip(np.asarray(times), np.asarray(values)):
        lap = next((lp for lp in dom_laps if lp[0] <= tc < lp[1]), None)
        if lap is None:
            continue
        hmap.add(localize.landmark_phase(tc, lap[0], lap[1]), float(v))
    return hmap


def correlate(csv_path, wav_path, window_s=0.5, hop_s=0.25, n_bands=audio.N_BANDS,
              n_bins=24, threshold_pct=95.0, min_laps=2):
    """Correlate acoustic anomalies against figure-8 track position.

    IMU gyro gives the lap structure; the 16 kHz audio gives the anomaly score; binning
    the latter by the former answers "do the faults recur at a fixed track position?".
    Assumes the IMU and audio share a recording-start origin (a merged-recorder session).

    Localizing a fault to the *track* (vs the unit) is a claim about recurrence ACROSS laps,
    so the verdict needs at least `min_laps` comparable laps. With fewer, a high within-lap
    contrast proves nothing about recurrence — the verdict is held at "inconclusive".
    """
    t_wall, _accel, gyro, fs = load_merged_csv(csv_path)
    laps, variants, dom_laps = dominant_laps(t_wall, gyro, fs)

    res = audio.detect_session(wav_path, window_s=window_s, hop_s=hop_s,
                               n_bands=n_bands, threshold_pct=threshold_pct)
    centres = np.asarray(res["times"]) + window_s / 2.0
    anomaly = bin_audio_by_phase(dom_laps, centres, res["raw"], n_bins=n_bins)

    n_laps = len(dom_laps)
    if n_laps >= min_laps:
        locus = anomaly.classify_locus()
    else:
        locus = "inconclusive"  # not enough laps to argue recurrence either way

    return {
        "fs_imu": fs,
        "fs_audio": res["fs"],
        "laps": len(laps),
        "route_variants": len(set(variants)),
        "dominant_laps": n_laps,
        "min_laps": min_laps,
        "anomaly": {
            "contrast": anomaly.spatial_contrast(),
            "peak_phase": anomaly.peak_phase(),
            "coverage": anomaly.coverage(),
            "locus": locus,
        },
        "anomaly_map": anomaly,
    }


def main(csv_path=DEFAULT_CSV, wav_path=DEFAULT_WAV):
    res = correlate(csv_path, wav_path)
    print(f"imu   : {csv_path}")
    print(f"audio : {wav_path}")
    print(f"laps  : {res['laps']} ({res['route_variants']} route-variant(s); "
          f"{res['dominant_laps']} in the dominant variant)")
    a = res["anomaly"]
    peak = f"{a['peak_phase']:.2f}" if a["peak_phase"] is not None else "n/a"
    print("\naudio anomaly vs track position (dominant variant):")
    print(f"  spatial contrast : {a['contrast']:.2f}  (peak/median bin)")
    print(f"  peak @ lap phase : {peak}")
    print(f"  coverage         : {a['coverage']:.2f}")
    print(f"  locus verdict    : {a['locus'].upper()}")
    if a["locus"] == "track":
        print("  -> acoustic faults recur at a fixed track position: TRACK defect, not onboard wear.")
    elif a["locus"] == "onboard":
        print("  -> faults uniform across track positions: rides the UNIT (onboard wear).")
    elif a["locus"] == "inconclusive":
        print(f"  -> only {res['dominant_laps']} lap(s) in the dominant variant "
              f"(need >= {res['min_laps']}): can't argue recurrence across laps yet.")
        print("     The contrast above is within-lap only — a longer / less route-variable")
        print("     recording (more comparable laps) is needed for a track-vs-onboard call.")
    else:
        print("  -> insufficient lap coverage to localize (need more laps / cleaner turns).")


if __name__ == "__main__":
    args = sys.argv[1:]
    main(*(args or (DEFAULT_CSV, DEFAULT_WAV)))
