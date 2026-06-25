# ABOUTME: Localizer — the conveyor-box localization pipeline as a streaming NATS service.
# ABOUTME: Consumes edge.camera (wheel-count), edge.imu (gyro anchors + motion), edge.mag (missed-turn
# ABOUTME: recovery) and publishes one position fix per advance to edge.position.<line>.<container>.
#
# This is the streaming port of localization_pipeline/pipeline.py. The offline tool runs over a whole
# recording (frame dir + IMU CSV) and does leave-one-out accuracy; here the same detectors run
# incrementally on the live derived topics. Geometry + cum_to_xy are copied verbatim from pipeline.py.

import asyncio
import base64
import json
import os
import ssl

import cv2
import numpy as np
import nats

# --- config ---------------------------------------------------------------

NATS_URL = os.environ.get("NATS_URL", "nats://nats:4222")
NATS_NKEY_SEED = os.environ.get("NATS_NKEY_SEED")
NATS_CA_FILE = os.environ.get("NATS_CA_FILE")
NATS_TLS_HOSTNAME = os.environ.get("NATS_TLS_HOSTNAME", "nats")

CAMERA_SUBJECT = os.environ.get("CAMERA_SUBJECT", "edge.camera.*.*")
IMU_SUBJECT = os.environ.get("IMU_SUBJECT", "edge.imu.*.*")
MAG_SUBJECT = os.environ.get("MAG_SUBJECT", "edge.mag.*.*")

ROI_FILE = os.environ.get("ROI_FILE", os.path.join(os.path.dirname(__file__), "roi.json"))
# First turntable the box reaches (clockwise BR/BL/TL/TR). Enables absolute leg-pinned position;
# empty -> relative cumulative distance only (still maps to x/y, just not corner-locked).
START_TT = os.environ.get("START_TT") or None

# detector tunables (defaults match the validated pipeline.py run)
D0 = float(os.environ.get("D0", "0.4"))            # ROI dark-fraction gate
NETTHR = float(os.environ.get("NETTHR", "0.3"))    # ROI camera-compensated motion gate
REL_K = float(os.environ.get("REL_K", "0.6"))      # adaptive dark thresh = REL_K * frame-median (0=fixed)
HANG_S = float(os.environ.get("HANG_S", "1.0"))    # max dt to trust frame-to-frame motion

# --- geometry (copied from localization_pipeline/pipeline.py) --------------

CM = 15.0
TT_LEN = 40.0
SEG_CC = [120 + TT_LEN, 420 + TT_LEN, 120 + TT_LEN, 420 + TT_LEN]   # 160,460,160,460
BOUND = np.cumsum([0.0] + SEG_CC)                                    # [0,160,620,780,1240]
LAP = float(BOUND[-1])                                               # 1240 cm
TT_CUM = BOUND[:-1]                                                  # turntable centres: 0,160,620,780
ORDER = ["BR", "BL", "TL", "TR"]                                    # clockwise neighbour order
SEG_AFTER = {"BR": SEG_CC[0], "BL": SEG_CC[1], "TL": SEG_CC[2], "TR": SEG_CC[3]}


def cum_to_xy(p):
    """centre-to-centre cum (0..LAP) -> (x_cm, y_cm, piece) on the 120x420 loop, each leg proportional
    so turntable dwells sit exactly at a corner. Verbatim from pipeline.py."""
    p %= LAP
    if p < BOUND[1]:
        f = (p - BOUND[0]) / SEG_CC[0]; return 120 - 120 * f, 0.0, "bottom"
    if p < BOUND[2]:
        f = (p - BOUND[1]) / SEG_CC[1]; return 0.0, 420 * f, "left"
    if p < BOUND[3]:
        f = (p - BOUND[2]) / SEG_CC[2]; return 120 * f, 420.0, "top"
    f = (p - BOUND[3]) / SEG_CC[3]; return 120.0, 420 - 420 * f, "right"


# --- ROI ------------------------------------------------------------------

