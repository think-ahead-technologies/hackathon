"""Better acoustic fault indicators, designed from what the ear/figure shows:
faults are HIGH-frequency, LOUD (absolute, not normalized), and SUSTAINED transients.
Compute fine-frame features and aggregate by max / median / active-fraction.
Compare to the weak first attempt (AUC 0.57)."""
import os, wave
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from scipy import signal, stats
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import LeaveOneOut
from sklearn.metrics import roc_auc_score, roc_curve

FIG = os.path.join(os.path.dirname(__file__), "figures")
T2 = os.path.join(os.path.dirname(__file__), "..", "data", "test2")
w = wave.open(os.path.join(T2, "merged_1600hz.wav"), "rb")
sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
mono = (np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32)/32768.0).reshape(-1, ch).mean(1)
w.close()
lab = pd.read_csv(os.path.join(T2, "labels.csv"))

# high-band signal (>2 kHz: above speech/engine rumble, where squeal/rattle live)
bb, aa = signal.butter(4, 2000/(sr/2), btype="high")
hi = signal.filtfilt(bb, aa, mono - mono.mean())
env_hi = np.abs(signal.hilbert(hi))
# global high-band noise floor (median over whole recording) for 'active' fraction
floor = np.median(env_hi) * 3

FR = int(0.064*sr); HOP = int(0.032*sr)
def frames(a):
    return [a[i:i+FR] for i in range(0, len(a)-FR+1, HOP)]

FEAT = ["hi_rms_max", "hi_rms_med", "hi_active_frac", "tonal_max_db",
        "crest_hi", "onset_rate", "hi_to_low_ratio"]
def clip_features(s, e):
    seg = mono[s:e] - mono[s:e].mean()
    h = hi[s:e]; ev = env_hi[s:e]
    fr_rms = np.array([np.sqrt(np.mean(x**2)) for x in frames(h)]) + 1e-9
    # tonal prominence per frame in 1-8kHz (squeal = strong narrow peak)
    ton = []
    for x in frames(seg):
        f, P = signal.welch(x, fs=sr, nperseg=min(512, len(x)))
        m = (f >= 1000)
        Pdb = 10*np.log10(P[m]+1e-12)
        ton.append(Pdb.max() - np.median(Pdb))
    ton = np.array(ton)
    # onsets: sharp rises in high-band envelope
    de = np.diff(ev); onsets = np.sum(de > (5*np.std(de)))
    # high vs low band absolute energy (character: high-pitched vs rumble/speech)
    lo = signal.filtfilt(*signal.butter(4, 2000/(sr/2), btype="low"), seg)
    hi_e = np.mean(h**2); lo_e = np.mean(lo**2) + 1e-12
    return [fr_rms.max(), np.median(fr_rms), np.mean(fr_rms > floor),
            ton.max(), fr_rms.max()/np.median(fr_rms), onsets/((e-s)/sr),
            10*np.log10(hi_e/lo_e)]

X, y = [], []
for _, r in lab.iterrows():
    s, e = int(r["t_start_s"]*sr), int(r["t_end_s"]*sr)
    if e-s < sr: continue
    X.append(clip_features(s, e)); y.append(int(r["fault"]))
X = np.array(X); y = np.array(y)

print("per-feature AUC (fault vs non-fault):")
aucs = {}
for i, fn in enumerate(FEAT):
    auc = stats.mannwhitneyu(X[y==1, i], X[y==0, i]).statistic / (y.sum()*(y==0).sum())
    aucs[fn] = auc
    print(f"  {fn:16s} AUC={auc:.2f}")

oof = np.zeros(len(y))
for tr, te in LeaveOneOut().split(X):
    clf = RandomForestClassifier(n_estimators=300, class_weight="balanced", random_state=0).fit(X[tr], y[tr])
    oof[te] = clf.predict_proba(X[te])[0, 1]
auc = roc_auc_score(y, oof)
rng = np.random.default_rng(0); b=[]
for _ in range(3000):
    idx = rng.choice(len(y), len(y))
    if len(set(y[idx]))<2: continue
    b.append(roc_auc_score(y[idx], oof[idx]))
lo, hi_ci = np.percentile(b, [2.5, 97.5])
fpr, tpr, thr = roc_curve(y, oof); fnr = 1-tpr; j = np.argmin(np.abs(fpr-fnr))
print(f"\nCombined leave-one-clip-out AUC = {auc:.2f} [95% CI {lo:.2f},{hi_ci:.2f}]  (was 0.57)")
print(f"equal-error: FPR={fpr[j]:.2f} FNR={fnr[j]:.2f}")
# best single indicator
best = max(aucs, key=lambda k: abs(aucs[k]-0.5)); bi = FEAT.index(best)
print(f"best single indicator: {best} (AUC {aucs[best]:.2f})")

fig, ax = plt.subplots(1, 2, figsize=(14, 5))
order = sorted(range(len(FEAT)), key=lambda i: abs(aucs[FEAT[i]]-0.5))
ax[0].barh([FEAT[i] for i in order], [aucs[FEAT[i]] for i in order], color="C0")
ax[0].axvline(0.5, color="grey", ls="--"); ax[0].set_xlabel("AUC (fault vs normal)")
ax[0].set_title("New acoustic indicators (absolute, high-band, transient-aware)")
a, bb2 = X[y==1, bi], X[y==0, bi]
tt = [(lab.iloc[k]["t_start_s"]+lab.iloc[k]["t_end_s"])/2 for k in range(len(X))]
ax[1].scatter(tt, X[:, bi], c=["C3" if v else "C0" for v in y], s=45)
ax[1].set_title(f"best indicator over time: {best} (red=fault)"); ax[1].set_xlabel("s"); ax[1].set_ylabel(best)
plt.tight_layout()
out = os.path.join(FIG, "audio-indicators.png")
plt.savefig(out, dpi=120); print("wrote", out)
