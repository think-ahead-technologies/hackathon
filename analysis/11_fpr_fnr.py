"""False-positive rate (FPR) and false-negative rate (FNR) for both validated
detectors, at explicit operating points, with bootstrap 95% CIs.

  FPR = FP/(FP+TN)  false alarms among actual negatives  (= 1 - specificity)
  FNR = FN/(FN+TP)  missed faults among actual positives (= 1 - recall)

Scores are the held-out (out-of-fold) probabilities, so these are generalisation
rates, not training fit."""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.ensemble import RandomForestClassifier
import features as F

OUT = os.path.join(os.path.dirname(__file__), "out")
FIG = os.path.join(os.path.dirname(__file__), "figures")
rng = np.random.default_rng(0)


def rates(y, score, thr):
    pred = score >= thr
    P = y == 1; N = y == 0
    fnr = np.sum(pred[P] == 0) / max(P.sum(), 1)
    fpr = np.sum(pred[N] == 1) / max(N.sum(), 1)
    return fpr, fnr


def boot_rates(y, score, thr, n=3000):
    fp, fn = [], []
    for _ in range(n):
        idx = rng.choice(len(y), len(y))
        if len(set(y[idx])) < 2:
            continue
        a, b = rates(y[idx], score[idx], thr)
        fp.append(a); fn.append(b)
    ci = lambda v: (np.percentile(v, 2.5), np.percentile(v, 97.5))
    return ci(fp), ci(fn)


def pick_thresholds(y, score):
    ts = np.unique(score)
    if len(ts) > 600:
        ts = np.quantile(score, np.linspace(0, 1, 600))
    fprs = np.array([rates(y, score, t)[0] for t in ts])
    fnrs = np.array([rates(y, score, t)[1] for t in ts])
    eer = ts[np.argmin(np.abs(fprs - fnrs))]                 # equal error rate
    you = ts[np.argmax((1 - fnrs) - fprs)]                   # Youden J (max TPR-FPR)
    # high recall: FNR <= 10%  -> smallest FPR achieving it
    ok = np.where(fnrs <= 0.10)[0]
    hr = ts[ok[np.argmin(fprs[ok])]] if len(ok) else you
    # low FPR: FPR <= 5% -> smallest FNR achieving it
    ok2 = np.where(fprs <= 0.05)[0]
    lf = ts[ok2[np.argmin(fnrs[ok2])]] if len(ok2) else you
    return {"equal-error": eer, "Youden-opt": you,
            "high-recall (FNR<=10%)": hr, "low-FPR (FPR<=5%)": lf}, ts, fprs, fnrs


def report(name, y, score, prevalence_note):
    print("=" * 74)
    print(f"{name}   (n={len(y)}, faults={int(y.sum())}, {prevalence_note})")
    print("=" * 74)
    ops, ts, fprs, fnrs = pick_thresholds(y, score)
    print(f"{'operating point':24s} {'thr':>6s} {'FPR [95% CI]':>22s} {'FNR [95% CI]':>22s}")
    rows = {}
    for label, thr in ops.items():
        fpr, fnr = rates(y, score, thr)
        (fpl, fph), (fnl, fnh) = boot_rates(y, score, thr)
        print(f"{label:24s} {thr:6.3f} "
              f"{fpr:5.2f} [{fpl:.2f},{fph:.2f}]   {fnr:5.2f} [{fnl:.2f},{fnh:.2f}]")
        rows[label] = (thr, fpr, fnr)
    return ts, fprs, fnrs, rows


# ---- Bearing: recompute LOSO OOF ----
d = np.load(os.path.join(OUT, "dataset.npz"), allow_pickle=True)
X, y, grp = d["X"], d["y"], d["grp"]
fault_sess = sorted(set(grp[y == 1]))
oof = np.full(len(y), np.nan)
for held in fault_sess:
    tr = grp != held; te = grp == held
    clf = RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                 random_state=0, n_jobs=-1).fit(X[tr], y[tr])
    oof[te] = clf.predict_proba(X[te])[:, 1]
m = ~np.isnan(oof); yb, sb = y[m], oof[m]
tsb, fprb, fnrb, rb = report("BEARING (window-level, LOSO)", yb, sb,
                             "prevalence 5.1% of held-out windows")

# ---- Wobble: OOF from script 06 ----
q = np.load(os.path.join(OUT, "qs_dataset.npz"), allow_pickle=True)
yw, sw = q["y"], q["oof"]
tsw, fprw, fnrw, rw = report("WOBBLE qs (window-level, leave-1-segment-out)", yw, sw,
                             "balanced ~52% wobbly")

# ---- Figure: FPR & FNR vs threshold for both ----
fig, ax = plt.subplots(1, 2, figsize=(16, 6))
for a, ts, fpr, fnr, rows, title in [
    (ax[0], tsb, fprb, fnrb, rb, "Bearing (window, LOSO)"),
    (ax[1], tsw, fprw, fnrw, rw, "Wobble qs (window, leave-1-seg-out)")]:
    a.plot(ts, fpr, color="C3", label="FPR (false alarms)")
    a.plot(ts, fnr, color="C0", label="FNR (misses)")
    thr_eer = rows["equal-error"][0]
    a.axvline(thr_eer, color="grey", ls="--",
              label=f"EER thr={thr_eer:.2f}\nFPR=FNR≈{rows['equal-error'][1]:.2f}")
    a.set_xlabel("decision threshold"); a.set_ylabel("rate")
    a.set_title(title); a.legend(); a.grid(alpha=0.3); a.set_ylim(0, 1)
fig.suptitle("False-positive vs false-negative rate trade-off (held-out scores)", fontsize=14)
plt.tight_layout()
out = os.path.join(FIG, "detector-fpr-fnr-tradeoff.png")
plt.savefig(out, dpi=120); print("\nwrote", out)

print("\nNOTE: bearing rates are WINDOW-level (1 s). At 5.1% prevalence a 10% FPR is "
      "many false windows; aggregating to per-lap EVENTS (require k consecutive "
      "flags) trades a little FNR for a large FPR drop — recommended for alarms.")