def load_roi(path):
    roi = json.load(open(path))
    return dict(polygon=[(int(x), int(y)) for x, y in roi["polygon"]],
                thresh=int(roi.get("thresh", 60)),
                frame_size=tuple(roi.get("frame_size", [320, 240])))


def build_mask(roi, w, h):
    """Scale the ROI polygon (defined at roi['frame_size']) to an actual (w,h) frame and crop to its
    bounding box. Returns (bbox, mask, n). Lets one ROI file work across camera resolutions."""
    fw, fh = roi["frame_size"]
    sx, sy = w / fw, h / fh
    poly = np.array([[int(round(x * sx)), int(round(y * sy))] for x, y in roi["polygon"]], np.int32)
    x0, y0 = int(poly[:, 0].min()), int(poly[:, 1].min())
    x1, y1 = int(poly[:, 0].max()), int(poly[:, 1].max())
    full = np.zeros((h, w), np.uint8); cv2.fillPoly(full, [poly], 1)
    mask = full[y0:y1 + 1, x0:x1 + 1].astype(bool)
    return (x0, y0, x1, y1), mask, int(mask.sum())


# --- streaming wheel detector (pipeline.detect_wheels, incremental) --------

class WheelDetector:
    """Run-gate wheel counter. A wheel = one contiguous run of dark&moving ROI; emitted at the run's
    peak-dark frame when the run closes. Mirrors pipeline.detect_wheels but frame-by-frame."""

    def __init__(self, roi, d0=D0, netthr=NETTHR, rel_k=REL_K, hang_s=HANG_S):
        self.roi = roi; self.d0 = d0; self.netthr = netthr; self.rel_k = rel_k; self.hang_s = hang_s
        self.bbox = self.mask = None; self.n = 0; self.dims = None
        self.prev_roi = self.prev_full = None; self.prev_t = None
        self.in_run = False; self.run_peak_dark = -1.0; self.run_max_net = 0.0
        self.run_peak = None

    def _ensure_mask(self, w, h):
        if self.dims != (w, h):
            self.bbox, self.mask, self.n = build_mask(self.roi, w, h)
            self.dims = (w, h)
            self.prev_roi = self.prev_full = None      # geometry changed -> drop motion history

    def push(self, gray, t, meta):
        """Feed one grayscale frame at time t (s). meta is carried onto an emitted wheel. Returns a
        wheel dict {t, **meta} when a run closes with enough motion, else None."""
        h, w = gray.shape[:2]
        self._ensure_mask(w, h)
        x0, y0, x1, y1 = self.bbox
        r = gray[y0:y1 + 1, x0:x1 + 1].astype(np.float32)
        thr = self.rel_k * float(np.median(gray)) if self.rel_k else self.roi["thresh"]
        dark = float((r[self.mask] < thr).mean()) if self.n else 0.0
        net = 0.0
        if self.prev_roi is not None and self.prev_t is not None and (t - self.prev_t) <= self.hang_s:
            rm = float(np.abs(r - self.prev_roi)[self.mask].mean())
            gm = float(np.median(np.abs(gray.astype(np.float32) - self.prev_full)))
            net = max(0.0, rm - gm)
        self.prev_roi, self.prev_full, self.prev_t = r, gray.astype(np.float32), t

        emitted = None
        if dark > self.d0:                              # inside a dark run
            if not self.in_run:
                self.in_run = True; self.run_peak_dark = -1.0; self.run_max_net = 0.0; self.run_peak = None
            self.run_max_net = max(self.run_max_net, net)
            if dark > self.run_peak_dark:
                self.run_peak_dark = dark; self.run_peak = dict(t=t, **meta)
        elif self.in_run:                               # run just closed
            if self.run_max_net >= self.netthr:
                emitted = self.run_peak
            self.in_run = False
        return emitted


# --- streaming gyro turntable-anchor detector (track.detect_turntables) ----

