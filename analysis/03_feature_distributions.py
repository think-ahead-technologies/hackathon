"""Build the pooled windowed dataset across all sessions and plot fault vs
normal feature distributions. Saves dataset to out/dataset.npz."""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import imloader as L
import features as F

FIG = os.path.join(os.path.dirname(__file__), "figures")
OUT = os.path.join(os.path.dirname(__file__), "out")
os.makedirs(OUT, exist_ok=True)

X, y, grp = [], [], []   # features, label, session index
sess_names = []

all_sessions = F.FAULT_SESSIONS + F.NORMAL_SESSIONS
for si, name in enumerate(all_sessions):
    sd = os.path.join(L.DATA_ROOT, name)
    imu = L.load_imu(sd)
    if imu is None:
        print("skip (no IMU):", name); continue
    fs = L.imu_fs(imu)
    feats, centers = F.extract_windows(imu, fs)
    labels = L.load_labels(sd)
    yi = F.window_labels(centers, labels)
    X.append(feats); y.append(yi); grp.append(np.full(len(feats), si))
    sess_names.append(name)
    print(f"{name:70s} windows={len(feats):5d} fault={yi.sum():4d}")

X = np.vstack(X); y = np.concatenate(y); grp = np.concatenate(grp)
print(f"\nTOTAL windows={len(X)}  fault={y.sum()}  normal={(y==0).sum()}  "
      f"prevalence={y.mean()*100:.2f}%")
np.savez(os.path.join(OUT, "dataset.npz"), X=X, y=y, grp=grp,
         feature_names=F.FEATURE_NAMES, sess_names=sess_names)

# Distribution plots: fault vs normal per feature
fig, axes = plt.subplots(2, 5, figsize=(20, 8))
for i, fn in enumerate(F.FEATURE_NAMES):
    ax = axes[i // 5][i % 5]
    a = X[y == 1, i]; b = X[y == 0, i]
    lo, hi = np.percentile(np.concatenate([a, b]), [1, 99])
    bins = np.linspace(lo, hi, 50)
    ax.hist(b, bins=bins, density=True, alpha=0.5, label="normal", color="C0")
    ax.hist(a, bins=bins, density=True, alpha=0.5, label="fault", color="C3")
    # separability: AUC-like (Mann-Whitney)
    from scipy.stats import mannwhitneyu
    try:
        U = mannwhitneyu(a, b).statistic
        auc = U / (len(a) * len(b))
    except Exception:
        auc = float("nan")
    ax.set_title(f"{fn}\nAUC={auc:.3f}")
    if i == 0:
        ax.legend()
fig.suptitle("IMU window features: fault (red) vs normal (blue) — AUC = univariate separability")
plt.tight_layout()
out = os.path.join(FIG, "bearing-imu-feature-separability.png")
plt.savefig(out, dpi=100)
print("wrote", out)

# rank features by separability
from scipy.stats import mannwhitneyu
print("\nUnivariate separability (|AUC-0.5|):")
rows = []
for i, fn in enumerate(F.FEATURE_NAMES):
    a, b = X[y == 1, i], X[y == 0, i]
    auc = mannwhitneyu(a, b).statistic / (len(a) * len(b))
    rows.append((abs(auc - 0.5), auc, fn))
for d, auc, fn in sorted(rows, reverse=True):
    print(f"  {fn:16s} AUC={auc:.3f}")
