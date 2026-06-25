"""
pipeline.py -- full localization pipeline for ANY run (parameterized).

Steps:
  1. WHEELS: dark x camera-compensated-motion in the ROI -> wheel detections
     (runs of the dark&moving gate; peak frame each). Same absolute gate as the
     validated run-1 detector (D0/NETTHR), so it transfers without labels.
  2. ANCHORS: IMU gyr_z -> ~90 deg turntable rotations (hard anchors).
  3. STOPS: IMU vibration+gyro energy -> stationary intervals.
  4. POSITION (with the FIX):
       - REJECT wheels detected while the box is IMU-stationary (false-idle wheels).
       - HOLD position during stationary intervals (no phantom advance).
       - cumulative distance = 15 cm per accepted wheel.
       - GEOMETRY SKELETON: the box runs a fixed CLOCKWISE loop with segments
         [bottom 120, left 420, top 120, right 420] (lap 1080 cm); turntables sit
         at cum-dist {120,540,660,1080,...}. At each DETECTED turntable we SNAP
         cum to the nearest such position -> hard-anchor reset that also recovers
         drift even when the IMU briefly froze.
  5. LOO: leave-one-wheel-out (speed-integration predictor, clean triplets) ->
     max position error.

Usage:
  python pipeline.py --frames DIR --imu CSV --roi roi.json --out PREFIX [--frame-glob '*.jpg']
"""
import os, glob, re, csv, json, argparse
import numpy as np
import cv2
import track as TK

CM = 15.0
TT_LEN = 40.0     # a turntable adds ~40cm to a leg (half on each end where the
                  # box is centred): bottom straight 120 -> 160 centre-to-centre.
# centre-to-centre leg lengths (clockwise BR->BL->TL->TR), straight + turntable
SEG_CC = [120 + TT_LEN, 420 + TT_LEN, 120 + TT_LEN, 420 + TT_LEN]  # 160,460,160,460
BOUND = np.cumsum([0.0] + SEG_CC)                  # [0,160,620,780,1240]
LAP = float(BOUND[-1])                              # 1240 cm
TT_CUM = BOUND[:-1]                                 # turntable centres: 0,160,620,780


def frame_list(frames, pat):
    fs = sorted(glob.glob(os.path.join(frames, pat)))
    ts = np.array([int(re.search(r"_(\d+)\.jpg$", f).group(1)) for f in fs], np.int64) / 1e6
    return fs, ts


def load_roi(path):
    roi = json.load(open(path))
    poly = np.array(roi["polygon"], np.int32)
    x0, y0 = poly[:, 0].min(), poly[:, 1].min()
    x1, y1 = poly[:, 0].max(), poly[:, 1].max()
    size = tuple(roi.get("frame_size", [320, 240]))
    full = np.zeros(size[::-1], np.uint8); cv2.fillPoly(full, [poly], 1)
    mask = full[y0:y1 + 1, x0:x1 + 1].astype(bool)
    return dict(bbox=(int(x0), int(y0), int(x1), int(y1)),
                thresh=int(roi.get("thresh", 60)), mask=mask, n=int(mask.sum()))