class AnchorDetector:
    """Integrate yaw rate; a contiguous |rate|>min stretch that turns ~step_deg is a hard anchor."""

    def __init__(self, step_deg=90.0, min_rate=15.0, tol_frac=0.6):
        self.step = step_deg; self.min_rate = min_rate; self.tol = tol_frac
        self.heading = 0.0; self.prev_t = self.prev_yaw = None
        self.active = False; self.start_heading = 0.0; self.start_t = None

    def push(self, t, yaw_dps):
        """Returns an anchor time (s) when a ~step_deg rotation completes, else None."""
        if self.prev_t is not None and t > self.prev_t:
            dt = t - self.prev_t
            if dt <= 2.0:                               # trapezoid on real dt; skip across big gaps
                self.heading += 0.5 * (yaw_dps + self.prev_yaw) * dt
        self.prev_t, self.prev_yaw = t, yaw_dps

        emitted = None
        act = abs(yaw_dps) > self.min_rate
        if act and not self.active:
            self.active = True; self.start_heading = self.heading; self.start_t = t
        elif not act and self.active:
            d = self.heading - self.start_heading
            if abs(abs(d) - self.step) <= self.tol * self.step:
                emitted = self.start_t
            self.active = False
        return emitted


# --- streaming motion (pipeline.motion_state) ------------------------------

class MotionState:
    """Rolling horizontal-vibration energy + Schmitt hysteresis + min-dwell debounce -> stopped flag."""

    def __init__(self, win=0.40, lo=0.025, hi=0.060, min_stop=2.0, min_move=1.5):
        self.win = win; self.lo = lo; self.hi = hi; self.min_stop = min_stop; self.min_move = min_move
        self.buf = []                                   # (t, ax, ay)
        self.committed = False; self.cand = False; self.cand_since = None

    def push(self, t, ax, ay):
        self.buf.append((t, ax, ay))
        cut = t - self.win
        while self.buf and self.buf[0][0] < cut:
            self.buf.pop(0)
        if len(self.buf) > 2:
            axs = np.array([b[1] for b in self.buf]); ays = np.array([b[2] for b in self.buf])
            e = float(np.sqrt(axs.std() ** 2 + ays.std() ** 2))
            if self.committed and e > self.hi:
                cand = False
            elif (not self.committed) and e < self.lo:
                cand = True
            else:
                cand = self.committed
            # min-dwell debounce: a flip must persist before it is committed
            if cand != self.committed:
                if self.cand != cand:
                    self.cand = cand; self.cand_since = t
                need = self.min_stop if cand else self.min_move
                if self.cand_since is not None and (t - self.cand_since) >= need:
                    self.committed = cand
            else:
                self.cand = cand; self.cand_since = None
        return self.committed


# --- position tracker (pipeline leg-pin + cap-and-hold) --------------------

class Position:
    """Cumulative distance (+15cm per accepted wheel; held while stopped). Each detected turntable
    snaps to the next centre-to-centre boundary (cap the leg + park at the corner). With START_TT the
    reconstruction is absolute and corner-locked; without it, position is relative cumulative."""

    def __init__(self, start_tt=None):
        self.start_tt = start_tt
        self.raw_cum = 0.0                              # relative cumulative (always advances)
        self.anchored = False
        self.leg_base = 0.0                             # abs cum of the last anchored turntable
        self.since_anchor = 0.0                         # raw cm travelled since that anchor
        self.order_idx = 0; self.cur_seg = SEG_CC[0]

    def on_wheel(self, stopped):
        if stopped:                                     # FIX: reject false idle wheels, hold position
            return
        self.raw_cum += CM
        self.since_anchor += CM

    def on_anchor(self):
        if not self.anchored:
            if self.start_tt in ORDER:
                self.order_idx = ORDER.index(self.start_tt)
                self.leg_base = float(TT_CUM[self.order_idx])
                self.cur_seg = SEG_AFTER[self.start_tt]
                self.since_anchor = 0.0
                self.anchored = True
            return
        self.leg_base += self.cur_seg                   # snap forward to the next turntable centre
        self.order_idx = (self.order_idx + 1) % 4
        self.cur_seg = SEG_AFTER[ORDER[self.order_idx]]
        self.since_anchor = 0.0

    def cum(self):
        if not self.anchored:
            return self.raw_cum
        return self.leg_base + min(self.since_anchor, self.cur_seg)   # cap leg + hold dwell


