"""How defensible is 'more defective bearings -> stronger signature'?

The evening sessions added bearings progressively (one continuous experiment):
  18-24 base  <  18-53 (+4)  <  19-11 (cumulated 8)  <  19-25 (+2 broken)
We test whether fault-event DURATION and per-lap FAULT-TIME FRACTION track that
ordinal severity, with bootstrap CIs and significance tests -- and report honestly
whether it's strong enough to present.
"""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import stats
import imloader as L

FIG = os.path.join(os.path.dirname(__file__), "figures")

SEV = [  # (ordinal rank, label, session)  -- counts are the colleagues' cumulative
         # progression; exact base count ambiguous (~0-1), confirm with the team.
    (1, "~0-1",  "Session-2026-06-17--18-24-09-faulty-bearing"),
    (2, "~4-5",  "Session-2026-06-17--18-53-07-additional-4-defective-idler-wheel-bearings"),
    (3, "8",     "Session-2026-06-17--19-11-06-cummulated-8-defective-idler-wheel-bearings"),
    (4, "10",    "Session-2026-06-17--19-25-09-additional-2-broken-bearings"),
]

def boot_ci(x, n=5000):
    rng = np.random.default_rng(0)
    b = [rng.choice(x, len(x)).mean() for _ in range(n)]
    return np.mean(x), np.percentile(b, 2.5), np.percentile(b, 97.5)

ranks_per_event, durs_per_event = [], []
print(f"{'session':14s} {'#evt':>4s} {'mean dur (s) [95% CI]':>26s} {'lap(s)':>7s} {'fault-time/lap':>14s}")
session_means = []
labs, means, los, his, fracs = [], [], [], [], []
for rank, lab, name in SEV:
    sd = os.path.join(L.DATA_ROOT, name)
    labels = L.load_labels(sd)
    f = labels[labels["label"] == "fault"]
    d = f["length"].values
    m, lo, hi = boot_ci(d)
    centers = ((f["start"] + f["end"]) / 2).values
    lap = np.median(np.diff(centers)) if len(centers) > 2 else np.nan
    # fault-time fraction per lap = (total fault seconds) / (#laps * lap)
    span = centers[-1] - centers[0]
    frac = d.sum() / span if span > 0 else np.nan
    print(f"{lab:14s} {len(d):4d} {m:6.2f} [{lo:.2f}, {hi:.2f}]        {lap:7.1f} {frac*100:12.1f}%")
    ranks_per_event += [rank] * len(d)
    durs_per_event += list(d)
    session_means.append((rank, m))
    labs.append(lab); means.append(m); los.append(lo); his.append(hi); fracs.append(frac * 100)

ranks_per_event = np.array(ranks_per_event); durs_per_event = np.array(durs_per_event)

# 1. Trend test across ordinal severity (per-event)
rho, p = stats.spearmanr(ranks_per_event, durs_per_event)
print(f"\nSpearman (ordinal severity vs per-event duration): rho={rho:.2f}, p={p:.1e}")

# 2. base vs multi-defect contrast (the honest, robust comparison)
base = durs_per_event[ranks_per_event == 1]
multi = durs_per_event[ranks_per_event > 1]
U, pmw = stats.mannwhitneyu(multi, base, alternative="greater")
auc = U / (len(multi) * len(base))
print(f"base vs (multi-defect): durations longer? AUC={auc:.2f}, Mann-Whitney p={pmw:.1e}")
print(f"  base mean={base.mean():.2f}s (n={len(base)})  multi mean={multi.mean():.2f}s (n={len(multi)})")

# 3. Is it monotone among the multi-defect levels (dose-response), or saturated?
multi_means = [m for r, m in session_means if r > 1]
print(f"\nmulti-defect session means: {[round(x,2) for x in multi_means]} "
      f"-> {'SATURATED (no fine dose-response)' if max(multi_means)-min(multi_means) < 0.5 else 'still rising'}")

x = np.arange(len(labs))
fig, ax = plt.subplots(1, 2, figsize=(13, 5))
ax[0].errorbar(x, means, yerr=[np.array(means)-np.array(los), np.array(his)-np.array(means)],
               fmt="o-", color="C3", capsize=5, ms=8)
ax[0].set_xticks(x); ax[0].set_xticklabels(labs, rotation=15)
ax[0].set_ylabel("mean fault-burst duration (s)"); ax[0].set_xlabel("approx. defective bearings")
ax[0].set_title(f"Burst duration vs approx. defective-bearing count\nbase vs multi: AUC={auc:.2f}, p={pmw:.0e} (saturates)")
ax[0].grid(alpha=0.3)
ax[1].bar(x, fracs, color="C0")
ax[1].set_xticks(x); ax[1].set_xticklabels(labs, rotation=15)
ax[1].set_ylabel("fault-time fraction per lap (%)"); ax[1].set_xlabel("approx. defective bearings")
ax[1].set_title("Share of each lap spent in a fault zone")
for i, v in enumerate(fracs): ax[1].text(i, v+0.2, f"{v:.1f}%", ha="center")
fig.suptitle("Fault severity: signature grows with defect burden, then saturates (direction, not a counter)", fontsize=12)
plt.tight_layout()
out = os.path.join(FIG, "fault-severity-trend.png")
plt.savefig(out, dpi=120); print("wrote", out)

print("""
VERDICT (for the deck):
 * Defensible: a clear, significant step up from the single-locus 'base' run (~2 s
   bursts) to multi-defect runs (~4 s) -- bigger/longer signature with more defects.
 * NOT defensible: a fine-grained 'count the bearings' dose-response -- the
   multi-defect levels saturate, the absolute counts are ambiguous, and track
   layout differs. Present as a *trend/direction*, not a calibrated severity meter.
""")
