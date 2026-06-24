"""Second fault characteristic: quasi-static stability  qs_wobbly vs qs_smooth.

Unlike the bearing fault (high-frequency vibration envelope), 'wobble' is a
LOW-frequency motion-instability signature: the box rocks / oscillates. This is
exactly the band a 50 Hz IMU (Nyquist 25 Hz) *can* resolve. Features here are
deliberately low-frequency: orientation/tilt variability, gyro rocking energy,
sway, and the dominant sub-5 Hz motion peak.

qs_smooth / qs_wobbly are labeled *within the same recordings*, so smooth vs
wobbly contrast has no cross-session gain/mounting confound.
"""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import signal, stats
import imloader as L

FIG = os.path.join(os.path.dirname(__file__), "figures")
OUT = os.path.join(os.path.dirname(__file__), "out")

QS_SESSIONS = [
    "Session-2026-06-16--16-09-46_all_normal_qs_labels",
    "Session-2026-06-16--16-22-53_all_normal_qs_labels",
    "Session-2026-06-17--10-26-19_IMU_all_normal_qs_labels",
]

WIN_S, HOP_S = 1.0, 0.5
QS_FEATURES = [
    "tilt_std",       # std of roll/pitch angle from accel (deg) -> rocking
    "gyro_lf_rms",    # <5 Hz gyro magnitude RMS -> angular rocking energy
    "sway_rms",       # <3 Hz horizontal accel RMS -> lateral sway
    "acc_lf_std",     # <5 Hz accel magnitude std -> gross unsteadiness
    "dom_freq",       # dominant motion frequency 0.5-10 Hz (gyro)
    "dom_peak_frac",  # fraction of gyro energy in dominant peak -> periodic wobble
    "jerk_rms",       # RMS of accel derivative -> abruptness
]


def qs_features(imu, fs):
    t = imu["t"].values
    acc = imu[["ax", "ay", "az"]].values
    gyr = imu[["gx", "gy", "gz"]].values
    # tilt from accel (low-passed): roll & pitch
    bl, al = signal.butter(2, 5.0 / (fs / 2), btype="low")
    accl = signal.filtfilt(bl, al, acc, axis=0)
    roll = np.degrees(np.arctan2(accl[:, 1], accl[:, 2]))
    pitch = np.degrees(np.arctan2(-accl[:, 0], np.hypot(accl[:, 1], accl[:, 2])))
    gyr_mag = np.linalg.norm(gyr, axis=1)
    gyr_lf = signal.filtfilt(bl, al, gyr_mag)
    bsw, asw = signal.butter(2, 3.0 / (fs / 2), btype="low")
    sway = signal.filtfilt(bsw, asw, np.hypot(acc[:, 0], acc[:, 1]))
    acc_mag = np.linalg.norm(acc, axis=1)
    acc_lf = signal.filtfilt(bl, al, acc_mag)
    jerk = np.gradient(acc_mag) * fs

    w = int(round(WIN_S * fs)); h = int(round(HOP_S * fs))
    freqs = np.fft.rfftfreq(w, 1.0 / fs)
    fb = (freqs >= 0.5) & (freqs <= 10)
    feats, centers = [], []
    for s in range(0, len(t) - w + 1, h):
        e = s + w
        g = gyr_mag[s:e] - gyr_mag[s:e].mean()
        spec = np.abs(np.fft.rfft(g * np.hanning(w))) ** 2
        band = spec[fb]
        domf = freqs[fb][np.argmax(band)] if band.size else 0.0
        peak_frac = band.max() / (spec.sum() + 1e-12)
        feats.append([
            np.std(np.concatenate([roll[s:e], pitch[s:e]])),
            np.sqrt(np.mean(gyr_lf[s:e] ** 2)),
            np.std(sway[s:e]),
            np.std(acc_lf[s:e]),
            domf,
            peak_frac,
            np.sqrt(np.mean(jerk[s:e] ** 2)),
        ])
        centers.append((t[s] + t[e - 1]) / 2)
    return np.array(feats), np.array(centers)


def seg_label(centers, labels):
    """Return per-window: 1=wobbly, 0=smooth, -1=neither; and segment id."""
    y = np.full(len(centers), -1); seg = np.full(len(centers), -1)
    k = 0
    for _, r in labels.iterrows():
        lab = str(r["label"])
        if lab not in ("qs_smooth", "qs_wobbly"):
            continue
        m = (centers >= r["start"]) & (centers < r["end"])
        y[m] = 1 if lab == "qs_wobbly" else 0
        seg[m] = k; k += 1
    return y, seg


