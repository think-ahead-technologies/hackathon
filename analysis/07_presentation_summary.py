"""Presentation-grade summary with bootstrap confidence ranges.

Produces a single scorecard figure covering both fault characteristics found so
far (bearing fault, quasi-static wobble), each with validated AUC + 95% CI, ROC
curves, and effect sizes. Numbers printed are paste-ready for slides."""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score, roc_curve
import features as F

OUT = os.path.join(os.path.dirname(__file__), "out")
FIG = os.path.join(os.path.dirname(__file__), "figures")
rng = np.random.default_rng(0)


def boot_auc_ci(y, score, n=3000):
    obs = roc_auc_score(y, score)
    b = []
    for _ in range(n):
        idx = rng.choice(len(y), len(y))
        if len(set(y[idx])) < 2:
            continue
        b.append(roc_auc_score(y[idx], score[idx]))
    return obs, np.percentile(b, 2.5), np.percentile(b, 97.5)


def mean_ci(x, n=3000):
    m = x.mean(); b = [rng.choice(x, len(x)).mean() for _ in range(n)]
    return m, np.percentile(b, 2.5), np.percentile(b, 97.5)


# ---- Bearing fault: recompute LOSO OOF scores ----
d = np.load(os.path.join(OUT, "dataset.npz"), allow_pickle=True)
X, y, grp = d["X"], d["y"], d["grp"]
fault_sess = sorted(set(grp[y == 1]))
oof = np.full(len(y), np.nan)
for held in fault_sess:
    tr = grp != held; te = grp == held
    clf = RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                 random_state=0, n_jobs=-1).fit(X[tr], y[tr])
    oof[te] = clf.predict_proba(X[te])[:, 1]
m = ~np.isnan(oof)
yb, sb = y[m], oof[m]
auc_b, lo_b, hi_b = boot_auc_ci(yb, sb)

# ---- Wobble: load OOF from script 06 ----
q = np.load(os.path.join(OUT, "qs_dataset.npz"), allow_pickle=True)
yw, sw = q["y"], q["oof"]
auc_w, lo_w, hi_w = boot_auc_ci(yw, sw)

# ---- Bearing effect size: HF vib fold-change fault/baseline (session 13-49-38) ----
import imloader as L
from scipy import signal
sd = os.path.join(L.DATA_ROOT, "Session-2026-06-17--13-49-38_faulty_bearing")
imu = L.load_imu(sd); labels = L.load_labels(sd); fs = L.imu_fs(imu); t = imu["t"].values
am = np.linalg.norm(imu[["ax", "ay", "az"]].values, axis=1)
bb, aa = signal.butter(4, 5.0 / (fs / 2), btype="high")
env = np.sqrt(np.convolve(signal.filtfilt(bb, aa, am) ** 2, np.ones(int(0.2 * fs)) / int(0.2 * fs), mode="same"))
fm = L.label_mask(t, labels, names=("fault",))
ratio = env[fm].mean() / env[~fm].mean()

print("=" * 64)
print("PASTE-READY NUMBERS (95% bootstrap CI)")
print("=" * 64)
print(f"Bearing fault detector  : ROC-AUC = {auc_b:.3f}  [{lo_b:.3f}, {hi_b:.3f}]   "
      f"(LOSO, {int(yb.sum())} fault / {len(yb)} windows)")
print(f"Wobble (qs) detector    : ROC-AUC = {auc_w:.3f}  [{lo_w:.3f}, {hi_w:.3f}]   "
      f"(leave-1-segment-out, {int(yw.sum())} wobbly / {len(yw)} windows)")
print(f"Bearing HF-vib effect   : fault/baseline = {ratio:.2f}x")

# ---- FIGURE ----
fig = plt.figure(figsize=(16, 9))
gs = fig.add_gridspec(2, 2, height_ratios=[1.1, 1])

# ROC curves
ax = fig.add_subplot(gs[0, 0])
for yy, ss, lab, c in [(yb, sb, "Bearing fault", "C3"), (yw, sw, "Wobble (qs)", "C0")]:
    fpr, tpr, _ = roc_curve(yy, ss)
    a = roc_auc_score(yy, ss)
    ax.plot(fpr, tpr, color=c, lw=2, label=f"{lab}  AUC={a:.3f}")
ax.plot([0, 1], [0, 1], "--", color="grey")
ax.set_xlabel("false positive rate"); ax.set_ylabel("true positive rate")
ax.set_title("Validated detector ROC (held-out)"); ax.legend(loc="lower right"); ax.grid(alpha=0.3)

# AUC with CI
ax = fig.add_subplot(gs[0, 1])
names = ["Bearing fault\n(LOSO)", "Wobble qs\n(leave-1-seg-out)"]
aucs = [auc_b, auc_w]; los = [lo_b, lo_w]; his = [hi_b, hi_w]
yp = np.arange(len(names))
ax.errorbar(aucs, yp, xerr=[np.array(aucs) - np.array(los), np.array(his) - np.array(aucs)],
            fmt="o", color="k", capsize=6, ms=9)
for i, (a, l, h) in enumerate(zip(aucs, los, his)):
    ax.text(a, yp[i] + 0.12, f"{a:.3f}  [{l:.3f}, {h:.3f}]", ha="center")
ax.axvline(0.5, ls="--", color="grey", label="chance")
ax.set_yticks(yp); ax.set_yticklabels(names); ax.set_xlim(0.45, 1.0)
ax.set_xlabel("ROC-AUC (95% CI)"); ax.set_title("Detector performance with confidence ranges")
ax.grid(axis="x", alpha=0.3)

# Bearing feature distribution overlay (HF vib envelope)
ax = fig.add_subplot(gs[1, 0])
bins = np.linspace(0, np.percentile(env, 99.5), 50)
ax.hist(env[~fm], bins=bins, density=True, alpha=0.55, color="C0", label="baseline")
ax.hist(env[fm], bins=bins, density=True, alpha=0.6, color="C3", label="fault")
ax.set_title(f"Bearing: HF-accel vibration envelope  (fault = {ratio:.1f}x baseline)")
ax.set_xlabel("RMS envelope >5 Hz (m/s²)"); ax.legend()

# Wobble feature distribution (sway)
ax = fig.add_subplot(gs[1, 1])
Xw = q["X"]; fn = list(q["feature_names"]); si = fn.index("sway_rms")
a, b = Xw[yw == 1, si], Xw[yw == 0, si]
bins = np.linspace(np.percentile(np.r_[a, b], 1), np.percentile(np.r_[a, b], 99), 30)
ax.hist(b, bins=bins, density=True, alpha=0.55, color="C0", label="smooth")
ax.hist(a, bins=bins, density=True, alpha=0.6, color="C3", label="wobbly")
ax.set_title("Wobble: lateral sway RMS (<3 Hz)"); ax.set_xlabel("sway RMS (m/s²)"); ax.legend()

fig.suptitle("Conveyor fault detection from box IMU — two validated fault signatures", fontsize=15)
plt.tight_layout()
out = os.path.join(FIG, "detector-scorecard.png")
plt.savefig(out, dpi=120); print("\nwrote", out)