# --- magnetometer missed-turn recovery (pipeline.recover_missed_turns) -----

class MagRecovery:
    """A turntable turn during an IMU data gap is invisible to the gyro (no samples = no integral). The
    mag reads absolute heading, so the turn shows as a heading STEP across the gap. We hard-iron
    calibrate over a rolling buffer (circle fit) and, a few seconds after a gap, inject an anchor if the
    calibrated heading stepped > step_deg and no gyro anchor already covered it."""

    def __init__(self, gap_s=0.8, step_deg=70.0, eval_delay=3.0, buf_max=600):
        self.gap_s = gap_s; self.step = step_deg; self.eval_delay = eval_delay; self.buf_max = buf_max
        self.buf = []                                   # (t, mx, my)
        self.pending = []                               # {ta, tb, eval_at}

    def note_imu_gap(self, ta, tb):
        if (tb - ta) > self.gap_s:
            self.pending.append(dict(ta=ta, tb=tb, eval_at=tb + self.eval_delay))

    def _heading(self, a, b, cx, cy):
        pts = [(m[1] - cx, m[2] - cy) for m in self.buf if a <= m[0] <= b]
        if len(pts) <= 3:
            return None
        mx = np.median([p[0] for p in pts]); my = np.median([p[1] for p in pts])
        return float(np.degrees(np.arctan2(my, mx)))

    def push(self, t, mx, my, anchor_times):
        """Record a mag sample and return a list of recovered anchor times that are due now."""
        self.buf.append((t, mx, my))
        if len(self.buf) > self.buf_max:
            self.buf.pop(0)
        recovered = []
        if not self.pending:
            return recovered
        # hard-iron circle fit over the current buffer
        if len(self.buf) < 20:
            return recovered
        arr = np.array(self.buf)
        mxs, mys = arr[:, 1], arr[:, 2]
        try:
            A = np.c_[2 * mxs, 2 * mys, np.ones(len(mxs))]
            cx, cy, _ = np.linalg.lstsq(A, mxs ** 2 + mys ** 2, rcond=None)[0]
        except np.linalg.LinAlgError:
            return recovered
        still = []
        for g in self.pending:
            if t < g["eval_at"]:
                still.append(g); continue
            ta, tb = g["ta"], g["tb"]
            mid = 0.5 * (ta + tb)
            if any(min(abs(a - ta), abs(a - tb), abs(a - mid)) < 4 for a in anchor_times):
                continue                                # gyro already caught this turn
            bef = self._heading(ta - 3, ta - 0.2, cx, cy)
            aft = self._heading(tb + 0.2, tb + 3, cx, cy)
            if bef is None or aft is None:
                continue
            step = (aft - bef + 180) % 360 - 180
            if abs(step) > self.step:
                recovered.append(mid)
        self.pending = still
        return recovered


# --- per-stream state ------------------------------------------------------

class Stream:
    def __init__(self, line, container, roi):
        self.line = line; self.container = container
        self.wheels = WheelDetector(roi)
        self.anchors = AnchorDetector()
        self.motion = MotionState()
        self.pos = Position(START_TT)
        self.mag = MagRecovery()
        self.stopped = False
        self.anchor_times = []                          # recent gyro/mag anchor times (s) for dedup
        self.last_imu_t = None

    def position_msg(self, t_us, t_host_us):
        x_cm, y_cm, piece = cum_to_xy(self.pos.cum() % LAP)
        return {
            "t_us": int(t_us),
            "t_host_us": int(t_host_us),
            "segment": f"{self.line}.{piece}",
            "x": round(x_cm / 100.0, 3),                # contract: metres
            "y": round(y_cm / 100.0, 3),
        }


