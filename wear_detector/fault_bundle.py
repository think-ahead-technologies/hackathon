# ABOUTME: Build an inspection bundle — per detected fault event, its camera frame + a short audio clip.
# ABOUTME: Lets a human eyeball/listen to each anomaly to judge what fault (or handling) it really is.
import os
import sys
import wave

import numpy as np

from wear_detector import frames


def write_clip(wav_in, out_path, t0, t1):
    """Write the [t0, t1] second slice of wav_in to out_path, preserving format. Returns secs written."""
    with wave.open(str(wav_in), "rb") as w:
        fs = w.getframerate()
        nframes = w.getnframes()
        params = w.getparams()
        a = max(0, int(t0 * fs))
        b = min(nframes, int(t1 * fs))
        w.setpos(a)
        data = w.readframes(max(0, b - a))
    with wave.open(str(out_path), "wb") as o:
        o.setnchannels(params.nchannels)
        o.setsampwidth(params.sampwidth)
        o.setframerate(fs)
        o.writeframes(data)
    return (b - a) / fs


def build(csv_path, wav_path, frames_src, out_dir, pre_s=1.5, post_s=1.5, gate=True,
          max_squeals=12):
    """Drop a frame + audio clip per fault into out_dir, for BOTH fault types:
    broadband-energy events (motion-gated) and tonal squeal/whine events.

    Returns the per-event records and writes an index.md. `max_squeals` caps the (often many)
    tonal events to the strongest by tonal score so the bundle stays inspectable.
    """
    from wear_detector import tonal

    os.makedirs(out_dir, exist_ok=True)
    index = frames.parse_frame_index(frames_src)
    origin = frames.recording_origin_us(csv_path)

    # Two detectors, two fault families. (t, type, detail-string, sort-key)
    energy = [(t, "energy", f"score={sc:.3f}") for t, sc in
              frames.anomaly_event_times(wav_path, gate_csv=(csv_path if gate else None))]
    squeals = tonal.detect_tonal(wav_path)["events"]
    squeals = sorted(squeals, key=lambda e: -e["tonal_score"])[:max_squeals]
    squeal_ev = [(e["t"], "squeal", f"{e['pitch_hz']:.0f}Hz (tonal {e['tonal_score']:.0f})")
                 for e in squeals]
    events = sorted(energy + squeal_ev, key=lambda x: x[0])

    records = []
    for i, (t, ftype, detail) in enumerate(events, 1):
        fr, dt = frames.nearest_frame(index, frames.audio_time_to_host_us(t, origin))
        img_name = f"event{i:02d}_{ftype}_t{t:06.1f}s_img.jpg"
        clip_name = f"event{i:02d}_{ftype}_t{t:06.1f}s_audio.wav"
        frames.extract_frames(frames_src, [fr[2]], out_dir)
        os.replace(os.path.join(out_dir, os.path.basename(fr[2])),
                   os.path.join(out_dir, img_name))
        write_clip(wav_path, os.path.join(out_dir, clip_name), t - pre_s, t + post_s)
        records.append({"i": i, "t": t, "type": ftype, "detail": detail,
                        "frame_dt": dt, "img": img_name, "clip": clip_name})

    # Location by camera view: faults whose frames look alike are at the same spot on the line.
    # Optional — only if Pillow is available; absence just omits the column.
    try:
        from wear_detector.fault_location import locate
        labels = locate([os.path.join(out_dir, r["img"]) for r in records])
        for r, lab in zip(records, labels):
            r["location"] = f"L{lab}"
    except Exception:
        for r in records:
            r["location"] = "?"

    n_energy = sum(1 for r in records if r["type"] == "energy")
    n_squeal = sum(1 for r in records if r["type"] == "squeal")
    lines = ["# Fault inspection bundle\n",
             f"Source: `{os.path.basename(wav_path)}`  |  {n_energy} broadband-energy events"
             f"{' (motion-gated)' if gate else ''} + {n_squeal} tonal squeal events\n",
             "Each event: a camera frame and a "
             f"{pre_s + post_s:.0f}s audio clip centred on it. `type` is the detector that fired "
             "(energy = broadband impact/grind, squeal = narrowband high-pitched tone).\n",
             "`location` groups faults whose camera view matches — same label = same spot on the line.\n",
             "| # | time (s) | type | detail | location | image | audio |",
             "|---|---|---|---|---|---|---|"]
    for r in records:
        lines.append(f"| {r['i']} | {r['t']:.1f} | {r['type']} | {r['detail']} "
                     f"| {r.get('location','?')} | `{r['img']}` | `{r['clip']}` |")
    with open(os.path.join(out_dir, "index.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return records


def main(csv_path, wav_path, frames_src, out_dir):
    records = build(csv_path, wav_path, frames_src, out_dir)
    n_e = sum(1 for r in records if r["type"] == "energy")
    n_s = sum(1 for r in records if r["type"] == "squeal")
    print(f"bundle: {len(records)} events ({n_e} energy + {n_s} squeal) -> {out_dir}")
    for r in records:
        print(f"  event{r['i']:02d}  t={r['t']:6.1f}s  {r['type']:>6}  {r['detail']:<24}  "
              f"loc={r.get('location','?')}")


if __name__ == "__main__":
    main(*sys.argv[1:5])
