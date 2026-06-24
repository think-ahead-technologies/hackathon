# ABOUTME: End-to-end eval of the deployed (int8) encoder + per-unit centroid on real data.
# ABOUTME: Reports per-window + dwell AUC and the severity ladder; writes baseline.json for the manifest.
import json
import os

import numpy as np

from wear_detector.export import baseline, dataset, spectro
from wear_detector.export.train import BUILD
from wear_detector.evaluate import auc

TFLITE = os.path.join(BUILD, "model_int8.tflite")
BASELINE_PATH = os.path.join(BUILD, "baseline.json")


def rolling_mean(x, w):
    if len(x) < w:
        return np.array([np.mean(x)]) if len(x) else np.array([])
    c = np.cumsum(np.insert(x, 0, 0.0))
    return (c[w:] - c[:-w]) / w


def _session_streams(sessions, keep):
    streams = []
    for s in sessions:
        specs = [spec for spec, _ in dataset.session_spectrograms(s, keep)]
        if specs:
            streams.append(np.stack(specs).astype(np.float32))
    return streams


def main(seed=0, dwell_w=3):
    d = dataset.build(seed=seed)
    centroid = baseline.embed_int8(TFLITE, d["X_train"]).mean(axis=0)
    score = lambda X: baseline.distances(baseline.embed_int8(TFLITE, X), centroid)
    h_te, f = score(d["X_healthy_test"]), score(d["X_fault"])

    print(f"int8 encoder + per-unit centroid (K={centroid.shape[0]}) on {d['fs']:.0f} Hz data")
    print(f"  healthy test {len(h_te)} | fault {len(f)}")
    print(f"\n  per-window AUC      : {auc(f, h_te):.3f}")
    print("  (per-window threshold is operating-point-limited at 50 Hz — the device"
          " stages on the dwell-smoothed score, spec §5)")

    # Dwell: wear persists, healthy spikes are transient (spec §5). Calibrate the
    # operating threshold on the smoothed healthy stream -> the shipped operating point.
    h_str = _session_streams(dataset.HEALTHY, dataset.HEALTHY_KEEP)
    f_str = _session_streams(dataset.FAULT, dataset.FAULT_KEEP)
    h_roll = np.concatenate([rolling_mean(score(s), dwell_w) for s in h_str])
    f_roll = np.concatenate([rolling_mean(score(s), dwell_w) for s in f_str])
    thr = float(np.quantile(h_roll, 0.95))  # ~5% healthy false-alarm on the dwell signal
    print(f"\n  dwell AUC (w={dwell_w})      : {auc(f_roll, h_roll):.3f}")
    print(f"  dwell threshold@5%  : {thr:.3f}")
    print(f"  dwell TPR (fault)   : {(f_roll >= thr).mean():.3f}")
    print(f"  dwell FPR (healthy) : {(h_roll >= thr).mean():.3f}")

    print("\n  severity ladder (median distance):")
    print(f"    {'0 healthy':14} {np.median(h_te):.3f}")
    for name, X in dataset.ladder_spectrograms().items():
        if len(X):
            print(f"    {name:14} {np.median(score(X)):.3f}  (n={len(X)})")

    cfg = spectro.feature_config(d["fs"])
    quant = json.load(open(os.path.join(BUILD, "quant.json")))
    with open(BASELINE_PATH, "w") as fh:
        json.dump({"centroid": centroid.tolist(), "threshold": thr, "dwell_w": dwell_w,
                   "embed_dim": int(centroid.shape[0]), "fpr_target": 0.05,
                   "feature_config": cfg, "quant": quant}, fh, indent=2)
    print(f"\n  wrote {BASELINE_PATH}")


if __name__ == "__main__":
    main()
