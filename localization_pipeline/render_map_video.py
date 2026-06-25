"""
render_map_video.py -- verification video: camera frame + live position on the
track loop, driven by a *_track.csv from pipeline.py.

Fixes: full-height map (no clipping), persistent wheel flash, configurable START
turntable (absolute placement), and a rate-limited marker so hard-anchor snaps
ease in instead of teleporting.

Usage:
  python render_map_video.py --frames DIR --track exp2_track.csv --wheels exp2_wheels.csv
       --roi roi.json --start TL --out exp2_localization.mp4
"""
import os, csv, argparse, glob, json
import numpy as np
import cv2

RECT_W, RECT_H = 120.0, 420.0
SEG_CC = [160.0, 460.0, 160.0, 460.0]              # centre-to-centre (straight + turntable)
BOUND = [0.0, 160.0, 620.0, 780.0, 1240.0]
LAP = 1240.0
TT_CUM = {"BR": 0.0, "BL": 160.0, "TL": 620.0, "TR": 780.0}
CORNERS = {"BR": (120, 0), "BL": (0, 0), "TL": (0, 420), "TR": (120, 420)}


def cum_to_xy(p):
    p %= LAP
    if p < BOUND[1]:  f = (p - BOUND[0]) / SEG_CC[0]; return 120 - 120 * f, 0.0, "bottom (BR->BL)"
    if p < BOUND[2]:  f = (p - BOUND[1]) / SEG_CC[1]; return 0.0, 420 * f, "left (BL->TL)"
    if p < BOUND[3]:  f = (p - BOUND[2]) / SEG_CC[2]; return 120 * f, 420.0, "top (TL->TR)"
    f = (p - BOUND[3]) / SEG_CC[3]; return 120.0, 420 - 420 * f, "right (TR->BR)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", required=True)
    ap.add_argument("--track", required=True)
    ap.add_argument("--wheels", required=True)
    ap.add_argument("--roi", default="roi.json")
    ap.add_argument("--start", default="BR", choices=list(TT_CUM))
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=float, default=20.0)
    args = ap.parse_args()
    HERE = os.path.dirname(os.path.abspath(__file__))

    roi = json.load(open(args.roi if os.path.isabs(args.roi) else os.path.join(HERE, args.roi)))
    poly = np.array(roi["polygon"], np.int32)
    offset = 0.0                                     # pipeline cum is already absolute (BR-frame)

    rows = list(csv.DictReader(open(args.track)))
    wheel_frames = sorted(int(r["frame"]) for r in csv.DictReader(open(args.wheels)))
    wf_set = set(wheel_frames)
    # turntable flashes: {frame -> deg}
    tt = {}
    apath = args.track.replace("_track.csv", "_anchors.csv")
    if os.path.exists(apath):
        for r in csv.DictReader(open(apath)):
            tt[int(r["frame"])] = float(r["deg"]) if r.get("deg") not in (None, "") else 0.0
    fs = sorted(glob.glob(os.path.join(args.frames, "*.jpg")))
    assert len(fs) == len(rows)

    # rate-limited displayed cum (ease snaps over ~0.5 s instead of teleporting)
    cum = np.array([float(r["cum_cm"]) for r in rows])
    disp = np.empty_like(cum); disp[0] = cum[0]
    MAXSTEP = 25.0
    for i in range(1, len(cum)):
        d = cum[i] - disp[i - 1]
        disp[i] = disp[i - 1] + np.clip(d, -MAXSTEP, MAXSTEP)

    # layout: left = camera(320x240) + status(320x230); right = map(360 x 470)
    CAM_W, CAM_H = 320, 240
    MAP_W, H = 360, 470
    W = CAM_W + MAP_W
    sc = (H - 60) / RECT_H                            # px per cm (full height)
    ox = CAM_W + (MAP_W - RECT_W * sc) / 2; oy = 30
    def to_px(cx, cy):
        return int(ox + cx * sc), int(oy + (RECT_H - cy) * sc)

    vw = cv2.VideoWriter(args.out, cv2.VideoWriter_fourcc(*"mp4v"), args.fps, (W, H))
    wheel_count = 0; flash = 0; trail = []; tt_flash = 0; tt_deg = 0.0; tt_count = 0
    for i, (f, r) in enumerate(zip(fs, rows)):
        canvas = np.full((H, W, 3), 25, np.uint8)
        img = cv2.imread(f)
        cv2.polylines(img, [poly], True, (0, 255, 255), 1)
        canvas[0:CAM_H, 0:CAM_W] = img
        if int(r["frame"]) in wf_set:
            wheel_count += 1; flash = 10
        if flash > 0:
            cv2.rectangle(canvas, (0, 0), (CAM_W - 1, CAM_H - 1), (0, 255, 0), 3)
            cv2.putText(canvas, f"WHEEL #{wheel_count}", (90, 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            flash -= 1
        if int(r["frame"]) in tt:
            tt_flash = 18; tt_deg = tt[int(r["frame"])]; tt_count += 1
        if tt_flash > 0:
            cv2.rectangle(canvas, (0, 0), (CAM_W - 1, CAM_H - 1), (0, 140, 255), 5)
            turn = "LEFT" if tt_deg > 0 else "RIGHT"
            cv2.putText(canvas, f"TURNTABLE #{tt_count}: TURN {turn} {tt_deg:+.0f}deg",
                        (10, CAM_H - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 140, 255), 2)
            tt_flash -= 1

        # ---- map ----
        pts = [to_px(*CORNERS[c]) for c in ["BR", "BL", "TL", "TR"]]
        cv2.polylines(canvas, [np.array(pts, np.int32)], True, (170, 170, 170), 3, cv2.LINE_AA)
        for name, (cx, cy) in CORNERS.items():
            px, py = to_px(cx, cy)
            on = (args.start == name)
            cv2.circle(canvas, (px, py), 11, (0, 200, 255) if not on else (60, 220, 60), -1, cv2.LINE_AA)
            lx = px - 52 if cx == 0 else px + 16
            cv2.putText(canvas, name + (" (1st TT)" if on else ""), (lx, py + 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
        # start position (where the box began, before the 1st turntable)
        sx, sy, _ = cum_to_xy(float(rows[0]["cum_cm"]))
        spx, spy = to_px(sx, sy)
        cv2.circle(canvas, (spx, spy), 7, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(canvas, "start", (spx + 10, spy + 4), cv2.FONT_HERSHEY_SIMPLEX,
                    0.4, (255, 255, 255), 1, cv2.LINE_AA)
        # marker + trail (shifted to START frame)
        x, y, _ = cum_to_xy(disp[i] + offset)
        px, py = to_px(x, y)
        trail.append((px, py)); trail = trail[-40:]
        for k in range(1, len(trail)):
            cv2.line(canvas, trail[k - 1], trail[k], (0, 140, 0), 2, cv2.LINE_AA)
        col = (60, 255, 60) if int(r["moving"]) else (60, 60, 255)
        cv2.circle(canvas, (px, py), 9, col, -1, cv2.LINE_AA)
        cv2.circle(canvas, (px, py), 9, (255, 255, 255), 1, cv2.LINE_AA)

        # ---- status (under camera) ----
        pos_i = disp[i] + offset
        _, _, seg = cum_to_xy(pos_i)
        lines = [f"frame {i}/{len(fs)}    t = {float(r['t_rel_s']):.1f} s",
                 f"position: {pos_i/100:5.1f} m   ({pos_i/LAP:.2f} laps)",
                 f"segment:  {seg}",
                 f"wheels:   {wheel_count}",
                 ("MOVING" if int(r["moving"]) else "STOPPED")]
        for j, t in enumerate(lines):
            c = (0, 255, 0) if (j == 4 and int(r["moving"])) else \
                ((0, 0, 255) if j == 4 else (235, 235, 235))
            cv2.putText(canvas, t, (12, CAM_H + 34 + j * 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, c, 2, cv2.LINE_AA)
        cv2.putText(canvas, "TRACK (clockwise) - position route-assumed", (int(ox) - 60, 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (160, 160, 160), 1, cv2.LINE_AA)
        cv2.putText(canvas, f"turntables seen: {tt_count}", (CAM_W + 12, H - 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 140, 255), 1, cv2.LINE_AA)

        vw.write(canvas)
        if i % 600 == 0:
            print(f"  {i}/{len(fs)}")
    vw.release()
    print("wrote", args.out)


if __name__ == "__main__":
    main()
