# ABOUTME: Tonal (squeal/whine) detector — narrowband high-pitched faults the broadband detector misses.
# ABOUTME: A clean machine is broadband; a sharp spectral peak is intrinsically a fault, near baseline-free.
import wave

import numpy as np

from wear_detector.audio import load_wav

# A squeal is a narrow spectral peak well above the local noise floor. peak/median power in the
# high band is the indicator: tens for broadband running, hundreds-to-thousands for a real tone.
# It's near baseline-free (tonality is abnormal regardless of how often it happens), which is
# exactly why it catches persistent squeals that the self-baseline energy detector absorbs.
F_LO_HZ = 1500.0       # "high pitched" — squeals observed ~2 kHz and up
TONAL_FLOOR = 300.0    # peak/median above this is a tone, not broadband running
_NFFT = 2048


def tonal_score(frame, fs, f_lo=F_LO_HZ, n_fft=_NFFT):
    """Peak-to-median power ratio in the band above f_lo — large for a narrowband tone."""
    frame = np.asarray(frame, dtype=np.float64)
    n = min(n_fft, len(frame))
    if n < 16:
        return 0.0
    seg = frame[:n] * np.hanning(n)
    ps = np.abs(np.fft.rfft(seg)) ** 2
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    hb = ps[freqs >= f_lo]
    if hb.size == 0:
        return 0.0
    med = np.median(hb)
    return float(hb.max() / (med + 1e-12))


def peak_freq(frame, fs, f_lo=F_LO_HZ, n_fft=_NFFT):
    """Frequency (Hz) of the dominant bin above f_lo — the squeal pitch."""
    frame = np.asarray(frame, dtype=np.float64)
    n = min(n_fft, len(frame))
    seg = frame[:n] * np.hanning(n)
    ps = np.abs(np.fft.rfft(seg)) ** 2
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    mask = freqs >= f_lo
    return float(freqs[mask][int(np.argmax(ps[mask]))])


def detect_tonal(wav_path, window_s=0.25, hop_s=0.125, f_lo=F_LO_HZ,
                 floor=TONAL_FLOOR, gap_s=1.0):
    """Find squeal/whine events: windows whose high-band peak/median exceeds `floor`.

    Short windows (default 0.25 s) catch brief squeals. Adjacent flagged windows are clustered
    into events; each event reports its peak time, max tonal score, and the squeal pitch (Hz).
    Absolute floor (not a percentile) so a clean recording yields no false squeals.
    """
    x, fs = load_wav(wav_path)
    n = max(16, int(round(window_s * fs)))
    step = max(1, int(round(hop_s * fs)))
    times, scores = [], []
    for i in range(0, len(x) - n + 1, step):
        times.append((i + n / 2) / fs)
        scores.append(tonal_score(x[i:i + n], fs, f_lo))
    times = np.asarray(times)
    scores = np.asarray(scores)

    flagged = times[scores >= floor]
    events = []
    for t in sorted(flagged):
        if events and t - events[-1]["_last"] <= gap_s:
            events[-1]["_last"] = t
            events[-1]["_ts"].append(t)
        else:
            events.append({"_last": t, "_ts": [t]})
    out = []
    for ev in events:
        mask = np.isin(times, ev["_ts"])
        pk = int(np.where(mask)[0][int(np.argmax(scores[mask]))])
        frame_start = int((times[pk] - window_s / 2) * fs)
        frame = x[max(0, frame_start):max(0, frame_start) + n]
        out.append({
            "t": float(times[pk]),
            "tonal_score": float(scores[pk]),
            "pitch_hz": peak_freq(frame, fs, f_lo),
        })
    return {"fs": fs, "events": out, "floor": floor}
