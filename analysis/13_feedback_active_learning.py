"""Potential of the technician-in-the-loop feedback system, simulated.

In production a technician triages flagged events and marks FP/FN; those
corrections retrain the model. That is *active learning* — the model is taught
exactly the cases it gets wrong/is unsure about, which is far more label-efficient
than random labelling.

We simulate it on the bearing dataset: hold out fixed TEST sessions, start from a
tiny labelled seed, and each round reveal labels for either (a) the windows the
model is most UNSURE about (uncertainty sampling ~ technician triaging borderline
flags) or (b) RANDOM windows (passive labelling). Track held-out FNR@FPR=0.20.
"""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score, roc_curve

OUT = os.path.join(os.path.dirname(__file__), "out")
FIG = os.path.join(os.path.dirname(__file__), "figures")
d = np.load(os.path.join(OUT, "dataset.npz"), allow_pickle=True)
X, y, grp = d["X"], d["y"], d["grp"]
snames = list(d["sess_names"])

# Fixed held-out TEST = 2 fault + 2 normal sessions (never labelled)
test_names = [
    "Session-2026-06-17--18-24-09-faulty-bearing",
    "Session-2026-06-17--19-25-09-additional-2-broken-bearings",
    "Session-2026-06-17--11-26-26_all_normal",
    "Session-2026-06-17--10-46-14_normal",
]
test_g = [snames.index(n) for n in test_names if n in snames]
test = np.isin(grp, test_g)
Xte, yte = X[test], y[test]
Xpool_all, ypool_all = X[~test], y[~test]
print(f"pool={len(Xpool_all)} (faults {ypool_all.sum()}), test={test.sum()} (faults {yte.sum()})")


def fnr_at_fpr(yt, sc, target_fpr=0.20):
    fpr, tpr, _ = roc_curve(yt, sc)
    i = np.searchsorted(fpr, target_fpr)
    i = min(i, len(tpr) - 1)
    return 1 - tpr[i]   # FNR = 1 - recall at that FPR


def run(strategy, seeds=4, seed0=40, batch=40, rounds=18):
    curves = []
    for s in range(seeds):
        rng = np.random.default_rng(s)
        idx = np.arange(len(Xpool_all))
        # stratified seed so both classes present
        pos = idx[ypool_all == 1]; neg = idx[ypool_all == 0]
        lab = np.r_[rng.choice(pos, seed0 // 2, replace=False),
                    rng.choice(neg, seed0 // 2, replace=False)]
        labset = set(lab.tolist())
        curve = []
        for r in range(rounds):
            li = np.array(sorted(labset))
            clf = RandomForestClassifier(n_estimators=150, class_weight="balanced",
                                         random_state=0, n_jobs=-1).fit(Xpool_all[li], ypool_all[li])
            sc = clf.predict_proba(Xte)[:, 1]
            curve.append((len(li), fnr_at_fpr(yte, sc)))
            # choose next batch from unlabelled pool
            un = np.array([i for i in idx if i not in labset])
            if len(un) == 0:
                break
            if strategy == "uncertainty":
                p = clf.predict_proba(Xpool_all[un])[:, 1]
                order = un[np.argsort(np.abs(p - 0.5))]    # most uncertain first
                pick = order[:batch]
            else:
                pick = rng.choice(un, min(batch, len(un)), replace=False)
            labset.update(pick.tolist())
        curves.append(curve)
    # align by round
    n = min(len(c) for c in curves)
    ns = np.array([curves[0][i][0] for i in range(n)])
    fnrs = np.array([[c[i][1] for c in curves] for i in range(n)])
    return ns, fnrs.mean(1), fnrs.std(1)


for strat, col in [("uncertainty", "C3"), ("random", "C0")]:
    ns, m, sd = run(strat)
    plt.plot(ns, m, color=col, marker="o", ms=3,
             label=f"{'technician-triage (uncertainty)' if strat=='uncertainty' else 'passive (random labelling)'}")
    plt.fill_between(ns, m - sd, m + sd, color=col, alpha=0.15)
    print(f"\n{strat}:")
    for nn, mm in zip(ns, m):
        print(f"  {nn:4d} labels -> FNR@FPR20 = {mm:.2f}")

plt.xlabel("number of labelled windows (technician corrections)")
plt.ylabel("held-out FNR @ FPR=0.20  (miss rate)")
plt.title("Feedback-loop potential: active learning vs passive labelling (bearing)")
plt.legend(); plt.grid(alpha=0.3); plt.ylim(0, 1)
plt.tight_layout()
out = os.path.join(FIG, "feedback-loop-potential.png")
plt.savefig(out, dpi=120); print("\nwrote", out)
