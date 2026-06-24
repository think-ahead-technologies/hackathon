#!/usr/bin/env python3
# ABOUTME: Cross-checks the C scorer (harness_score) against baseline.distances on random
# ABOUTME: int8 embeddings, proving the on-device L2-to-centroid score matches the reference.
import json
import os
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASELINE = os.path.join(ROOT, "wear_detector", "export", "build", "baseline.json")

with open(BASELINE) as fh:
    bl = json.load(fh)
CENTROID = np.array(bl["centroid"], dtype=np.float64)
OUT_SCALE = bl["quant"]["output"]["scale"]
OUT_ZP = bl["quant"]["output"]["zero_point"]


def reference_distance(emb_int8):
    emb = (emb_int8.astype(np.float64) - OUT_ZP) * OUT_SCALE
    return float(np.linalg.norm(emb - CENTROID))


def run_c(harness, emb_int8):
    inp = "\n".join(str(int(v)) for v in emb_int8)
    out = subprocess.run([harness], input=inp, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"harness failed rc={out.returncode}: {out.stderr}")
    return float(out.stdout.strip())


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: score_cross_test.py <harness_score>")
    harness = sys.argv[1]
    rng = np.random.default_rng(1)
    worst = 0.0
    CASES = 200
    for _ in range(CASES):
        emb = rng.integers(-128, 128, size=8).astype(np.int8)
        ref = reference_distance(emb)
        got = run_c(harness, emb)
        worst = max(worst, abs(got - ref))
    print(f"score cross-check: {CASES} embeddings, max|Δdistance|={worst:.6g}")
    if worst > 1e-3:
        raise SystemExit(f"FAIL: distance diff {worst} exceeds 1e-3")
    print("scorer matches baseline.distances — OK")


if __name__ == "__main__":
    main()
