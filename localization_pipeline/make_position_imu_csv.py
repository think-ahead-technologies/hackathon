"""
make_position_imu_csv.py -- join the estimated position onto every IMU sample so
position can be correlated with the raw IMU signals.

The position track (*_track.csv from pipeline.py) is per camera-FRAME; the IMU is
much faster. We align both on the shared host clock (frame ts in the filename ==
IMU t_host_us), interpolate the absolute position onto each IMU sample, derive
the loop segment + map x/y, and flag wheel detections and turntable anchors.

Usage:
  python make_position_imu_csv.py --imu ../experiment2/merged_1600hz.csv \
      --frames ../experiment2/merged_1600hz_frames --track exp2_track.csv \
      --wheels exp2_wheels.csv --anchors exp2_anchors.csv --out exp2_position_imu.csv
"""
import os, csv, glob, re, argparse
import numpy as np

LAP = 1240.0
BOUND = [0.0, 160.0, 620.0, 780.0, 1240.0]
SEG_CC = [160.0, 460.0, 160.0, 460.0]


def cum_to_xy(p):
    p %= LAP
    if p < BOUND[1]:  f = (p - BOUND[0]) / SEG_CC[0]; return 120 - 120 * f, 0.0, "bottom_BR_BL"
    if p < BOUND[2]:  f = (p - BOUND[1]) / SEG_CC[1]; return 0.0, 420 * f, "left_BL_TL"
    if p < BOUND[3]:  f = (p - BOUND[2]) / SEG_CC[2]; return 120 * f, 420.0, "top_TL_TR"
    f = (p - BOUND[3]) / SEG_CC[3]; return 120.0, 420 - 420 * f, "right_TR_BR"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--imu", required=True)
    ap.add_argument("--frames", required=True)
    ap.add_argument("--track", required=True)
    ap.add_argument("--wheels", required=True)
    ap.add_argument("--anchors", default=None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    # shared clock: first frame host timestamp = t_rel zero (same as pipeline)
    fts = np.array([int(re.search(r"_(\d+)\.jpg$", f).group(1))
                    for f in sorted(glob.glob(os.path.join(args.frames, "*.jpg")))]) / 1e6
    t0 = fts.min()

    # position track (per frame): t_rel_s -> cum_cm, moving
    tr = list(csv.DictReader(open(args.track)))
    tt = np.array([float(r["t_rel_s"]) for r in tr])
    cum = np.array([float(r["cum_cm"]) for r in tr])
    mov = np.array([int(r["moving"]) for r in tr])

    # event times -> nearest-IMU flag
    wheel_t = np.array([float(r["t_rel_s"]) for r in csv.DictReader(open(args.wheels))])
    anc = []
    if args.anchors and os.path.exists(args.anchors):
        for r in csv.DictReader(open(args.anchors)):
            anc.append((float(r["t_rel_s"]), r.get("deg", ""), r.get("source", "")))

    rows = list(csv.DictReader(open(args.imu)))
    imu_t = np.array([float(r["t_host_us"]) for r in rows]) / 1e6 - t0

    # mark the IMU sample nearest each wheel / anchor event
    wheel_flag = np.zeros(len(rows), bool)
    for wt in wheel_t:
        wheel_flag[int(np.argmin(np.abs(imu_t - wt)))] = True
    anc_idx = {}
    for at, deg, src in anc:
        anc_idx[int(np.argmin(np.abs(imu_t - at)))] = (deg, src)

    cols = ["t_host_us", "t_rel_s", "acc_x_g", "acc_y_g", "acc_z_g",
            "gyr_x_dps", "gyr_y_dps", "gyr_z_dps", "mag_x_ut", "mag_y_ut", "mag_z_ut"]
    with open(args.out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols + ["cum_cm", "lap", "segment", "map_x_cm", "map_y_cm",
                           "moving", "wheel", "turntable", "turn_deg", "turn_src"])
        for i, r in enumerate(rows):
            t = imu_t[i]
            c = float(np.interp(t, tt, cum))
            x, y, seg = cum_to_xy(c)
            mv = int(mov[min(np.searchsorted(tt, t), len(mov) - 1)])
            deg, src = anc_idx.get(i, ("", ""))
            w.writerow([r["t_host_us"], round(t, 4)] +
                       [r[c2] for c2 in cols[2:]] +
                       [round(c, 1), round(c / LAP, 3), seg, round(x, 1), round(y, 1),
                        mv, int(wheel_flag[i]), int(i in anc_idx), deg, src])
    print(f"wrote {args.out}: {len(rows)} IMU rows, "
          f"{int(wheel_flag.sum())} wheel marks, {len(anc_idx)} turntable marks")
    print("columns: IMU channels + cum_cm (absolute along loop), lap, segment, "
          "map_x/y (cm on 120x420 loop), moving, wheel, turntable")


if __name__ == "__main__":
    main()
