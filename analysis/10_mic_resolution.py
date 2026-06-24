"""The 'why mic it' money figure: the mic resolves the band the IMU is blind to.

Earlier (figure 08) the IMU fault PSD was *still rising at its 25 Hz Nyquist* -> the
real bearing energy lives above it. Here we show, on one log-frequency axis, what
the 16 kHz mic actually captures in that missing band, and contrast loud vs quiet
acoustic windows to expose where the mechanical/bearing energy concentrates."""
import os, wave
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import signal

FIG = os.path.join(os.path.dirname(__file__), "figures")
WAV = os.path.join(os.path.dirname(__file__), "..", "data", "test1",
                   "merged_20260623_17xx.wav")

w = wave.open(WAV, "rb"); sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
x = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
w.close()
mono = x.reshape(-1, ch).mean(axis=1)

# Welch PSD over the whole recording
f, P = signal.welch(mono, fs=sr, nperseg=8192)

# loud vs quiet windows by high-band (>1 kHz) envelope
hop = int(0.05 * sr); nf = len(mono) // hop
bb, aa = signal.butter(4, 1000 / (sr / 2), btype="high")
hi = signal.filtfilt(bb, aa, mono)
env = np.array([np.sqrt(np.mean(hi[i*hop:(i+1)*hop]**2)) for i in range(nf)])
loud_thr = np.percentile(env, 90); quiet_thr = np.percentile(env, 40)
loud_idx = np.where(env >= loud_thr)[0]; quiet_idx = np.where(env <= quiet_thr)[0]
def pool_psd(idx):
    segs = [mono[i*hop:(i+1)*hop] for i in idx]
    xx = np.concatenate(segs)
    ff, PP = signal.welch(xx, fs=sr, nperseg=4096)
    return ff, PP
fl, Pl = pool_psd(loud_idx); fq, Pq = pool_psd(quiet_idx)

# identify tonal peaks (motor/servo lines) in 50-2000 Hz
band = (f >= 50) & (f <= 2000)
fb, Pb = f[band], P[band]
pk, _ = signal.find_peaks(10*np.log10(Pb), prominence=6, distance=5)
tones = fb[pk]
print("Prominent tonal lines (Hz):", np.round(tones[:12], 1))
# fundamental guess = smallest spacing / first strong line
if len(tones) >= 2:
    print(f"lowest tone {tones[0]:.0f} Hz; median spacing {np.median(np.diff(tones)):.0f} Hz")

# energy fractions per band
def frac(lo, hi_):
    m = (f >= lo) & (f < hi_); return P[m].sum() / P.sum()
print(f"\nAcoustic energy by band:")
print(f"  0-25 Hz   (old IMU window) : {frac(0,25)*100:5.2f}%")
print(f"  0-50 Hz   (test1 IMU window): {frac(0,50)*100:5.2f}%")
print(f"  50-1000 Hz (motor/structural): {frac(50,1000)*100:5.2f}%")
print(f"  1-8 kHz   (bearing-ring band): {frac(1000,8000)*100:5.2f}%")
print(f"Mic bandwidth vs IMU: {(sr/2)/25:.0f}x (old 50Hz IMU), {(sr/2)/50:.0f}x (test1 100Hz IMU)")

fig, ax = plt.subplots(1, 2, figsize=(17, 6))
ax[0].semilogx(f, 10*np.log10(P+1e-14), color="k", lw=1)
ax[0].axvspan(0.1, 25, color="C0", alpha=0.18, label="50 Hz IMU sees only this (<25 Hz)")
ax[0].axvspan(25, 50, color="C2", alpha=0.18, label="100 Hz IMU adds this (25-50 Hz)")
for tline in tones[:10]:
    ax[0].axvline(tline, color="C3", ls=":", lw=0.7, alpha=0.7)
ax[0].set_xlabel("frequency (Hz, log)"); ax[0].set_ylabel("PSD (dB)")
ax[0].set_title("test1 mic PSD — IMU sees only the shaded sliver; red = motor tonal lines")
ax[0].set_xlim(1, sr/2); ax[0].legend(loc="upper right", fontsize=9); ax[0].grid(alpha=0.3, which="both")

ax[1].semilogx(fq, 10*np.log10(Pq+1e-14), color="C0", lw=1.2, label="quiet windows")
ax[1].semilogx(fl, 10*np.log10(Pl+1e-14), color="C3", lw=1.2, label="loud (high-band) windows")
ax[1].axvline(25, color="grey", ls="--", lw=1); ax[1].axvline(50, color="grey", ls=":", lw=1)
ax[1].text(26, ax[1].get_ylim()[0]+5, "IMU\nNyquist", fontsize=8)
ax[1].set_xlabel("frequency (Hz, log)"); ax[1].set_ylabel("PSD (dB)")
ax[1].set_title("Loud vs quiet acoustic windows — where mechanical energy rises")
ax[1].set_xlim(1, sr/2); ax[1].legend(loc="upper right"); ax[1].grid(alpha=0.3, which="both")
plt.tight_layout()
out = os.path.join(FIG, "mic-vs-imu-bandwidth.png")
plt.savefig(out, dpi=120); print("\nwrote", out)
