"""Zoom into individual labeled fault events and compare against baseline.
Build a short-window RMS envelope of high-pass accel (bearing vibration proxy)."""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import signal
import imloader as L

FIG = os.path.join(os.path.dirname(__file__), "figures")
SESS = os.path.join(L.DATA_ROOT, "Session-2026-06-17--13-49-38_faulty_bearing")

imu = L.load_imu(SESS)
labels = L.load_labels(SESS)
fs = L.imu_fs(imu)
t = imu["t"].values
acc = imu[["ax", "ay", "az"]].values
gyr = imu[["gx", "gy", "gz"]].values

# high-pass accel magnitude -> vibration; then RMS envelope over 0.2s windows
acc_mag = np.linalg.norm(acc, axis=1)
b, a = signal.butter(4, 5.0 / (fs / 2), btype="high")
acc_hp = signal.filtfilt(b, a, acc_mag)
win = int(0.2 * fs)
env = np.sqrt(np.convolve(acc_hp**2, np.ones(win) / win, mode="same"))
gyr_mag = np.linalg.norm(gyr, axis=1)

faults = labels[labels["label"] == "fault"].reset_index(drop=True)

# Pick 6 fault events to show zoomed +/- 8s
pick = faults.iloc[[1, 3, 5, 7, 9, 11]].reset_index(drop=True)
fig, axes = plt.subplots(2, 3, figsize=(16, 8), sharey="row")
for i, (_, r) in enumerate(pick.iterrows()):
    c, rr = divmod(i, 3)  # not used; simple grid
for i, (_, r) in enumerate(pick.iterrows()):
    ax = axes[i // 3][i % 3]
    c = (r["start"] + r["end"]) / 2
    sel = (t >= c - 8) & (t <= c + 8)
    ax.plot(t[sel] - c, env[sel], color="C0", lw=0.9)
    ax.axvspan(r["start"] - c, r["end"] - c, color="red", alpha=0.2)
    ax.set_title(f"fault @ {c:.0f}s")
    ax.set_xlabel("t - center (s)")
    if i % 3 == 0:
        ax.set_ylabel("accel HP RMS env\n(>5 Hz, 0.2s)")
fig.suptitle(f"{L.session_name(SESS)} — zoom on fault events (red = labeled window)")
plt.tight_layout()
out = os.path.join(FIG, "bearing-fault-signature.png")
plt.savefig(out, dpi=110)
print("wrote", out)

# Quantify: envelope inside fault windows vs outside
m = L.label_mask(t, faults, names=("fault",))
print(f"\naccel HP RMS envelope: fault mean={env[m].mean():.4f}  baseline mean={env[~m].mean():.4f}  "
      f"ratio={env[m].mean()/env[~m].mean():.2f}x")
print(f"gyro mag: fault mean={gyr_mag[m].mean():.3f}  baseline mean={gyr_mag[~m].mean():.3f}  "
      f"ratio={gyr_mag[m].mean()/gyr_mag[~m].mean():.2f}x")

# loop period from fault spacing
centers = ((faults["start"] + faults["end"]) / 2).values
print(f"\nfault spacing (loop period): {np.diff(centers).mean():.1f}s +/- {np.diff(centers).std():.1f}s")
