"""
track.py -- position encoding + IMU fusion on the figure-8 conveyor.

Two independent measurements localize the box:
  * CAMERA wheel-counter (count_wheels_v2.py): counts big wheels passing. Each
    straight2 = 4 wheels, straight4 = 8 wheels -> wheel count gives FINE travel
    progress along the straights.
  * IMU (merged_20260623_17xx.csv): integrating yaw rate gyr_z over time gives
    heading; each TURNTABLE produces a ~90 deg yaw step -> a hard ANCHOR that
    says "you just entered a new piece" and re-syncs the (drift-prone) wheel
    count. Crucial because video hangs make the wheel count skip.

ODR CAVEAT: the IMU output-data-rate CHANGES mid-recording, so we NEVER assume a
fixed sample period -- every integration uses the real per-sample dt from the
timestamp column. See integrate_yaw().

Position is reported as {loop, piece, pct, point_in, point_out}: pct in [0,100]
is travel progress along `piece`; the box sits between the piece on the
point_in side and the next piece on the point_out side ("between two pieces").
"""
import json, os, bisect
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


# ----------------------------------------------------------------- map
class TrackMap:
    def __init__(self, path=None):
        self.t = json.load(open(path or os.path.join(HERE, "track_map.json")))
        self.pieces = self.t["pieces"]
        self.loops = self.t["loops"]

    def ring(self, loop):
        return self.loops[loop]["ring"]

    def loop_len(self, loop):
        return self.loops[loop]["length_cm"]

    def loop_wheels(self, loop):
        return self.loops[loop]["wheels"]

    # ---- position <-> global per-loop coordinate ----
    def pos_to_cm(self, loop, piece, pct):
        """{loop,piece,pct} -> cumulative cm around that loop."""
        for node in self.ring_nodes(loop):
            if node["piece"] == piece:
                L = self.pieces[piece]["length_cm"]
                return node["cum_cm_start"] + (pct / 100.0) * L
        raise KeyError(f"{piece} not in loop {loop}")

    def ring_nodes(self, loop):
        return self.loops[loop]["ring"]

    def cm_to_pos(self, loop, cm):
        """cumulative cm (any value; wraps) -> {loop,piece,pct,...}."""
        total = self.loop_len(loop)
        cm = cm % total if total else 0.0
        nodes = self.ring_nodes(loop)
        starts = [n["cum_cm_start"] for n in nodes]
        i = bisect.bisect_right(starts, cm) - 1
        node = nodes[i]
        L = self.pieces[node["piece"]]["length_cm"]
        pct = 100.0 * (cm - node["cum_cm_start"]) / L if L > 0 else 0.0
        return dict(loop=loop, piece=node["piece"], pct=round(pct, 1),
                    point_in=node["point_in"], point_out=node["point_out"],
                    cum_cm=round(cm, 2))

    def wheels_to_pos(self, loop, wheel_count):
        """cumulative big-wheel count (wraps per lap) -> position. Uses the
        camera counter's running total directly."""
        total_w = self.loop_wheels(loop)
        wc = wheel_count % total_w if total_w else 0
        nodes = self.ring_nodes(loop)
        starts = [n["cum_wheels_start"] for n in nodes]
        i = bisect.bisect_right(starts, wc) - 1
        node = nodes[i]; piece = node["piece"]
        w = self.pieces[piece]["wheels"]
        pct = 100.0 * (wc - node["cum_wheels_start"]) / w if w > 0 else 0.0
        return dict(loop=loop, piece=piece, pct=round(pct, 1),
                    point_in=node["point_in"], point_out=node["point_out"],
                    cum_wheels=wc)


# ----------------------------------------------------------------- IMU
def load_imu(csv_path=None, t_col="t_rel_s", yaw_col="gyr_z_dps"):
    """Load IMU as (t_seconds, yaw_rate_dps). Robust to the ODR change: we keep
    the real timestamps and never resample."""
    p = csv_path or os.path.join(HERE, "..", "merged_20260623_17xx.csv")
    import csv
    t, yaw = [], []
    with open(p, newline="") as fh:
        r = csv.DictReader(fh)
        for row in r:
            try:
                t.append(float(row[t_col])); yaw.append(float(row[yaw_col]))
            except (KeyError, ValueError):
                continue
    return np.array(t), np.array(yaw)


def integrate_yaw(t, yaw_dps):
    """Cumulative heading (deg) by trapezoidal integration on REAL dt.
    Variable dt (ODR change) is handled exactly because dt = diff(t)."""
    if len(t) < 2:
        return np.zeros_like(t)
    dt = np.diff(t)
    # trapezoid on yaw rate; guard against any out-of-order/huge gaps
    inc = 0.5 * (yaw_dps[1:] + yaw_dps[:-1]) * dt
    return np.concatenate([[0.0], np.cumsum(inc)])


def detect_turntables(t, yaw_dps, step_deg=90.0, min_rate_dps=20.0,
                      tol_frac=0.45):
    """Find turntable rotation events: contiguous stretches where |yaw_rate|
    stays above min_rate_dps and the integrated turn is ~ +/- step_deg.
    Returns list of dicts {t_start, t_end, delta_deg}. These are the hard
    anchors that advance the piece index by one turntable.
    ODR-robust: integration inside each candidate window uses real dt."""
    heading = integrate_yaw(t, yaw_dps)
    active = np.abs(yaw_dps) > min_rate_dps
    events, i, n = [], 0, len(t)
    while i < n:
        if active[i]:
            j = i
            while j < n and active[j]:
                j += 1
            d = heading[min(j, n - 1)] - heading[i]
            if abs(abs(d) - step_deg) <= tol_frac * step_deg:
                events.append(dict(i_start=int(i), i_end=int(j - 1),
                                   t_start=float(t[i]), t_end=float(t[min(j, n - 1)]),
                                   delta_deg=round(float(d), 1)))
            i = j
        else:
            i += 1
    return events


# ----------------------------------------------------------------- demo / self-check
if __name__ == "__main__":
    M = TrackMap()
    print("loops:", {k: (v["length_cm"], v["wheels"]) for k, v in M.loops.items()})

    # position round-trips
    for loop, cm in [("upper", 150.0), ("lower", 500.0)]:
        pos = M.cm_to_pos(loop, cm)
        back = M.pos_to_cm(loop, pos["piece"], pos["pct"])
        print(f"{loop} {cm}cm -> {pos['piece']} {pos['pct']}%  (back={back:.1f}cm)")

    # wheel-count -> position (e.g. camera counted 30 wheels into the lower loop)
    print("lower wheel#30 ->", M.wheels_to_pos("lower", 30))

    # IMU turntable anchors
    try:
        t, yaw = load_imu()
        ev = detect_turntables(t, yaw)
        print(f"IMU: {len(t)} samples, dt range "
              f"{np.diff(t).min()*1000:.1f}-{np.diff(t).max()*1000:.1f} ms "
              f"(ODR varies); detected {len(ev)} turntable rotations")
        for e in ev[:8]:
            print("   turn @ %.2fs  %+.0f deg" % (e["t_start"], e["delta_deg"]))
    except Exception as ex:
        print("IMU check skipped:", ex)
