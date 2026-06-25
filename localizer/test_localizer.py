# ABOUTME: Unit tests for the localizer's streaming detectors — no NATS, no hardware.

import numpy as np

import main


ROI = {"polygon": [[32, 40], [54, 40], [54, 53], [32, 53]], "thresh": 60, "frame_size": [320, 240]}


# --- geometry --------------------------------------------------------------

def test_cum_to_xy_corners():
    # cum 0 = BR corner (x=120cm,y=0); each turntable centre lands exactly on a corner
    assert main.cum_to_xy(0.0) == (120.0, 0.0, "bottom")
    x, y, piece = main.cum_to_xy(main.BOUND[1])      # BL
    assert (round(x), round(y), piece) == (0, 0, "left")
    x, y, piece = main.cum_to_xy(main.BOUND[2])      # TL
    assert (round(x), round(y), piece) == (0, 420, "top")


def test_build_mask_scales_to_frame():
    roi = main.load_roi.__wrapped__ if hasattr(main.load_roi, "__wrapped__") else None  # noqa
    bbox, mask, n = main.build_mask(ROI, 640, 480)    # 2x the roi frame_size
    x0, y0, x1, y1 = bbox
    assert (x0, y0) == (64, 80) and (x1, y1) == (108, 106)
    assert n == mask.sum() > 0


# --- wheel detector --------------------------------------------------------

def _frame(dark_frac, w=320, h=240):
    """Bright frame with a dark patch covering dark_frac of the ROI rect [32,40]-[54,53]."""
    img = np.full((h, w), 200, np.uint8)
    cols = int(round(dark_frac * (54 - 32 + 1)))
    if cols:
        img[40:54, 32:32 + cols] = 0
    return img


def test_wheel_emits_on_dark_moving_run():
    det = main.WheelDetector(ROI, d0=0.4, netthr=0.05, rel_k=0.0)
    out = []
    # bright -> dark(moving) for a few frames -> bright again. One wheel at the run.
    seq = [(_frame(0.0), 0.0), (_frame(0.9), 0.1), (_frame(0.95), 0.2),
           (_frame(0.0), 0.3), (_frame(0.0), 0.4)]
    for img, t in seq:
        w = det.push(img, t, {"t_us": int(t * 1e6), "t_host_us": int(t * 1e6)})
        if w:
            out.append(w)
    assert len(out) == 1


def test_wheel_rejects_static_dark():
    # dark but never moving (identical frames -> net≈0) must not count with a real motion gate
    det = main.WheelDetector(ROI, d0=0.4, netthr=0.5, rel_k=0.0)
    out = [det.push(_frame(0.9), t / 10, {"t_us": 0, "t_host_us": 0}) for t in range(6)]
    assert all(w is None for w in out)


# --- anchor detector -------------------------------------------------------

def test_anchor_detects_90deg_turn():
    det = main.AnchorDetector(step_deg=90, min_rate=15, tol_frac=0.6)
    anchors = []
    t = 0.0
    # 1s of stillness, then ~90deg turn at 90 dps for 1s, then still
    for _ in range(10):
        a = det.push(t, 0.0); t += 0.1
        if a is not None:
            anchors.append(a)
    for _ in range(10):
        a = det.push(t, 90.0); t += 0.1
        if a is not None:
            anchors.append(a)
    for _ in range(10):
        a = det.push(t, 0.0); t += 0.1
        if a is not None:
            anchors.append(a)
    assert len(anchors) == 1


def test_anchor_ignores_small_wobble():
    det = main.AnchorDetector(step_deg=90, min_rate=15, tol_frac=0.6)
    anchors = [det.push(t / 10, 20.0 if t % 2 else -20.0) for t in range(40)]  # jitter, no net turn
    assert all(a is None for a in anchors)


# --- motion state ----------------------------------------------------------

def test_motion_detects_stop_after_dwell():
    m = main.MotionState(win=0.4, lo=0.025, hi=0.060, min_stop=0.5, min_move=0.5)
    t = 0.0; last = None
    for _ in range(60):                                # 3s of perfectly still -> stopped
        last = m.push(t, 0.0, 0.0); t += 0.05
    assert last is True


def test_motion_moving_when_vibrating():
    m = main.MotionState(win=0.4, lo=0.025, hi=0.060, min_stop=0.5, min_move=0.5)
    t = 0.0; last = None
    for i in range(60):                                # strong alternating accel -> moving
        last = m.push(t, 0.5 if i % 2 else -0.5, 0.0); t += 0.05
    assert last is False


# --- position tracker ------------------------------------------------------

def test_position_accumulates_and_holds():
    p = main.Position(start_tt=None)
    p.on_wheel(stopped=False); p.on_wheel(stopped=False)
    assert p.cum() == 30.0
    p.on_wheel(stopped=True)                           # idle wheel rejected
    assert p.cum() == 30.0


def test_position_absolute_leg_pin():
    p = main.Position(start_tt="BL")                   # TT_CUM index 1 -> 160 cm
    p.on_anchor()                                      # first anchor sets the absolute origin
    assert p.cum() == 160.0
    for _ in range(40):                                # 40 wheels * 15 = 600cm, leg BL->TL caps at 460
        p.on_wheel(stopped=False)
    assert p.cum() == 160.0 + 460.0                    # capped + held at the next turntable
    p.on_anchor()                                      # reaching TL snaps forward
    assert p.cum() == 620.0                            # == TT_CUM[2]