def detect_wheels(fs, ts, roi, HANG_S=1.0, D0=0.4, NETTHR=0.5, PAD=1, rel_k=0.6):
    # rel_k>0 -> ADAPTIVE threshold = rel_k * frame-median brightness (robust to
    # the camera's auto-exposure drift; the wheel hub is darker than the frame
    # regardless of absolute level). rel_k=0 falls back to the fixed roi thresh.
    x0, y0, x1, y1 = roi["bbox"]; thr = roi["thresh"]; mask = roi["mask"]; n = roi["n"]
    dt = np.diff(ts, prepend=ts[0])
    N = len(fs); dark = np.zeros(N); net = np.zeros(N)
    prev_roi = prev_full = None
    for i, f in enumerate(fs):
        img = cv2.imread(f, cv2.IMREAD_GRAYSCALE)
        r = img[y0:y1 + 1, x0:x1 + 1].astype(np.float32)
        t_i = rel_k * float(np.median(img)) if rel_k else thr   # brightness-adaptive
        dark[i] = float((r[mask] < t_i).mean()) if n else 0.0
        if prev_roi is not None and dt[i] <= HANG_S:
            rm = float(np.abs(r - prev_roi)[mask].mean())
            gm = float(np.median(np.abs(img.astype(np.float32) - prev_full)))
            net[i] = max(0.0, rm - gm)
        prev_roi, prev_full = r, img.astype(np.float32)
    # run-level AND of dark & moving -> gate
    gate = np.zeros(N); b = dark > D0; i = 0
    while i < N:
        if b[i]:
            j = i
            while j < N and b[j]:
                j += 1
            if net[i:j].max() >= NETTHR:
                gate[max(0, i - PAD):min(N, j + PAD)] = 1.0
            i = j
        else:
            i += 1
    # wheels = contiguous gate runs; peak = max dark
    wheels = []; i = 0
    while i < N:
        if gate[i]:
            j = i
            while j < N and gate[j]:
                j += 1
            pk = i + int(np.argmax(dark[i:j]))
            wheels.append(pk)
            i = j
        else:
            i += 1
    return np.array(wheels), dark, gate


def load_imu(csv_path):
    rows = list(csv.DictReader(open(csv_path)))
    th = np.array([float(r["t_host_us"]) for r in rows]) / 1e6
    gz = np.array([float(r["gyr_z_dps"]) for r in rows])
    ax = np.array([float(r["acc_x_g"]) for r in rows])
    ay = np.array([float(r["acc_y_g"]) for r in rows])
    mx = np.array([float(r["mag_x_ut"]) for r in rows])
    my = np.array([float(r["mag_y_ut"]) for r in rows])
    gm = np.sqrt(np.array([float(r["gyr_x_dps"]) for r in rows]) ** 2 +
                 np.array([float(r["gyr_y_dps"]) for r in rows]) ** 2 + gz ** 2)
    return th, gz, ax, ay, gm, mx, my


def recover_missed_turns(th, gz, mx, my, anchor_times, gap_s=0.8, step_deg=70.0):
    """Turntable turns during an IMU data gap are invisible to the gyro (it
    integrates RATE, so no samples = no turn). The magnetometer reads ABSOLUTE
    orientation, so a turn during the gap shows as a STEP in mag heading. We
    HARD-IRON CALIBRATE first (least-squares circle fit on mx,my; the box carries
    a large magnetic offset that otherwise compresses a 90deg turn to ~15deg) so
    a real turn reads ~90-145deg and mag perturbations stay <~40deg -> a 70deg
    threshold cleanly separates real missed turns from noise. Recover a turn at a
    gap with a big mag step AND ~no gyro rotation."""
    A = np.c_[2 * mx, 2 * my, np.ones(len(mx))]
    cx, cy, c = np.linalg.lstsq(A, mx ** 2 + my ** 2, rcond=None)[0]
    mxc, myc = mx - cx, my - cy                        # hard-iron corrected
    hd = TK.integrate_yaw(th, gz)
    def mhead(a, b):
        m = (th >= a) & (th <= b)
        return np.degrees(np.arctan2(np.median(myc[m]), np.median(mxc[m]))) if m.sum() > 3 else None
    def cdiff(a, b):
        return (a - b + 180) % 360 - 180
    dt = np.diff(th); rec = []
    for i in np.where(dt > gap_s)[0]:
        ta, tb = th[i], th[i + 1]
        # dedup vs gyro anchors within 4s (real consecutive turns are >=5s apart,
        # so a nearby gyro turn = the SAME turn the gyro already caught)
        if any(min(abs(a - ta), abs(a - tb), abs(a - 0.5 * (ta + tb))) < 4 for a in anchor_times):
            continue
        bef, aft = mhead(ta - 3, ta - 0.2), mhead(tb + 0.2, tb + 3)
        if bef is None or aft is None:
            continue
        m = (th >= ta - 1) & (th <= tb + 1)
        gyr = hd[m][-1] - hd[m][0] if m.sum() > 1 else 0
        if abs(cdiff(aft, bef)) > step_deg and abs(gyr) < 40:
            rec.append(0.5 * (ta + tb))
    return rec


