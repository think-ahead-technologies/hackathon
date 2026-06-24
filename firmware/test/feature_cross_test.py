#!/usr/bin/env python3
# ABOUTME: Cross-checks the C feature extractor (harness_features) against spectro.py + the
# ABOUTME: model's int8 input quantization, proving the on-device front-end matches the trained one.
import os
import subprocess
import sys

import numpy as np

# Import the authoritative reference front-end.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from wear_detector.export import spectro  # noqa: E402

FS = 50.0
N = spectro.feature_config(FS)            # window geometry
WINDOW = max(int(round(spectro.WINDOW_S * FS)), 200)
IN_SCALE = 0.025814762338995934           # model input quant (quant.json)
IN_ZP = -128


def reference_int8(accel):
    """spectro.accel_to_spectrogram -> baseline.embed_int8 quantization (round-half-to-even)."""
    logmel = spectro.accel_to_spectrogram(accel, FS).astype(np.float32)  # [49,40] float32
    q = np.round(logmel / IN_SCALE + IN_ZP).clip(-128, 127).astype(np.int8)
    return q.reshape(-1)


def run_c(harness, accel):
    n = accel.shape[0]
    buf = [str(n)] + [repr(float(v)) for v in accel.reshape(-1)]
    out = subprocess.run([harness], input="\n".join(buf), capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"harness failed rc={out.returncode}: {out.stderr}")
    return np.array([int(x) for x in out.stdout.split()], dtype=np.int8)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: feature_cross_test.py <harness_features>")
    harness = sys.argv[1]
    rng = np.random.default_rng(0)
    max_abs_diff = 0
    total_off = 0
    total = 0
    CASES = 25
    for c in range(CASES):
        # Realistic-ish vibration: small AC accel on top of a ~1g DC offset (gravity).
        accel = rng.normal(0.0, 1.0, size=(WINDOW, 3)).astype(np.float64)
        accel[:, 2] += 9.81  # gravity on Z; dynamic-magnitude removes the per-window mean
        accel += 0.3 * np.sin(2 * np.pi * 7.0 * np.arange(WINDOW)[:, None] / FS)  # a tone
        ref = reference_int8(accel)
        got = run_c(harness, accel)
        if got.shape != ref.shape:
            raise SystemExit(f"shape mismatch: C {got.shape} vs ref {ref.shape}")
        diff = np.abs(got.astype(int) - ref.astype(int))
        max_abs_diff = max(max_abs_diff, int(diff.max()))
        total_off += int((diff > 0).sum())
        total += diff.size
    print(f"cross-check: {CASES} windows, {total} values, "
          f"max|Δ|={max_abs_diff} LSB, mismatched={total_off} "
          f"({100.0*total_off/total:.3f}%)")
    # Accept exact, tolerate <=1-LSB rounding on a tiny fraction (float32 ref vs float64 C).
    if max_abs_diff > 1:
        raise SystemExit(f"FAIL: max abs diff {max_abs_diff} LSB > 1")
    if total_off > 0.01 * total:
        raise SystemExit(f"FAIL: {100.0*total_off/total:.2f}% values differ (>1%)")
    print("feature extractor matches spectro.py (<=1 LSB) — OK")


if __name__ == "__main__":
    main()