X, y, grp, seg = [], [], [], []
seg_off = 0
for si, name in enumerate(QS_SESSIONS):
    sd = os.path.join(L.DATA_ROOT, name)
    imu = L.load_imu(sd); labels = L.load_labels(sd)
    fs = L.imu_fs(imu)
    f, c = qs_features(imu, fs)
    yi, segi = seg_label(c, labels)
    keep = yi >= 0
    segi2 = np.where(segi >= 0, segi + seg_off, -1)
    seg_off = segi2[keep].max() + 1 if keep.any() else seg_off
    X.append(f[keep]); y.append(yi[keep]); grp.append(np.full(keep.sum(), si)); seg.append(segi2[keep])
    print(f"{name}: windows={keep.sum()} wobbly={int(yi[keep].sum())} smooth={int((yi[keep]==0).sum())}")

X = np.vstack(X); y = np.concatenate(y); grp = np.concatenate(grp); seg = np.concatenate(seg)
print(f"\nTOTAL: {len(X)} windows, {int(y.sum())} wobbly, {int((y==0).sum())} smooth, "
      f"{len(set(seg))} labeled segments")

# Univariate separability with bootstrap CI on AUC
from scipy.stats import mannwhitneyu
def auc_ci(a, b, n=2000):
    obs = mannwhitneyu(a, b).statistic / (len(a) * len(b))
    rng = np.random.default_rng(0); boots = []
    for _ in range(n):
        ai = rng.choice(a, len(a)); bi = rng.choice(b, len(b))
        boots.append(mannwhitneyu(ai, bi).statistic / (len(ai) * len(bi)))
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return obs, lo, hi

print("\nUnivariate separability (wobbly vs smooth), AUC [95% CI]:")
ranks = []
for i, fn in enumerate(QS_FEATURES):
    obs, lo, hi = auc_ci(X[y == 1, i], X[y == 0, i])
    ranks.append((abs(obs - 0.5), fn, obs, lo, hi))
for _, fn, obs, lo, hi in sorted(ranks, reverse=True):
    print(f"  {fn:14s} AUC={obs:.3f} [{lo:.3f}, {hi:.3f}]")

# Classifier: leave-one-segment-out (group=seg) to avoid leakage, pooled OOF AUC
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.metrics import roc_auc_score
logo = LeaveOneGroupOut()
oof = np.full(len(y), np.nan)
for tr, te in logo.split(X, y, seg):
    clf = RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                 random_state=0, n_jobs=-1).fit(X[tr], y[tr])
    oof[te] = clf.predict_proba(X[te])[:, 1]
auc = roc_auc_score(y, oof)
# bootstrap CI on the pooled classifier AUC
rng = np.random.default_rng(1); boots = []
for _ in range(2000):
    idx = rng.choice(len(y), len(y))
    if len(set(y[idx])) < 2: continue
    boots.append(roc_auc_score(y[idx], oof[idx]))
lo, hi = np.percentile(boots, [2.5, 97.5])
print(f"\nLeave-one-SEGMENT-out RF AUC = {auc:.3f}  [95% CI {lo:.3f}, {hi:.3f}]")

np.savez(os.path.join(OUT, "qs_dataset.npz"), X=X, y=y, seg=seg, oof=oof,
         feature_names=QS_FEATURES)

# Plot distributions for top-4 features
order = [fn for _, fn, *_ in sorted(ranks, reverse=True)][:4]
idxs = [QS_FEATURES.index(fn) for fn in order]
fig, axes = plt.subplots(1, 4, figsize=(20, 4.5))
for ax, i in zip(axes, idxs):
    a, b = X[y == 1, i], X[y == 0, i]
    lo2, hi2 = np.percentile(np.concatenate([a, b]), [1, 99])
    bins = np.linspace(lo2, hi2, 30)
    ax.hist(b, bins=bins, density=True, alpha=0.55, label="smooth", color="C0")
    ax.hist(a, bins=bins, density=True, alpha=0.55, label="wobbly", color="C3")
    obs, l, h = auc_ci(a, b, n=500)
    ax.set_title(f"{QS_FEATURES[i]}\nAUC={obs:.2f} [{l:.2f},{h:.2f}]")
    ax.legend()
fig.suptitle("Quasi-static stability: qs_wobbly (red) vs qs_smooth (blue) — low-frequency motion features")
plt.tight_layout()
out = os.path.join(FIG, "wobble-feature-separability.png")
plt.savefig(out, dpi=110); print("wrote", out)
