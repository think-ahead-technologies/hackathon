"""Recalibrate the wobble (quasi-static instability) detector to be regime-robust.

Problem found in detector-crosstalk.png: the wobble model was trained on the
slow quasi-static test runs and over-fires on the live running loop (it reads
normal running motion as 'wobbly'). Fix: judge wobble as a *relative* anomaly --
"wobblier than THIS recording's own baseline" -- by robustly standardising each
feature per recording (median / inter-quartile range) before scoring. That is a
per-unit baseline, which should transfer across motion regimes.

Caveat: we have NO wobble/smooth labels on running-loop data, so we can only
validate (a) in-regime accuracy is retained and (b) over-firing on running-normal
drops. True running-wobble recall needs labels we don't yet have.
"""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import signal
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.metrics import roc_auc_score
import imloader as L
import features as F

FIG = os.path.join(os.path.dirname(__file__), "figures")

QS_SESSIONS = [
    "Session-2026-06-16--16-09-46_all_normal_qs_labels",
    "Session-2026-06-16--16-22-53_all_normal_qs_labels",
    "Session-2026-06-17--10-26-19_IMU_all_normal_qs_labels",
]
WIN_S, HOP_S = 1.0, 0.5
QS_FEATURES = ["tilt_std", "gyro_lf_rms", "sway_rms", "acc_lf_std",
               "dom_freq", "dom_peak_frac", "jerk_rms"]


def qs_features(imu, fs):
    t = imu["t"].values
    acc = imu[["ax", "ay", "az"]].values; gyr = imu[["gx", "gy", "gz"]].values
    bl, al = signal.butter(2, 5.0/(fs/2), btype="low")
    accl = signal.filtfilt(bl, al, acc, axis=0)
    roll = np.degrees(np.arctan2(accl[:, 1], accl[:, 2]))
    pitch = np.degrees(np.arctan2(-accl[:, 0], np.hypot(accl[:, 1], accl[:, 2])))
    gyr_mag = np.linalg.norm(gyr, axis=1); gyr_lf = signal.filtfilt(bl, al, gyr_mag)
    bsw, asw = signal.butter(2, 3.0/(fs/2), btype="low")
    sway = signal.filtfilt(bsw, asw, np.hypot(acc[:, 0], acc[:, 1]))
    acc_mag = np.linalg.norm(acc, axis=1); acc_lf = signal.filtfilt(bl, al, acc_mag)
    jerk = np.gradient(acc_mag)*fs
    w = int(round(WIN_S*fs)); h = int(round(HOP_S*fs))
    freqs = np.fft.rfftfreq(w, 1.0/fs); fb = (freqs >= 0.5) & (freqs <= 10)
    feats, centers = [], []
    for s in range(0, len(t)-w+1, h):
        e = s+w; g = gyr_mag[s:e]-gyr_mag[s:e].mean()
        sp = np.abs(np.fft.rfft(g*np.hanning(w)))**2; bd = sp[fb]
        feats.append([np.std(np.concatenate([roll[s:e], pitch[s:e]])),
                      np.sqrt(np.mean(gyr_lf[s:e]**2)), np.std(sway[s:e]), np.std(acc_lf[s:e]),
                      freqs[fb][np.argmax(bd)] if bd.size else 0.0, bd.max()/(sp.sum()+1e-12),
                      np.sqrt(np.mean(jerk[s:e]**2))])
        centers.append((t[s]+t[e-1])/2)
    return np.array(feats), np.array(centers)


def robust_standardise(Xs):
    """z-score per feature using median / IQR of THIS recording (per-unit baseline)."""
    med = np.median(Xs, axis=0)
    iqr = np.percentile(Xs, 75, axis=0) - np.percentile(Xs, 25, axis=0)
    iqr[iqr == 0] = 1.0
    return (Xs - med) / iqr


# ---- build standardised quasi-static dataset ----
Xn, Xraw, y, seg = [], [], [], []
off = 0
for si, name in enumerate(QS_SESSIONS):
    sd = os.path.join(L.DATA_ROOT, name)
    imu = L.load_imu(sd); labels = L.load_labels(sd); fs = L.imu_fs(imu)
    f, c = qs_features(imu, fs)
    yi = np.full(len(c), -1); segi = np.full(len(c), -1); k = 0
    for _, r in labels.iterrows():
        lab = str(r["label"])
        if lab not in ("qs_smooth", "qs_wobbly"):
            continue
        m = (c >= r["start"]) & (c < r["end"])
        yi[m] = 1 if lab == "qs_wobbly" else 0; segi[m] = k; k += 1
    fn = robust_standardise(f)        # per-recording standardisation
    keep = yi >= 0
    Xn.append(fn[keep]); Xraw.append(f[keep]); y.append(yi[keep])
    seg.append(np.where(segi[keep] >= 0, segi[keep] + off, -1)); off += k
