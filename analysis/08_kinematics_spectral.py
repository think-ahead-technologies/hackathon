"""Refine the bearing hypothesis with the measured rig geometry.

Geometry (colleague's diagram, figures/Screenshot...):
  big driven wheels  d = 55 cm   (4, on 2 servos)
  idler rollers      d = 2.5 cm  (the 'idler-wheel bearings' that fail)
  box (carrier)      39 x 39 cm, EMPTY (sensor only)

Mechanism hypothesis: a defective idler roller is fixed track infrastructure; the
box rolls over it once per lap, producing a vibration burst lasting ~ box_length/v
at the roller's rotation / bearing-defect frequency. Predictions:
  1. burst duration ~ box_length / v   -> gives an independent box-speed estimate
  2. roller spin freq f_r = v/(pi*d_roller) and its bearing harmonics should be
     the band where in-fault accel energy concentrates (and be < 25 Hz Nyquist)
  3. big-wheel spin v/(pi*0.55) is ~22x slower -> undetectable, so only idlers show
"""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import signal
import imloader as L

FIG = os.path.join(os.path.dirname(__file__), "figures")
D_ROLLER = 0.025      # m
D_BIGWHEEL = 0.55     # m
BOX_LEN = 0.39        # m (square carrier, along travel)

SESSIONS = [
    ("13-49-38 (1 locus/lap)", "Session-2026-06-17--13-49-38_faulty_bearing"),
    ("18-24 (base)", "Session-2026-06-17--18-24-09-faulty-bearing"),
    ("18-53 (+4)", "Session-2026-06-17--18-53-07-additional-4-defective-idler-wheel-bearings"),
    ("19-11 (cum 8)", "Session-2026-06-17--19-11-06-cummulated-8-defective-idler-wheel-bearings"),
    ("19-25 (+2 broken)", "Session-2026-06-17--19-25-09-additional-2-broken-bearings"),
]

# ---- 1. Kinematics: estimate box speed from burst duration ----
print("KINEMATIC SPEED ESTIMATE (v ~ box_length / burst_duration)")
print(f"{'session':26s} {'burst(s)':>9s} {'v(m/s)':>7s} {'f_roller(Hz)':>12s} {'f_bigwheel(Hz)':>14s}")
speeds = []
for tag, name in SESSIONS:
    sd = os.path.join(L.DATA_ROOT, name)
    labels = L.load_labels(sd)
    faults = labels[labels["label"] == "fault"]
    # use the SHORT bursts (single locus pass); robust = median of shortest 40%
    durs = np.sort(faults["length"].values)
    short = durs[: max(1, int(0.5 * len(durs)))]
    burst = np.median(short)
    v = BOX_LEN / burst
    f_r = v / (np.pi * D_ROLLER)
    f_bw = v / (np.pi * D_BIGWHEEL)
    speeds.append(v)
    print(f"{tag:26s} {burst:9.2f} {v:7.3f} {f_r:12.2f} {f_bw:14.3f}")
v_mean = np.mean(speeds)
f_r_mean = v_mean / (np.pi * D_ROLLER)
print(f"\nmean v = {v_mean:.3f} m/s  -> roller spin f_r = {f_r_mean:.1f} Hz; "
      f"bearing-defect harmonics ~{3*f_r_mean:.0f}-{8*f_r_mean:.0f} Hz "
      f"(Nyquist = 25 Hz)")

# ---- 2. In-fault vs baseline PSD ----
def gather(name):
    """Return HP accel and masks for fault / straight-normal / turn (matched motion)."""
    sd = os.path.join(L.DATA_ROOT, name)
    imu = L.load_imu(sd); labels = L.load_labels(sd); fs = L.imu_fs(imu)
    t = imu["t"].values
    am = np.linalg.norm(imu[["ax", "ay", "az"]].values, axis=1)
    bb, aa = signal.butter(4, 2.0 / (fs / 2), btype="high")
    am = signal.filtfilt(bb, aa, am)            # remove gravity/slow tilt
    gm = np.linalg.norm(imu[["gx", "gy", "gz"]].values, axis=1)
    fmask = L.label_mask(t, labels, names=("fault",))
    glo, ghi = np.percentile(gm, [40, 75])
    straight = (~fmask) & (gm < glo)   # moving on a straight (low rotation) = matched to faults
    turn = (~fmask) & (gm > ghi)       # turning (high rotation) for context
    return am, fmask, straight, turn, fs

pool_f, pool_s, pool_t = [], [], []
fs = 50.0
for tag, name in SESSIONS:
    am, fmask, straight, turn, fs = gather(name)
    pool_f.append(am[fmask]); pool_s.append(am[straight]); pool_t.append(am[turn])
xf, xs, xt = (np.concatenate(p) for p in (pool_f, pool_s, pool_t))
nper = 64
ff, Pf = signal.welch(xf, fs=fs, nperseg=nper)
_, Ps = signal.welch(xs, fs=fs, nperseg=nper)
_, Pt = signal.welch(xt, fs=fs, nperseg=nper)

fig, ax = plt.subplots(1, 2, figsize=(16, 6))
ax[0].semilogy(ff, Ps, color="C0", lw=2, label="straight-normal (matched, low gyro)")
ax[0].semilogy(ff, Pt, color="C2", lw=1.5, ls=":", label="turns (high gyro, context)")
ax[0].semilogy(ff, Pf, color="C3", lw=2, label="fault windows")
ax[0].axvline(f_r_mean, color="green", ls="--", lw=1, label=f"roller spin {f_r_mean:.1f} Hz")
ax[0].axvline(25, color="k", ls=":", lw=1, label="Nyquist 25 Hz")
ax[0].set_xlabel("frequency (Hz)"); ax[0].set_ylabel("PSD (m/s²)²/Hz")
ax[0].set_title("Accel PSD: fault vs MATCHED straight baseline (pooled)")
ax[0].legend(fontsize=9); ax[0].grid(alpha=0.3, which="both")

ratio = Pf / Ps
ax[1].plot(ff, ratio, color="purple", lw=2)
ax[1].axhline(1.0, color="grey", ls="--")
ax[1].set_xlabel("frequency (Hz)"); ax[1].set_ylabel("fault / straight-normal PSD ratio")
ax[1].set_title("Fault excess vs a straight (ratio > 1 = fault adds energy)")
ax[1].grid(alpha=0.3)
fig.suptitle("Bearing-fault spectral signature — fault vs matched straight (turns shown for context)", fontsize=13)
plt.tight_layout()
out = os.path.join(FIG, "bearing-spectrum-vs-kinematics.png")
plt.savefig(out, dpi=120); print("\nwrote", out)

lo = ff < 10; hi = ff >= 15
print(f"\nfault/straight-normal PSD ratio:  <10 Hz = {ratio[lo].mean():.2f}x   "
      f">=15 Hz = {ratio[hi].mean():.2f}x   at top bin ({ff[-1]:.0f}Hz) = {ratio[-1]:.2f}x")
print("Interpretation: matched to a straight, fault adds energy broadly and most "
      "strongly toward Nyquist -> true defect content is >25 Hz, aliasing down at 50 Hz.")
