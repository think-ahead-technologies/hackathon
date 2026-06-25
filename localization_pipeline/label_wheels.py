"""
Tap-to-label ground-truth tool for big-wheel counting (interval version).

Mark EVERY frame a wheel is visible; contiguous marked frames are grouped into
one wheel (an interval). Also mark the END of each segment (its 4th wheel) so we
can cross-check that every segment has exactly 4 wheels.

Run:
    python label_wheels.py
    python label_wheels.py --start 2550 --end 3100   # label one stretch
    python label_wheels.py --group-gap 1             # bridge <=1 missed frame

Controls
--------
  space            play / pause
  r                toggle RECORD mode -> every frame you land on is marked as
                   wheel-present (best: hit r when a wheel appears, play/step
                   through it, hit r again when it's gone)
  w  (or  m / UP)  toggle wheel-present on the CURRENT frame (single frame)
  e                toggle SEGMENT-END (this frame's wheel is the 4th of a segment)
  d / right        step +1   (A/D = +/-25)
  a / left         step -1
  . / ,            faster / slower playback
  [ / ]            group-gap -1 / +1  (max missed frames inside one wheel)
  z                undo last wheel-frame added
  c                clear ALL marks
  s                save wheels_truth.json
  q / ESC          save + quit

Live readout shows grouped wheel count and segment-ends, and flags when
wheels != 4 x (segment-ends) so you can spot a miss while labeling.
"""
import argparse, glob, json, os
import cv2, numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--dir", default="merged_20260623_17xx_frames")
ap.add_argument("--roi", default="roi.json")
ap.add_argument("--out", default="wheels_truth.json")
ap.add_argument("--start", type=int, default=0)
ap.add_argument("--end", type=int, default=-1)
ap.add_argument("--group-gap", type=int, default=1,
                help="max consecutive UNMARKED frames still kept inside one wheel")
args = ap.parse_args()

frame_dir = args.dir if os.path.isabs(args.dir) else os.path.join(HERE, args.dir)
files = sorted(glob.glob(os.path.join(frame_dir, "*.jpg")))
assert files, f"no frames in {frame_dir}"
lo = max(0, args.start)
hi = len(files) - 1 if args.end < 0 else min(len(files) - 1, args.end)

roi = json.load(open(os.path.join(HERE, args.roi)))
poly = np.array(roi["polygon"], np.int32)
dark_thresh = int(roi.get("thresh", 60))
W, H = roi.get("frame_size", [320, 240])
mask = np.zeros((H, W), np.uint8); cv2.fillPoly(mask, [poly], 255)
npix = int((mask > 0).sum())

out_path = args.out if os.path.isabs(args.out) else os.path.join(HERE, args.out)
wheel_frames = set()        # every frame where a wheel is visible
seg_end_frames = set()      # frames that are the 4th wheel of a segment
spans = []                  # [[lo,hi], ...] labeled ranges
group_gap = args.group_gap
add_order = []              # for undo

if os.path.exists(out_path):
    try:
        prev = json.load(open(out_path))
        # new format, else fall back to old point-marks ("frames")
        wf = prev.get("wheel_frames", prev.get("frames", []))
        wheel_frames = set(int(x) for x in wf)
        seg_end_frames = set(int(x) for x in prev.get("segment_end_frames", []))
        spans = [list(s) for s in prev.get("spans", [])]
        print(f"Loaded {len(wheel_frames)} wheel-frames, {len(seg_end_frames)} "
              f"segment-ends, {len(spans)} spans from {out_path}")
    except Exception as e:
        print("could not load existing labels:", e)


def merge_spans(sp):
    sp = sorted([list(s) for s in sp])
    out = []
    for s in sp:
        if out and s[0] <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], s[1])
        else:
            out.append(list(s))
    return out


def group(frames, gap):
    """Group sorted frames into [start,end] wheels (bridge gaps <= gap)."""
    fs = sorted(frames)
    groups = []
    for f in fs:
        if groups and f - groups[-1][1] <= gap + 1:
            groups[-1][1] = f
        else:
            groups.append([f, f])
    return groups