# --- wiring ----------------------------------------------------------------

def parse_subject(subject):
    # edge.<kind>.<line>.<container>
    _, _, line, container = subject.split(".", 3)
    return line, container


async def main():
    roi = load_roi(ROI_FILE)
    auth = {}
    if NATS_NKEY_SEED:
        auth["nkeys_seed"] = NATS_NKEY_SEED
    if NATS_CA_FILE:
        auth["tls"] = ssl.create_default_context(cafile=NATS_CA_FILE)
        auth["tls_hostname"] = NATS_TLS_HOSTNAME
    nc = await nats.connect(NATS_URL, reconnect_time_wait=2, max_reconnect_attempts=-1, **auth)

    streams = {}

    def stream(line, container):
        key = (line, container)
        s = streams.get(key)
        if s is None:
            s = streams[key] = Stream(line, container, roi)
        return s

    async def publish(s, t_us, t_host_us):
        msg = s.position_msg(t_us, t_host_us)
        await nc.publish(f"edge.position.{s.line}.{s.container}", json.dumps(msg).encode())

    async def on_camera(m):
        try:
            line, container = parse_subject(m.subject)
            d = json.loads(m.data)
            buf = np.frombuffer(base64.b64decode(d["data"]), np.uint8)
            gray = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
            if gray is None:
                return
            t = float(d["t_host_us"]) / 1e6
            s = stream(line, container)
            wheel = s.wheels.push(gray, t, dict(t_us=d.get("t_us", 0), t_host_us=d["t_host_us"]))
            if wheel is not None:
                s.pos.on_wheel(s.stopped)
                await publish(s, wheel["t_us"], wheel["t_host_us"])
        except Exception as exc:  # noqa: BLE001 — one bad frame must not kill the subscription
            print(f"[localizer] camera dropped: {exc}", flush=True)

    async def on_imu(m):
        try:
            line, container = parse_subject(m.subject)
            d = json.loads(m.data)
            t = float(d["t_host_us"]) / 1e6
            s = stream(line, container)
            if s.last_imu_t is not None:
                s.mag.note_imu_gap(s.last_imu_t, t)
            s.last_imu_t = t
            ax, ay, _az = d["acc_g"]
            s.stopped = s.motion.push(t, ax, ay)
            yaw = d["gyr_dps"][2]
            anc = s.anchors.push(t, yaw)
            if anc is not None:
                s.anchor_times.append(anc)
                s.pos.on_anchor()
                await publish(s, d["t_us"], d["t_host_us"])
        except Exception as exc:  # noqa: BLE001
            print(f"[localizer] imu dropped: {exc}", flush=True)

    async def on_mag(m):
        try:
            line, container = parse_subject(m.subject)
            d = json.loads(m.data)
            t = float(d["t_host_us"]) / 1e6
            mx, my, _mz = d["mag_ut"]
            s = stream(line, container)
            for rt in s.mag.push(t, mx, my, s.anchor_times):
                s.anchor_times.append(rt)
                s.pos.on_anchor()                       # recovered turntable -> snap
                await publish(s, d["t_us"], d["t_host_us"])
        except Exception as exc:  # noqa: BLE001
            print(f"[localizer] mag dropped: {exc}", flush=True)

    await nc.subscribe(CAMERA_SUBJECT, cb=on_camera)
    await nc.subscribe(IMU_SUBJECT, cb=on_imu)
    await nc.subscribe(MAG_SUBJECT, cb=on_mag)
    print(f"[localizer] {CAMERA_SUBJECT} + {IMU_SUBJECT} + {MAG_SUBJECT} "
          f"-> edge.position.<line>.<container>  (start_tt={START_TT or 'relative'})", flush=True)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    import signal
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await nc.drain()


if __name__ == "__main__":
    asyncio.run(main())
