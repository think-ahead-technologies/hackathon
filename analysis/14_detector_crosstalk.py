"""Detector cross-talk: do the two detectors confuse each other's fault type?

In production both run on the same stream. We check:
  (a) does the BEARING detector flag qs_wobbly windows as faults?
  (b) does the WOBBLE detector flag bearing-fault windows as wobbly?
Low cross-firing => the two signatures are orthogonal (good, separable faults)."""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.ensemble import RandomForestClassifier
import imloader as L
import features as F
from importlib import import_module
qs = import_module("06_wobble_stability") if False else None  # avoid re-exec; redefine below

OUT = os.path.join(os.path.dirname(__file__), "out")
FIG = os.path.join(os.path.dirname(__file__), "figures")

# ---- load both trained models' training data ----
db = np.load(os.path.join(OUT, "dataset.npz"), allow_pickle=True)
Xb, yb = db["X"], db["y"]
bearing = RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                 random_state=0, n_jobs=-1).fit(Xb, yb)
BEAR_THR = 0.053  # equal-error operating point

dq = np.load(os.path.join(OUT, "qs_dataset.npz"), allow_pickle=True)
Xq, yq = dq["X"], dq["y"]
wobble = RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                random_state=0, n_jobs=-1).fit(Xq, yq)
WOB_THR = 0.50

# need qs feature extractor for bearing sessions -> import from script 06
import importlib.util, sys
spec = importlib.util.spec_from_file_location("wob", os.path.join(os.path.dirname(__file__), "06_wobble_stability.py"))
# script 06 runs on import (it's a script); instead re-implement the call we need:
from scipy import signal

# minimal copy of qs_features (kept in sync with 06_wobble_stability.py)
WIN_S, HOP_S = 1.0, 0.5
def qs_features(imu, fs):
    t = imu["t"].values
    acc = imu[["ax","ay","az"]].values; gyr = imu[["gx","gy","gz"]].values
    bl, al = signal.butter(2, 5.0/(fs/2), btype="low")
    accl = signal.filtfilt(bl, al, acc, axis=0)
    roll = np.degrees(np.arctan2(accl[:,1], accl[:,2]))
    pitch = np.degrees(np.arctan2(-accl[:,0], np.hypot(accl[:,1], accl[:,2])))
    gyr_mag = np.linalg.norm(gyr, axis=1); gyr_lf = signal.filtfilt(bl, al, gyr_mag)
    bsw, asw = signal.butter(2, 3.0/(fs/2), btype="low")
    sway = signal.filtfilt(bsw, asw, np.hypot(acc[:,0], acc[:,1]))
    acc_mag = np.linalg.norm(acc, axis=1); acc_lf = signal.filtfilt(bl, al, acc_mag)
    jerk = np.gradient(acc_mag)*fs
    w = int(round(WIN_S*fs)); h = int(round(HOP_S*fs))
    freqs = np.fft.rfftfreq(w, 1.0/fs); fb = (freqs>=0.5)&(freqs<=10)
    feats, centers = [], []
    for s in range(0, len(t)-w+1, h):
        e=s+w; g=gyr_mag[s:e]-gyr_mag[s:e].mean()
        sp=np.abs(np.fft.rfft(g*np.hanning(w)))**2; bd=sp[fb]
        feats.append([np.std(np.concatenate([roll[s:e],pitch[s:e]])),
            np.sqrt(np.mean(gyr_lf[s:e]**2)), np.std(sway[s:e]), np.std(acc_lf[s:e]),
            freqs[fb][np.argmax(bd)] if bd.size else 0.0, bd.max()/(sp.sum()+1e-12),
            np.sqrt(np.mean(jerk[s:e]**2))])
        centers.append((t[s]+t[e-1])/2)
    return np.array(feats), np.array(centers)

QS_SESSIONS = ["Session-2026-06-16--16-09-46_all_normal_qs_labels",
               "Session-2026-06-16--16-22-53_all_normal_qs_labels",
               "Session-2026-06-17--10-26-19_IMU_all_normal_qs_labels"]