def save():
    groups = group(wheel_frames, group_gap)
    all_spans = merge_spans(spans + [[lo, hi]])
    centers = [int(round((a + b) / 2)) for a, b in groups]   # back-compat point marks
    json.dump({"wheel_frames": sorted(wheel_frames),
               "segment_end_frames": sorted(seg_end_frames),
               "groups": groups, "group_gap": group_gap,
               "frames": centers, "count": len(groups),
               "spans": all_spans, "dir": args.dir},
              open(out_path, "w"), indent=2)
    print(f"saved {len(groups)} wheels ({len(wheel_frames)} frames), "
          f"{len(seg_end_frames)} segment-ends, {len(all_spans)} span(s) -> {out_path}")


idx = lo
playing = False
recording = False
delay = 80
SCALE = 3
WIN = "label wheels  (r=record  w=wheel  e=seg-end  s=save  q=quit)"
cv2.namedWindow(WIN)

while True:
    frame = cv2.imread(files[idx])
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    dark = (gray < dark_thresh) & (mask > 0)
    dfrac = dark.sum() / npix

    if recording:
        if idx not in wheel_frames:
            wheel_frames.add(idx); add_order.append(idx)

    disp = frame.copy()
    disp[dark] = (0, 0, 255)
    cv2.polylines(disp, [poly], True, (0, 255, 0), 1)
    disp = cv2.resize(disp, (W * SCALE, H * SCALE), interpolation=cv2.INTER_NEAREST)

    if idx in wheel_frames:
        cv2.rectangle(disp, (0, 0), (disp.shape[1] - 1, disp.shape[0] - 1),
                      (0, 255, 255), 5)
    if idx in seg_end_frames:
        cv2.rectangle(disp, (3, 3), (disp.shape[1] - 4, disp.shape[0] - 4),
                      (255, 0, 255), 5)
    if recording:
        cv2.putText(disp, "REC", (disp.shape[1] - 70, 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

    groups = group(wheel_frames, group_gap)
    nseg = len(seg_end_frames)
    expect = 4 * nseg
    mismatch = nseg > 0 and len(groups) != expect

    panel = np.full((96, disp.shape[1], 3), 30, np.uint8)
    state = "PLAY" if playing else "PAUSE"
    cv2.putText(panel, f"frame {idx}/{hi}  [{state} {1000//delay}fps]  DARK {dfrac*100:3.0f}%"
                f"  gap<= {group_gap}", (8, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                (255, 255, 255), 1)
    cv2.putText(panel, f"WHEELS (grouped): {len(groups)}    segment-ends: {nseg}"
                f"  (expect {expect} wheels)", (8, 56), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                (0, 255, 0), 2)
    msg = ("MISMATCH: wheels != 4 x segments -> check for miss" if mismatch
           else "wheel-frames: %d" % len(wheel_frames))
    cv2.putText(panel, msg, (8, 84), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (0, 0, 255) if mismatch else (180, 180, 180), 1 if not mismatch else 2)
    cv2.imshow(WIN, np.vstack([disp, panel]))

    key = cv2.waitKey(delay if playing else 0) & 0xFF
    if playing and idx < hi:
        idx += 1
    elif playing:
        playing = False

    if key in (ord('q'), 27):
        save(); break
    elif key == ord(' '):
        playing = not playing
    elif key == ord('r'):
        recording = not recording
        if recording and idx not in wheel_frames:
            wheel_frames.add(idx); add_order.append(idx)
    elif key in (ord('w'), ord('m'), 82):
        if idx in wheel_frames:
            wheel_frames.discard(idx)
        else:
            wheel_frames.add(idx); add_order.append(idx)
    elif key == ord('e'):
        if idx in seg_end_frames:
            seg_end_frames.discard(idx)
        else:
            seg_end_frames.add(idx)
    elif key in (ord('d'), 83):
        idx = min(hi, idx + 1)
    elif key == ord('D'):
        idx = min(hi, idx + 25)
    elif key in (ord('a'), 81):
        idx = max(lo, idx - 1)
    elif key == ord('A'):
        idx = max(lo, idx - 25)
    elif key == ord('.'):
        delay = max(10, delay - 15)
    elif key == ord(','):
        delay = min(300, delay + 15)
    elif key == ord('['):
        group_gap = max(0, group_gap - 1)
    elif key == ord(']'):
        group_gap = min(10, group_gap + 1)
    elif key == ord('z'):
        while add_order:
            f = add_order.pop()
            if f in wheel_frames:
                wheel_frames.discard(f); break
    elif key == ord('c'):
        wheel_frames.clear(); seg_end_frames.clear(); add_order.clear()
        print("  cleared all marks")
    elif key == ord('s'):
        save()

cv2.destroyAllWindows()
