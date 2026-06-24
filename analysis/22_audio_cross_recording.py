"""Cross-recording validation of the acoustic fault indicator.

Two independently-labelled recordings now exist (Waldemar): data/test1 (54 clips,
7 fault) and data/test2 (24 clips, 6 fault). We test whether the high-band loudness
indicator GENERALISES across recordings, or needs per-recording calibration.

AUC = area under the ROC curve; FPR/FNR = false-positive / false-negative rate.
"""
import os, wave
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import signal, stats
from sklearn.metrics import roc_auc_score, roc_curve
FIG = os.path.join(os.path.dirname(__file__), "figures")

T = os.path.join(os.path.dirname(__file__), "..", "data")
RECS = {"test1": "test1/merged_20260623_17xx.wav", "test2": "test2/merged_1600hz.wav"}

def load(rec):
    w = wave.open(os.path.join(T, RECS[rec]), "rb")
    sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
    x = (np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32)/32768.0).reshape(-1, ch).mean(1)
    w.close()
    lab = pd.read_csv(os.path.join(T, rec, "labels.csv"))
    return x, sr, lab

def clip_feats(rec):
    x, sr, lab = load(rec)
    bb, aa = signal.butter(4, 2000/(sr/2), btype="high")
    hi = signal.filtfilt(bb, aa, x - x.mean())
    floor = np.median(np.abs(hi)) * 3
    FR, HOP = int(0.064*sr), int(0.032*sr)
    feats, y = [], []
    for _, r in lab.iterrows():
        s, e = int(r["t_start_s"]*sr), int(r["t_end_s"]*sr)
        if e-s < sr: continue
        h = hi[s:e]
        fr = np.array([np.sqrt(np.mean(h[i:i+FR]**2)) for i in range(0, len(h)-FR+1, HOP)]) + 1e-9
        feats.append([np.median(fr), np.mean(fr > floor)])  # hi_rms_med, hi_active_frac
        y.append(int(r["fault"]))
    return np.array(feats), np.array(y)

X1, y1 = clip_feats("test1"); X2, y2 = clip_feats("test2")
print(f"test1: {len(y1)} clips, {y1.sum()} fault   test2: {len(y2)} clips, {y2.sum()} fault\n")

# within-recording AUC for the headline indicator (hi_rms_med = col 0)
for name, X, y in [("test1", X1, y1), ("test2", X2, y2)]:
    auc = roc_auc_score(y, X[:, 0])
    print(f"within {name}: hi_rms_med AUC = {auc:.2f}")

def rates_at_eer(y, score):
    fpr, tpr, thr = roc_curve(y, score); fnr = 1-tpr
    j = np.argmin(np.abs(fpr-fnr)); return fpr[j], fnr[j], thr[j]

# CROSS: threshold learned on one recording, applied to the other (absolute transfer)
print("\nCROSS-recording (absolute threshold transfer), hi_rms_med:")
for (na, Xa, ya), (nb, Xb, yb) in [(("test1",X1,y1),("test2",X2,y2)), (("test2",X2,y2),("test1",X1,y1))]:
    _, _, th = rates_at_eer(ya, Xa[:, 0])           # threshold from A
    pred = Xb[:, 0] >= th                             # apply to B
    P, N = yb == 1, yb == 0
    fpr = pred[N].sum()/N.sum(); fnr = (~pred[P]).sum()/P.sum()
    auc = roc_auc_score(yb, Xb[:, 0])
    print(f"  train {na} -> test {nb}: AUC={auc:.2f}  at A's threshold FPR={fpr:.2f} FNR={fnr:.2f}")

# Does per-recording standardisation help transfer? (z-score each recording's feature)
def z(X): return (X - np.median(X, 0)) / (np.percentile(X,75,0)-np.percentile(X,25,0)+1e-9)
Xz = np.vstack([z(X1), z(X2)]); yz = np.concatenate([y1, y2])
auc_pool_abs = roc_auc_score(yz, np.concatenate([X1[:,0], X2[:,0]]))
auc_pool_rel = roc_auc_score(yz, Xz[:, 0])
print(f"\nPooled both recordings, hi_rms_med:  absolute AUC={auc_pool_abs:.2f}   "
      f"per-recording-standardised AUC={auc_pool_rel:.2f}")
fpr, fnr, _ = rates_at_eer(yz, Xz[:, 0])
print(f"  per-recording-standardised pooled equal-error: FPR={fpr:.2f}  FNR={fnr:.2f}  (n={len(yz)}, {yz.sum()} fault)")

# figure: hi_rms_med per clip, both recordings, coloured by fault, with per-recording threshold
fig, ax = plt.subplots(1, 2, figsize=(13, 5), sharey=False)
for k, (name, X, y) in enumerate([("test1", X1, y1), ("test2", X2, y2)]):
    _, _, th = rates_at_eer(y, X[:, 0])
    jit = (np.random.default_rng(k).random(len(y))-0.5)*0.3
    ax[k].scatter(y + jit, X[:, 0], c=["C3" if v else "C0" for v in y], s=45, alpha=0.8)
    ax[k].axhline(th, color="grey", ls="--", lw=1, label="equal-error threshold")
    ax[k].set_xticks([0, 1]); ax[k].set_xticklabels(["normal", "fault"])
    ax[k].set_title(f"{name}: AUC {roc_auc_score(y, X[:,0]):.2f}  ({y.sum()} fault / {len(y)})")
    ax[k].set_ylabel("high-band (>2 kHz) median loudness"); ax[k].legend()
fig.suptitle("Acoustic fault indicator generalises across two independent recordings\n"
             "(ranking transfers; absolute threshold needs per-recording calibration)", fontsize=12)
plt.tight_layout()
out = os.path.join(FIG, "audio-cross-recording.png")
plt.savefig(out, dpi=120); print("wrote", out)
