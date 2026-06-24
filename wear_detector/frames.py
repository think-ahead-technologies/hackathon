# ABOUTME: Correlate acoustic anomalies to camera frames — map audio time to the shared host clock.
# ABOUTME: Frame names carry a host_us stamp; the IMU CSV pins host_us = 1e6*t_rel + recording origin.
import csv
import os
import re
import sys
import zipfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wear_detector import audio
from wear_detector.audio_eval import cluster

# Recorder frame name: ..._f<frame_no>_<host_us>.jpg
_FRAME_RX = re.compile(r"_f(\d+)_(\d+)\.jpg$")

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DEFAULT_ZIP = os.path.join(DATA, "test1", "merged_20260623_17xx_frames.zip")
DEFAULT_CSV = os.path.join(DATA, "test1", "merged_20260623_17xx.csv")
DEFAULT_WAV = os.path.join(DATA, "test1", "merged_20260623_17xx.wav")


def parse_frame_index(zip_path):
    """Return [(frame_no, host_us, name), ...] sorted by host_us, from a frames zip."""
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.endswith(".jpg")]
    out = []
    for n in names:
        m = _FRAME_RX.search(n)
        if m:
            out.append((int(m.group(1)), int(m.group(2)), n))
    out.sort(key=lambda x: x[1])
    return out


def recording_origin_us(csv_path):
    """Host clock (µs) at recording start, t_rel_s = 0: median(t_host_us - 1e6*t_rel_s).

    The merged CSV carries both clocks; their offset is the shared origin the frame
    host stamps are measured against, so audio/IMU/video all land on one timeline.
    """
    host, rel = [], []
    with open(csv_path) as fh:
        for row in csv.DictReader(fh):
            host.append(int(row["t_host_us"]))
            rel.append(float(row["t_rel_s"]))
    host = np.asarray(host, dtype=np.float64)
    rel = np.asarray(rel, dtype=np.float64)
    return float(np.median(host - 1e6 * rel))


def audio_time_to_host_us(t_audio, origin_us):
    """Audio seconds-from-start -> absolute host µs (audio start == recording start)."""
    return origin_us + np.asarray(t_audio, dtype=np.float64) * 1e6


def nearest_frame(frames, host_us):
    """(frame, dt_s) for the frame closest to host_us; dt_s = frame_time - host_us."""
    ts = np.array([f[1] for f in frames], dtype=np.float64)
    i = int(np.argmin(np.abs(ts - host_us)))
    return frames[i], float((ts[i] - host_us) / 1e6)


def correlate_events(frames, origin_us, event_times_audio):
    """Match each audio event time to its nearest camera frame."""
    out = []
    for t in event_times_audio:
        fr, dt = nearest_frame(frames, audio_time_to_host_us(t, origin_us))
        out.append({"t_audio": float(t), "frame_no": fr[0],
                    "frame_name": fr[2], "dt_s": dt})
    return out


def extract_frames(zip_path, names, out_dir):
    """Extract the named frames (flat, by basename) into out_dir; return written paths."""
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    with zipfile.ZipFile(zip_path) as z:
        for n in names:
            dst = os.path.join(out_dir, os.path.basename(n))
            with open(dst, "wb") as fh:
                fh.write(z.read(n))
            paths.append(dst)
    return paths


def anomaly_event_times(wav_path, window_s=0.5, hop_s=0.25, threshold_pct=97.0, gap_s=3.0):
    """Peak-score time of each flagged anomaly cluster (audio seconds from start)."""
    res = audio.detect_session(wav_path, window_s=window_s, hop_s=hop_s,
                               threshold_pct=threshold_pct)
    t = np.asarray(res["times"]) + window_s / 2.0
    s = np.asarray(res["scores"])
    flagged = sorted(t[np.asarray(res["flags"])])
    events = []
    for grp in cluster(flagged, gap_s):
        lo, hi = grp[0], grp[-1]
        mask = (t >= lo - 1e-6) & (t <= hi + 1e-6)
        peak_t = t[mask][int(np.argmax(s[mask]))]
        events.append((float(peak_t), float(s[mask].max())))
    return events


def main(zip_path=DEFAULT_ZIP, csv_path=DEFAULT_CSV, wav_path=DEFAULT_WAV, out_dir=None):
    frames = parse_frame_index(zip_path)
    origin = recording_origin_us(csv_path)
    events = anomaly_event_times(wav_path)
    times = [t for t, _ in events]
    matches = correlate_events(frames, origin, times)

    span_s = (frames[-1][1] - frames[0][1]) / 1e6
    print(f"frames : {len(frames)} over {span_s:.1f}s "
          f"(~{len(frames)/span_s:.1f} fps); origin host_us={int(origin)}")
    print(f"events : {len(events)} acoustic anomalies -> nearest camera frame\n")
    print(f"{'#':>2}  {'t_audio':>8}  {'score':>5}  {'frame#':>7}  {'dt':>7}  file")
    for i, (m, (_, sc)) in enumerate(zip(matches, events), 1):
        print(f"{i:>2}  {m['t_audio']:>7.1f}s  {sc:>5.3f}  {m['frame_no']:>7}  "
              f"{m['dt_s']:>+6.2f}s  {os.path.basename(m['frame_name'])}")

    if out_dir:
        paths = extract_frames(zip_path, [m["frame_name"] for m in matches], out_dir)
        print(f"\nextracted {len(paths)} frames -> {out_dir}")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else None
    main(out_dir=out)