def motion_state(th, ax, ay, win=0.40, lo=0.025, hi=0.060,
                 min_stop=2.0, min_move=1.5, dt=0.05):
    """Stable stopped/moving signal: rolling horizontal-accel vibration energy +
    Schmitt hysteresis (lo/hi) + minimum-dwell debounce. Returns (grid, stopped)
    where stopped[k] is True when the box is stationary at grid time k. This
    replaces the spotty single-threshold test (184 -> ~40 state changes)."""
    grid = np.arange(th.min(), th.max(), dt)
    e = np.zeros(len(grid))
    for i, g in enumerate(grid):
        a = np.searchsorted(th, g - win); b = np.searchsorted(th, g + win)
        if b - a > 2:
            e[i] = np.sqrt(ax[a:b].std() ** 2 + ay[a:b].std() ** 2)
    stopped = np.zeros(len(e), bool); cur = False
    for i, v in enumerate(e):                       # hysteresis
        if cur and v > hi:
            cur = False
        elif (not cur) and v < lo:
            cur = True
        stopped[i] = cur
    for _ in range(3):                              # debounce short runs
        i = 0
        while i < len(stopped):
            j = i
            while j < len(stopped) and stopped[j] == stopped[i]:
                j += 1
            mind = min_stop if stopped[i] else min_move
            if (j - i) * dt < mind and 0 < i and j < len(stopped):
                stopped[i:j] = stopped[i - 1]
            i = j
    return grid, stopped


def make_stopped_fn(grid, stopped):
    def at(t):
        k = int(np.clip(np.searchsorted(grid, t), 0, len(stopped) - 1))
        return bool(stopped[k])
    return at


# --------- LOO (speed-integration, clean triplets) ----------
_trapz = np.trapezoid if hasattr(np, "trapezoid") else np.trapz


def predict_speed(ts, ss, tb):
    o = np.argsort(ts); ts, ss = ts[o], ss[o]
    tm = 0.5 * (ts[1:] + ts[:-1]); vg = np.diff(ss) / np.diff(ts)
    A = np.where(ts < tb)[0]; A = A[-1] if len(A) else 0
    grid = np.linspace(ts[A], tb, 48); V = np.interp(grid, tm, vg)
    return float(ss[A] + _trapz(V, grid))


