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


def ranks(v):
    """Average ranks of v (1..n), ties averaged — for scale-free rank fusion."""
    v = np.asarray(v, dtype=np.float64)
    order = np.argsort(v, kind="mergesort")
    r = np.empty(len(v))
    r[order] = np.arange(1, len(v) + 1)
    # average ties
    for val in np.unique(v):
        mask = v == val
        if mask.sum() > 1:
            r[mask] = r[mask].mean()
    return r


def imu_clip_features(t_wall, acc, gyro, t_dev, fs, t0, t1):
    """IMU feature bank for one clip (None if too few samples in the window)."""
    from wear_detector import burst_features as bf
    m = (t_wall >= t0) & (t_wall < t1)
    if int(m.sum()) < 32:
        return None
    a, g, td = acc[m], gyro[m], t_dev[m]
    sig = np.sqrt((a * a).sum(axis=1))
    sig = sig - sig.mean()
    rms = float(np.sqrt(np.mean(sig * sig)))
    z = (sig - sig.mean()) / (sig.std() + 1e-12)
    sp = bf.burst_spectral_features(a, td, fs)
    return {
        "imu_rms": rms,
        "imu_crest": float(np.abs(sig).max() / (rms + 1e-9)),
        "imu_kurtosis": float(np.mean(z ** 4)),
        "imu_gyro_rms": float(np.sqrt((g * g).sum(axis=1).mean())),
        "imu_hi400": float(sum(sp[f"band{b}"] for b in range(4, 8))),
        "imu_centroid": float(sp["centroid"]),
    }


def evaluate_multimodal(csv_path, wav_path, imu_csv_path):
    """AUC of audio features, IMU features, and audio+IMU rank fusion against the fault label.

    Audio and IMU clips are aligned by recording-start time (same origin). Returns each
    modality's per-feature AUC, the best single feature per modality, and the fused AUC
    (rank-sum of the best audio + best IMU feature) — to test whether fusion beats either.
    """
    from wear_detector.io_imu import load_merged_csv_bursts
    labels = load_labels(csv_path)
    x, fs_a = audio.load_wav(wav_path)
    t_wall, acc, gyro, fs_i, t_dev = load_merged_csv_bursts(imu_csv_path)
    y = np.array([row[3] for row in labels])

    a_feats = [clip_features(x, fs_a, t0, t1) for _c, t0, t1, _f, _t, _n in labels]
    i_feats = [imu_clip_features(t_wall, acc, gyro, t_dev, fs_i, t0, t1)
               for _c, t0, t1, _f, _t, _n in labels]
    covered = [f is not None for f in i_feats]

    a_auc = {k: auc(np.array([f[k] for f in a_feats])[y == 1],
                    np.array([f[k] for f in a_feats])[y == 0]) for k in a_feats[0].keys()}
    i_keys = next(f for f in i_feats if f).keys()
    cov = np.array(covered)
    i_auc = {k: auc(np.array([i_feats[j][k] for j in range(len(labels)) if cov[j]])[y[cov] == 1],
                    np.array([i_feats[j][k] for j in range(len(labels)) if cov[j]])[y[cov] == 0])
             for k in i_keys}

    best_a = max(a_auc, key=lambda k: abs(a_auc[k] - 0.5))
    best_i = max(i_auc, key=lambda k: abs(i_auc[k] - 0.5))
    av = np.array([f[best_a] for f in a_feats])
    iv = np.array([(f or {}).get(best_i, np.nan) for f in i_feats])
    use = cov  # only clips with IMU coverage for the fused score
    fused = ranks(av[use]) + ranks(iv[use])
    fused_auc = auc(fused[y[use] == 1], fused[y[use] == 0])
    return {"audio_auc": a_auc, "imu_auc": i_auc, "best_audio": best_a, "best_imu": best_i,
            "fused_auc": fused_auc, "n_covered": int(cov.sum()), "n_total": len(labels)}


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


def main_multimodal(csv_path, wav_path, imu_csv_path):
    r = evaluate_multimodal(csv_path, wav_path, imu_csv_path)
    print(f"labels: {r['n_total']} clips, {r['n_covered']} with IMU coverage\n")
    print("audio features:")
    for k, a in sorted(r["audio_auc"].items(), key=lambda kv: -abs(kv[1] - 0.5))[:5]:
        print(f"  {k:>16} AUC={a:.2f}")
    print("IMU features:")
    for k, a in sorted(r["imu_auc"].items(), key=lambda kv: -abs(kv[1] - 0.5))[:5]:
        print(f"  {k:>16} AUC={a:.2f}")
    print(f"\nbest audio={r['best_audio']} ({r['audio_auc'][r['best_audio']]:.2f}), "
          f"best IMU={r['best_imu']} ({r['imu_auc'][r['best_imu']]:.2f})")
    print(f"FUSED (rank-sum) AUC = {r['fused_auc']:.2f}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) >= 3:        # labels.csv  audio.wav  imu.csv  -> multimodal
        main_multimodal(args[0], args[1], args[2])
    else:                     # labels.csv  audio.wav        -> audio-only
        main(*args[:2])
