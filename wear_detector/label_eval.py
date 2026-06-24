# ABOUTME: Evaluate detectors against human labels — turn labels.csv into a measurable benchmark.
# ABOUTME: Reports per-feature AUC vs the fault column so we stop guessing and start measuring.
import csv
import sys

import numpy as np

from wear_detector import audio
from wear_detector.imu_band_probe import auc

_BANDS = [0, 250, 500, 1000, 2000, 3000, 4000, 6000, 8000]


def load_labels(csv_path):
    """[(clip, t0, t1, fault, type, notes), ...] from a clip-grid labels.csv (skips blank rows)."""
    out = []
    for r in csv.DictReader(open(csv_path)):
        if not r.get("t_start_s"):
            continue
        out.append((r["clip"], float(r["t_start_s"]), float(r["t_end_s"]),
                    int(r["fault"]), r.get("type", ""), r.get("notes", "")))
    return out


def clip_features(x, fs, t0, t1, nfft=4096):
    """Acoustic feature bank for one clip — the candidates a fault detector might use."""
    s = x[int(t0 * fs):int(t1 * fs)]
    s = s - s.mean()
    rms = float(np.sqrt(np.mean(s * s)))
    peak = float(np.abs(s).max())
    z = (s - s.mean()) / (s.std() + 1e-12)
    P = np.zeros(nfft // 2 + 1)
    nf = 0
    for i in range(0, len(s) - nfft, nfft // 2):
        P += np.abs(np.fft.rfft(s[i:i + nfft] * np.hanning(nfft))) ** 2
        nf += 1
    P /= max(nf, 1)
    fr = np.fft.rfftfreq(nfft, 1.0 / fs)
    f = {
        "rms": rms,
        "crest": peak / (rms + 1e-9),
        "kurtosis": float(np.mean(z ** 4)),
        "tonal": float(P[fr >= 1500].max() / (np.median(P[fr >= 1500]) + 1e-12)),
        "hf_ratio": float(P[fr >= 2000].sum() / (P.sum() + 1e-12)),
        "centroid": float(np.sum(fr * P) / (P.sum() + 1e-12)),
    }
    for k in range(len(_BANDS) - 1):
        f[f"band_{_BANDS[k]}_{_BANDS[k+1]}"] = float(P[(fr >= _BANDS[k]) & (fr < _BANDS[k + 1])].sum())
    return f


def evaluate(csv_path, wav_path):
    """Per-feature AUC against the fault label, plus the misranked clips at the best feature."""
    labels = load_labels(csv_path)
    x, fs = audio.load_wav(wav_path)
    feats = [clip_features(x, fs, t0, t1) for _c, t0, t1, _f, _t, _n in labels]
    y = np.array([row[3] for row in labels])
    names = list(feats[0].keys())
    aucs = {}
    for k in names:
        v = np.array([fd[k] for fd in feats])
        aucs[k] = auc(v[y == 1], v[y == 0])
    return {"labels": labels, "feats": feats, "y": y, "aucs": aucs,
            "n_fault": int(y.sum()), "n_ok": int((y == 0).sum())}


def main(csv_path, wav_path):
    r = evaluate(csv_path, wav_path)
    print(f"labels: {len(r['labels'])} clips  ({r['n_fault']} fault, {r['n_ok']} non-fault)\n")
    ranked = sorted(r["aucs"].items(), key=lambda kv: -abs(kv[1] - 0.5))
    print(f"{'feature':>16} {'AUC':>6}   (0.5 = no separation)")
    for k, a in ranked:
        print(f"{k:>16} {a:>6.2f}")
    best = ranked[0][0]
    print(f"\nbest separator: {best} (AUC {r['aucs'][best]:.2f}) — "
          f"{'usable' if abs(r['aucs'][best]-0.5) >= 0.35 else 'WEAK; not a reliable detector'}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