def loo(wt, period):
    """Leave-one-wheel-out on contiguous single-spaced runs only, so the true
    spacing is exactly 15 cm (skip-independent). Predict the held-out middle
    wheel by speed-integration of its neighbours; return |pred - truth| in cm."""
    lo, hi = 0.6 * period, 1.7 * period      # a single-wheel gap
    gaps = np.diff(wt)
    single = np.concatenate([[False], (gaps >= lo) & (gaps <= hi)])  # gap into wheel i
    errs = []
    for p in range(1, len(wt) - 1):
        if not (single[p] and single[p + 1]):    # both adjacent gaps single
            continue
        # extend a contiguous single-spaced run around p (exact 15cm grid)
        a = p
        while a > 0 and single[a]:
            a -= 1
        b = p
        while b + 1 < len(wt) and single[b + 1]:
            b += 1
        idx = list(range(a, b + 1))
        keep = [q for q in idx if q != p]
        if len(keep) < 2:
            continue
        ts = wt[keep]
        ss = np.array([15.0 * (q - a) for q in keep])   # exact grid within the run
        pred = predict_speed(ts, ss, wt[p])
        errs.append(abs(pred - 15.0 * (p - a)))
    return np.array(errs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", required=True)
    ap.add_argument("--imu", required=True)
    ap.add_argument("--roi", default="roi.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--frame-glob", default="*.jpg")
    ap.add_argument("--d0", type=float, default=0.4, help="dark-fraction gate")
    ap.add_argument("--netthr", type=float, default=0.5, help="ROI-motion gate")
    ap.add_argument("--rel-k", type=float, default=0.6,
                    help="adaptive dark threshold = rel_k * frame-median (0 = fixed roi thresh)")
    ap.add_argument("--start-tt", default=None, choices=[None, "BR", "BL", "TL", "TR"],
                    help="identity of the FIRST detected turntable (anchor0); enables "
                         "absolute leg-pinned reconstruction (assumes consecutive "
                         "clockwise turntables, i.e. a clean run with no missed turns).")
    args = ap.parse_args()
    HERE = os.path.dirname(os.path.abspath(__file__))

    fs, ts = frame_list(args.frames, args.frame_glob)
    roi = load_roi(args.roi if os.path.isabs(args.roi) else os.path.join(HERE, args.roi))
    print(f"frames {len(fs)}  span {ts.max()-ts.min():.1f}s")
    wheels, dark, gate = detect_wheels(fs, ts, roi, D0=args.d0, NETTHR=args.netthr, rel_k=args.rel_k)
    print(f"raw wheel detections: {len(wheels)} (adaptive thresh rel_k={args.rel_k})")

    th, gz, ax, ay, gm, mx, my = load_imu(args.imu)
    th0 = th - ts.min(); tsr = ts - ts.min()
    ev = TK.detect_turntables(th, gz, step_deg=90, min_rate_dps=15, tol_frac=0.6)
    gyro_anc = [e["t_start"] - ts.min() for e in ev]
    # recover turns the gyro missed during data gaps, via the magnetometer
    rec = recover_missed_turns(th0, gz, mx, my, gyro_anc)
    anchors = np.array(sorted(gyro_anc + rec))
    print(f"turntable anchors: {len(anchors)} ({len(gyro_anc)} gyro + {len(rec)} "
          f"mag-recovered{' @ '+', '.join('%.0fs'%r for r in rec) if rec else ''})")

    # stable stopped/moving signal (hysteresis + debounce)
    mgrid, mstop = motion_state(th0, ax, ay)
    stopped_at = make_stopped_fn(mgrid, mstop)
    n_flips = int((np.diff(mstop.astype(int)) != 0).sum())
    print(f"motion detector: {n_flips} stop/go transitions, stopped {100*mstop.mean():.0f}% of run")

    wt_all = tsr[wheels]
    # FIX 1: reject wheels while IMU-stationary
    accept = np.array([not stopped_at(t) for t in wt_all])
    wt = wt_all[accept]
    print(f"wheels after idle-reject: {len(wt)} (rejected {int((~accept).sum())} idle/false)")

    # belt period (cruise) from accepted wheels
    gaps = np.diff(wt); period = float(np.median(gaps[gaps < 1.0])) if len(gaps) else 0.45
    print(f"wheel period (cruise) {period:.3f}s -> belt {CM/period:.1f} cm/s")

    # raw cumulative distance: +15cm per accepted wheel; idle holds (no wheels)
    raw = np.zeros(len(fs)); wj = 0; c = 0.0
    for i in range(len(fs)):
        while wj < len(wt) and wt[wj] <= tsr[i]:
            c += CM; wj += 1
        raw[i] = c
    raw_anc = np.array([raw[max(0, np.searchsorted(tsr, a, "right") - 1)] for a in anchors])

    if args.start_tt and len(anchors):
        # absolute, leg-pinned reconstruction (clockwise, consecutive turntables)
        order = ["BR", "BL", "TL", "TR"]                    # clockwise neighbour order
        seg_after = {"BR": SEG_CC[0], "BL": SEG_CC[1], "TL": SEG_CC[2], "TR": SEG_CC[3]}
        si = order.index(args.start_tt); cu = float(TT_CUM[si]); true = []; seglen = []
        for k in range(len(anchors)):
            true.append(cu); s = seg_after[order[(si + k) % 4]]; seglen.append(s); cu += s
        true = np.array(true)
        print("\nleg-pin check (raw wheel-dist vs true centre-to-centre segment, "
              f"clockwise from {args.start_tt}):")
        for k in range(len(anchors) - 1):
            extra = raw_anc[k + 1] - raw_anc[k] - seglen[k]
            print(f"  {order[(si+k)%4]}->{order[(si+k+1)%4]}: raw "
                  f"{raw_anc[k+1]-raw_anc[k]:5.0f} cm  vs true {seglen[k]:.0f} cm"
                  f"  (excess {extra:+.0f} = dwell/false wheels, held at turntable)")
        # CAP each leg at its true segment length: the box traverses the segment
        # then DWELLS on the turntable (conveyor still running -> false wheels);
        # capping discards the dwell wheels and parks the marker at the corner.
        pos = np.empty(len(fs))
        for i in range(len(fs)):
            r = raw[i]
            if r <= raw_anc[0]:
                pos[i] = true[0] - min(raw_anc[0] - r, seglen[0])     # before 1st turntable
            elif r >= raw_anc[-1]:
                pos[i] = true[-1] + (r - raw_anc[-1])
            else:
                k = int(np.searchsorted(raw_anc, r, "right") - 1)
                pos[i] = true[k] + min(r - raw_anc[k], seglen[k])     # cap + hold dwell
    else:
        pos = raw.copy()

    track = []
    for i in range(len(fs)):
        x, y, seg = cum_to_xy(pos[i] % LAP)
        moving = 0 if stopped_at(tsr[i]) else 1
        track.append([round(tsr[i], 3), i, round(float(pos[i]), 1), seg,
                      round(x, 1), round(y, 1), moving])
    cum = float(pos[-1])

    err = loo(wt, period)
    loo_max = float(err.max()) if len(err) else float("nan")
    print(f"\nLOO leave-one-wheel-out: n={len(err)}  MAX={loo_max:.1f} cm  "
          f"RMS={np.sqrt((err**2).mean()):.1f}  p95={np.percentile(err,95):.1f}  "
          f"<=5cm={100*(err<=5).mean():.0f}%" if len(err) else "LOO: no clean triplets")

    with open(f"{args.out}_wheels.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["wheel_idx", "frame", "t_rel_s", "peak_file"])
        for k, pk in enumerate(wheels):
            w.writerow([k, int(pk), round(float(tsr[pk]), 3), os.path.basename(fs[pk])])
    with open(f"{args.out}_track.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t_rel_s", "frame", "cum_cm", "segment", "map_x", "map_y", "moving"])
        w.writerows(track)
    gyro_deg = {round(e["t_start"] - ts.min(), 1): round(e["delta_deg"], 0) for e in ev}
    with open(f"{args.out}_anchors.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["anchor", "frame", "t_rel_s", "deg", "source"])
        for k, a in enumerate(anchors):
            fi = min(int(np.searchsorted(tsr, a)), len(fs) - 1)
            deg = gyro_deg.get(round(float(a), 1), "")
            w.writerow([k, fi, round(float(a), 3), deg, "gyro" if deg != "" else "mag"])
    print(f"\nwrote {args.out}_wheels.csv, {args.out}_track.csv")
    print(f"distance travelled {raw[-1]:.0f} cm ({raw[-1]/100:.1f} m, {raw[-1]/LAP:.1f} laps); "
          f"abs position {pos[0]:.0f}->{pos[-1]:.0f} cm")


def cum_to_xy(p):
    """centre-to-centre cum (0..LAP) -> (x,y) on the 120x420 loop rectangle, with
    each leg mapped proportionally so turntable dwells sit exactly at a corner."""
    p %= LAP
    if p < BOUND[1]:                       # bottom BR->BL
        f = (p - BOUND[0]) / SEG_CC[0]; return 120 - 120 * f, 0.0, "bottom"
    if p < BOUND[2]:                       # left BL->TL
        f = (p - BOUND[1]) / SEG_CC[1]; return 0.0, 420 * f, "left"
    if p < BOUND[3]:                       # top TL->TR
        f = (p - BOUND[2]) / SEG_CC[2]; return 120 * f, 420.0, "top"
    f = (p - BOUND[3]) / SEG_CC[3]; return 120.0, 420 - 420 * f, "right"


if __name__ == "__main__":
    main()
