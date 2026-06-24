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


def build(csv_path, wav_path, frames_src, out_dir, pre_s=1.5, post_s=1.5, gate=True):
    """For each (motion-gated) acoustic fault event, drop a frame + audio clip into out_dir.

    Returns the list of per-event records. Also writes an index.md describing every event.
    """
    os.makedirs(out_dir, exist_ok=True)
    events = frames.anomaly_event_times(wav_path, gate_csv=(csv_path if gate else None))
    index = frames.parse_frame_index(frames_src)
    origin = frames.recording_origin_us(csv_path)

    records = []
    for i, (t, score) in enumerate(events, 1):
        fr, dt = frames.nearest_frame(index, frames.audio_time_to_host_us(t, origin))
        img_name = f"event{i:02d}_t{t:06.1f}s_img.jpg"
        clip_name = f"event{i:02d}_t{t:06.1f}s_audio.wav"
        frames.extract_frames(frames_src, [fr[2]], out_dir)
        os.replace(os.path.join(out_dir, os.path.basename(fr[2])),
                   os.path.join(out_dir, img_name))
        secs = write_clip(wav_path, os.path.join(out_dir, clip_name),
                          t - pre_s, t + post_s)
        records.append({"i": i, "t": t, "score": score, "frame_dt": dt,
                        "img": img_name, "clip": clip_name, "clip_s": secs})

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

    lines = [f"# Fault inspection bundle\n",
             f"Source: `{os.path.basename(wav_path)}`  |  {len(records)} events"
             f"{' (motion-gated)' if gate else ''}\n",
             "Each event: a camera frame at the anomaly and a "
             f"{pre_s + post_s:.0f}s audio clip centred on it.\n",
             "`location` groups faults whose camera view matches — same label = same spot on the line.\n",
             "| # | time (s) | acoustic score | location | frame Δt (s) | image | audio |",
             "|---|---|---|---|---|---|---|"]
    for r in records:
        lines.append(f"| {r['i']} | {r['t']:.1f} | {r['score']:.3f} | {r.get('location','?')} "
                     f"| {r['frame_dt']:+.2f} | `{r['img']}` | `{r['clip']}` |")
    with open(os.path.join(out_dir, "index.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return records


def main(csv_path, wav_path, frames_src, out_dir):
    records = build(csv_path, wav_path, frames_src, out_dir)
    print(f"bundle: {len(records)} events -> {out_dir}")
    for r in records:
        print(f"  event{r['i']:02d}  t={r['t']:6.1f}s  score={r['score']:.3f}  "
              f"loc={r.get('location','?')}  {r['img']} + {r['clip']}")


if __name__ == "__main__":
    main(*sys.argv[1:5])
