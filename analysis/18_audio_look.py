"""Look at fault vs normal clips at fine time resolution to see what the ear hears.
Hypothesis: faults are brief transients (squeal=tonal, rattle=impulsive) that 5 s
clip-averaging destroyed. Plot fine spectrograms + envelopes for representative clips."""
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

# representative clips: squeal, stronger rattle (faults) vs background (normal)
show = [(14, "FAULT squeal"), (38, "FAULT rattle-stronger"),
        (2, "normal background"), (20, "normal (rattle, not fault)")]
fig, axes = plt.subplots(2, len(show), figsize=(20, 8))
for j, (clip, title) in enumerate(show):
    row = lab[lab["clip"] == clip].iloc[0]
    s, e = int(row["t_start_s"]*sr), int(row["t_end_s"]*sr)
    seg = mono[s:e] - mono[s:e].mean()
    f_s, t_s, S = signal.spectrogram(seg, fs=sr, nperseg=1024, noverlap=768)
    axes[0][j].pcolormesh(t_s, f_s, 10*np.log10(S+1e-12), shading="auto", cmap="magma")
    axes[0][j].set_title(f"clip {clip}: {title}"); axes[0][j].set_ylabel("Hz" if j==0 else "")
    # high-band envelope (transient/impulse view)
    bb, aa = signal.butter(4, 2000/(sr/2), btype="high")
    env = np.abs(signal.hilbert(signal.filtfilt(bb, aa, seg)))
    axes[1][j].plot(np.arange(len(env))/sr, env, lw=0.4)
    axes[1][j].set_ylim(0, np.percentile(np.abs(signal.hilbert(signal.filtfilt(bb,aa,mono-mono.mean()))),99.9))
    axes[1][j].set_xlabel("s"); axes[1][j].set_ylabel(">2kHz envelope" if j==0 else "")
fig.suptitle("Fault vs normal clips, fine resolution — squeal=tonal lines, rattle=sharp impulses", fontsize=13)
plt.tight_layout()
out = os.path.join(FIG, "audio-clip-look.png")
plt.savefig(out, dpi=110); print("wrote", out)