Xn = np.vstack(Xn); Xraw = np.vstack(Xraw); y = np.concatenate(y); seg = np.concatenate(seg)


def loso_auc(X):
    oof = np.full(len(y), np.nan)
    for tr, te in LeaveOneGroupOut().split(X, y, seg):
        clf = RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                     random_state=0, n_jobs=-1).fit(X[tr], y[tr])
        oof[te] = clf.predict_proba(X[te])[:, 1]
    return roc_auc_score(y, oof), oof

auc_raw, _ = loso_auc(Xraw)
auc_norm, oof_norm = loso_auc(Xn)
print(f"In-regime accuracy (qs_wobbly vs qs_smooth), leave-one-segment-out:")
print(f"  absolute features  : ROC-AUC = {auc_raw:.3f}")
print(f"  per-recording (relative) features: ROC-AUC = {auc_norm:.3f}")

# pick threshold on the standardised model at ~16% false positives in-regime
from sklearn.metrics import roc_curve
fpr, tpr, thr = roc_curve(y, oof_norm)
i = np.searchsorted(fpr, 0.16); i = min(i, len(thr)-1); THR = thr[i]

# train final standardised model on all quasi-static data
final = RandomForestClassifier(n_estimators=300, class_weight="balanced",
                               random_state=0, n_jobs=-1).fit(Xn, y)

# ---- apply to running-loop (bearing) sessions, standardised per recording ----
def fire_rate(model, standardise):
    rates = []
    for name in F.FAULT_SESSIONS:
        sd = os.path.join(L.DATA_ROOT, name)
        imu = L.load_imu(sd)
        if imu is None:
            continue
        fs = L.imu_fs(imu); labels = L.load_labels(sd)
        f, c = qs_features(imu, fs)
        ff = robust_standardise(f) if standardise else f
        s = model.predict_proba(ff)[:, 1]
        nf = F.window_labels(c, labels) == 0    # running-normal windows
        rates.append((s[nf] >= THR).mean())
    return np.mean(rates)

# old absolute model (trained on raw features) for comparison
old = RandomForestClassifier(n_estimators=300, class_weight="balanced",
                             random_state=0, n_jobs=-1).fit(Xraw, y)
# old model used thr 0.5; new uses THR on standardised
old_fire = []
for name in F.FAULT_SESSIONS:
    sd = os.path.join(L.DATA_ROOT, name); imu = L.load_imu(sd)
    if imu is None: continue
    fs = L.imu_fs(imu); labels = L.load_labels(sd)
    f, c = qs_features(imu, fs); s = old.predict_proba(f)[:, 1]
    nf = F.window_labels(c, labels) == 0
    old_fire.append((s[nf] >= 0.5).mean())
old_fire = np.mean(old_fire)
new_fire = fire_rate(final, standardise=True)

print(f"\nOver-firing on running-NORMAL windows (should be low ~ the in-regime FPR 0.16):")
print(f"  old absolute model  : {old_fire*100:.0f}%   (the domain-shift failure)")
print(f"  recalibrated relative model: {new_fire*100:.0f}%")

fig, ax = plt.subplots(1, 2, figsize=(13, 5))
ax[0].bar(["absolute\n(old)", "per-recording\n(recalibrated)"], [auc_raw, auc_norm],
          color=["C7", "C0"]); ax[0].set_ylim(0.5, 1.0)
ax[0].set_ylabel("in-regime ROC-AUC"); ax[0].set_title("Accuracy retained (wobbly vs smooth)")
for i2, v in enumerate([auc_raw, auc_norm]): ax[0].text(i2, v+0.01, f"{v:.3f}", ha="center")
ax[1].bar(["absolute\n(old)", "per-recording\n(recalibrated)"], [old_fire*100, new_fire*100],
          color=["C3", "C2"]); ax[1].set_ylabel("% running-normal flagged as wobbly")
ax[1].set_title("Over-firing on live running motion (lower = better)")
for i2, v in enumerate([old_fire*100, new_fire*100]): ax[1].text(i2, v+1, f"{v:.0f}%", ha="center")
fig.suptitle("Wobble detector recalibration: per-recording (relative) features fix the regime shift", fontsize=12)
plt.tight_layout()
out = os.path.join(FIG, "wobble-recalibration.png")
plt.savefig(out, dpi=120); print("\nwrote", out)