# ---- (a) bearing detector applied to qs (wobble) sessions ----
bscore_w, lab_w = [], []
for name in QS_SESSIONS:
    sd = os.path.join(L.DATA_ROOT, name)
    imu = L.load_imu(sd); fs = L.imu_fs(imu); labels = L.load_labels(sd)
    feats, centers = F.extract_windows(imu, fs)
    s = bearing.predict_proba(feats)[:, 1]
    y = np.full(len(centers), -1)
    for _, r in labels.iterrows():
        if str(r["label"]) == "qs_wobbly": y[(centers>=r["start"])&(centers<r["end"])] = 1
        elif str(r["label"]) == "qs_smooth": y[(centers>=r["start"])&(centers<r["end"])] = 0
    bscore_w.append(s); lab_w.append(y)
bscore_w = np.concatenate(bscore_w); lab_w = np.concatenate(lab_w)
fire_smooth = (bscore_w[lab_w==0] >= BEAR_THR).mean()
fire_wobbly = (bscore_w[lab_w==1] >= BEAR_THR).mean()
# bearing detector's own FPR on its training negatives (reference)
ref_fpr = (bearing.predict_proba(Xb[yb==0])[:,1] >= BEAR_THR).mean()
print("(a) BEARING detector on QS-session windows (thr=%.3f):" % BEAR_THR)
print(f"    fires on qs_smooth: {fire_smooth*100:.1f}%   on qs_wobbly: {fire_wobbly*100:.1f}%")
print(f"    (reference: bearing detector FPR on its own normals = {ref_fpr*100:.1f}%)")

# ---- (b) wobble detector applied to bearing-fault windows ----
wscore_f, lab_f = [], []
for name in F.FAULT_SESSIONS:
    sd = os.path.join(L.DATA_ROOT, name)
    imu = L.load_imu(sd)
    if imu is None: continue
    fs = L.imu_fs(imu); labels = L.load_labels(sd)
    feats, centers = qs_features(imu, fs)
    s = wobble.predict_proba(feats)[:, 1]
    y = F.window_labels(centers, labels)   # 1 = bearing fault
    wscore_f.append(s); lab_f.append(y)
wscore_f = np.concatenate(wscore_f); lab_f = np.concatenate(lab_f)
fire_fault = (wscore_f[lab_f==1] >= WOB_THR).mean()
fire_norm = (wscore_f[lab_f==0] >= WOB_THR).mean()
print(f"\n(b) WOBBLE detector on bearing-session windows (thr=%.2f):" % WOB_THR)
print(f"    fires on bearing-FAULT windows: {fire_fault*100:.1f}%   on normal: {fire_norm*100:.1f}%")

fig, ax = plt.subplots(1, 2, figsize=(14, 5))
ax[0].hist(bscore_w[lab_w==0], bins=30, density=True, alpha=0.6, color="C0", label="qs_smooth")
ax[0].hist(bscore_w[lab_w==1], bins=30, density=True, alpha=0.6, color="C2", label="qs_wobbly")
ax[0].axvline(BEAR_THR, color="k", ls="--", label=f"bearing thr {BEAR_THR}")
ax[0].set_title("(a) Bearing-detector score on WOBBLE windows\n(want: most below threshold)")
ax[0].set_xlabel("bearing fault score"); ax[0].legend()
ax[1].hist(wscore_f[lab_f==0], bins=30, density=True, alpha=0.6, color="C0", label="bearing normal")
ax[1].hist(wscore_f[lab_f==1], bins=30, density=True, alpha=0.6, color="C3", label="bearing fault")
ax[1].axvline(WOB_THR, color="k", ls="--", label=f"wobble thr {WOB_THR}")
ax[1].set_title("(b) Wobble-detector score on BEARING windows\n(want: faults not flagged as wobble)")
ax[1].set_xlabel("wobble score"); ax[1].legend()
fig.suptitle("Detector cross-talk — are the two fault signatures orthogonal?", fontsize=13)
plt.tight_layout()
out = os.path.join(FIG, "detector-crosstalk.png")
plt.savefig(out, dpi=120); print("\nwrote", out)
