# ABOUTME: Acoustic anomaly demo — self-baseline scan of a 16 kHz recording, prints the timeline.
# ABOUTME: Clusters flagged windows into events (the built-in track errors recur across laps).
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wear_detector import audio

DEFAULT_WAV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "test1", "merged_20260623_17xx.wav")


def cluster(times, gap_s):
    """Group sorted timestamps into events separated by more than gap_s."""
    events = []
    for t in times:
        if events and t - events[-1][-1] <= gap_s:
            events[-1].append(t)
        else:
            events.append([t])
    return events


def main(path, window_s=0.5, hop_s=0.25, threshold_pct=97.0, gap_s=3.0):
    res = audio.detect_session(path, window_s=window_s, hop_s=hop_s,
                               threshold_pct=threshold_pct)
    t = np.asarray(res["times"])
    s = np.asarray(res["scores"])
    flg = np.asarray(res["flags"])
    dur = t[-1] + window_s

    print(f"recording : {path}")
    print(f"audio     : {res['fs']} Hz, {len(t)} windows of {window_s}s "
          f"(hop {hop_s}s), {dur:.1f}s total")
    print(f"baseline  : self-referential (no healthy data); directed score, "
          f"threshold = p{threshold_pct:g}")
    print(f"flagged   : {int(flg.sum())} windows ({100 * flg.mean():.1f}%)")

    centers = [tt + window_s / 2 for tt in t[flg]]
    events = cluster(centers, gap_s)
    print(f"\nanomaly events (>{gap_s:g}s apart): {len(events)} — candidate track errors")
    print(f"{'#':>3}  {'t_center':>9}  {'span_s':>7}  {'peak_score':>10}")
    for i, ev in enumerate(events, 1):
        lo, hi = ev[0], ev[-1]
        mask = (t + window_s / 2 >= lo - 1e-6) & (t + window_s / 2 <= hi + 1e-6)
        peak = s[mask].max() if mask.any() else float("nan")
        ctr = 0.5 * (lo + hi)
        print(f"{i:>3}  {ctr:>8.1f}s  {hi - lo:>6.1f}s  {peak:>10.3f}")


if __name__ == "__main__":
    wav = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_WAV
    main(wav)
