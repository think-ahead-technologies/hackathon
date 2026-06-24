"""Continuous, time-resolved acoustic fault detector + spectral characterisation.

(1) Run the winning indicator -- high-band (>2 kHz) loudness -- continuously across
    the whole recording at fine resolution, overlay the human fault labels, threshold,
    and report fine-window false-positive / false-negative rates.
(2) Characterise WHERE the fault energy lives (fault-clip vs normal-clip average
    spectrum), to inform the on-device log-mel feature design.

Acronyms: RMS = root-mean-square (loudness); FPR/FNR = false-positive / false-negative
rate; AUC = area under the ROC curve.
"""
import os, wave
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from scipy import signal

FIG = os.path.join(os.path.dirname(__file__), "figures")
T2 = os.path.join(os.path.dirname(__file__), "..", "data", "test2")
w = wave.open(os.path.join(T2, "merged_1600hz.wav"), "rb")
sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
mono = (np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32)/32768.0).reshape(-1, ch).mean(1)
w.close()
lab = pd.read_csv(os.path.join(T2, "labels.csv"))
fault_clips = lab[lab["fault"] == 1]

# ---- (1) continuous high-band loudness ----
bb, aa = signal.butter(4, 2000/(sr/2), btype="high")
hi = signal.filtfilt(bb, aa, mono - mono.mean())
HOP = int(0.25*sr); WIN = int(0.5*sr)
t_idx = np.arange(0, len(hi)-WIN, HOP)
loud_raw = np.array([np.sqrt(np.mean(hi[i:i+WIN]**2)) for i in t_idx])
tt = (t_idx + WIN//2)/sr
# faults are sustained -> rolling median over ~2 s suppresses isolated transient noise
import pandas as _pd
K = 8  # 8 x 0.25 s hop = 2 s
loud = _pd.Series(loud_raw).rolling(K, center=True, min_periods=1).median().values

# label each fine window by the clip it falls in (only clips that were annotated)
yfine = np.full(len(tt), -1)
for _, r in lab.iterrows():
    yfine[(tt >= r["t_start_s"]) & (tt < r["t_end_s"])] = int(r["fault"])
have = yfine >= 0

# threshold from the labels: maximise (TPR - FPR) over labelled fine windows
from sklearn.metrics import roc_curve, roc_auc_score
fpr, tpr, thr = roc_curve(yfine[have], loud[have])
auc = roc_auc_score(yfine[have], loud[have])
jbest = np.argmax(tpr - fpr); TH = thr[jbest]
# rates at that threshold
pred = loud[have] >= TH
P = yfine[have] == 1; N = yfine[have] == 0
FPR = np.sum(pred[N]) / N.sum(); FNR = np.sum(~pred[P]) / P.sum()
print(f"Continuous acoustic detector (0.5 s windows, 2 s rolling-median smoothed):")
print(f"  AUC = {auc:.2f}  | at chosen threshold: FPR = {FPR:.2f}, FNR = {FNR:.2f}")
print(f"  threshold (high-band RMS) = {TH:.4f}")

# detected events (>=2 consecutive windows above threshold) vs labelled faults
above = loud >= TH
events = []
i = 0
while i < len(above):
    if above[i]:
        j = i
        while j < len(above) and above[j]:
            j += 1
        if j - i >= 2:
            events.append((tt[i], tt[j-1]))
        i = j
    else:
        i += 1
def overlaps_fault(s, e):
    return any(not (e < r["t_start_s"] or s > r["t_end_s"]) for _, r in fault_clips.iterrows())
matched = [ev for ev in events if overlaps_fault(*ev)]
unlabelled = [ev for ev in events if not overlaps_fault(*ev)]
print(f"  detected events: {len(events)}  (overlap a labelled fault: {len(matched)}; "
      f"other: {len(unlabelled)} -> candidate UNLABELLED faults or non-bearing noise)")

# ---- (2) fault vs normal average spectrum ----
def avg_spec(clips):
    acc = None; cnt = 0
    for _, r in clips.iterrows():
        s, e = int(r["t_start_s"]*sr), int(r["t_end_s"]*sr)
        f, P = signal.welch(mono[s:e]-mono[s:e].mean(), fs=sr, nperseg=2048)
        acc = P if acc is None else acc + P; cnt += 1
    return f, acc/max(cnt, 1)
f, Pf = avg_spec(lab[lab["fault"] == 1])
_, Pn = avg_spec(lab[(lab["fault"] == 0) & (lab["type"] == "-")])  # quiet background only

# ---- figure ----
fig, ax = plt.subplots(2, 1, figsize=(15, 9))
ax[0].plot(tt, loud, lw=0.6, color="C0")
ax[0].axhline(TH, color="k", ls="--", lw=1, label=f"threshold (FPR {FPR:.2f}, FNR {FNR:.2f})")
for _, r in fault_clips.iterrows():
    ax[0].axvspan(r["t_start_s"], r["t_end_s"], color="red", alpha=0.25)
for s, e in unlabelled:
    ax[0].axvspan(s, e, color="orange", alpha=0.20)
ax[0].set_xlabel("time (s)"); ax[0].set_ylabel("high-band (>2 kHz) loudness")
ax[0].set_title("Continuous acoustic fault detector — red = labelled fault, orange = other detections")
ax[0].legend(loc="upper right")

ax[1].semilogy(f, Pn, color="C0", lw=1.5, label="normal (quiet background)")
ax[1].semilogy(f, Pf, color="C3", lw=1.5, label="fault clips")
ax[1].axvspan(2000, 8000, color="C3", alpha=0.07, label=">2 kHz fault band")
ax[1].set_xlabel("frequency (Hz)"); ax[1].set_ylabel("power spectral density")
ax[1].set_title("Where the fault energy lives (fault vs normal average spectrum)")
ax[1].legend(); ax[1].set_xlim(0, sr/2)
plt.tight_layout()
out = os.path.join(FIG, "audio-continuous.png")
plt.savefig(out, dpi=120); print("wrote", out)

# band-resolved fault/normal ratio (which bands separate best -> on-device feature design)
print("\nfault/normal power ratio by band:")
for lo, hib in [(0,500),(500,1000),(1000,2000),(2000,4000),(4000,8000)]:
    m = (f >= lo) & (f < hib)
    print(f"  {lo:>4}-{hib:<4} Hz : {Pf[m].mean()/ (Pn[m].mean()+1e-12):5.1f}x")
