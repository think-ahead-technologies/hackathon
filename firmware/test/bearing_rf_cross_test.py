#!/usr/bin/env python3
"""Cross-check exported bearing_rf.c scores against sklearn on real windows."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from analysis import export_bearing_rf_c as exporter  # noqa: E402

TOL = 1e-5


def selected_indices(x: np.ndarray, y: np.ndarray, clf, threshold: float) -> np.ndarray:
    score = clf.predict_proba(x.astype(np.float32))[:, 1]
    positives = np.flatnonzero(y == 1)
    negatives = np.flatnonzero(y == 0)
    near_threshold = np.argsort(np.abs(score - threshold))[:64]
    high = np.argsort(score)[-64:]
    low = np.argsort(score)[:64]
    pos_pick = positives[np.linspace(0, len(positives) - 1, min(64, len(positives))).astype(int)]
    neg_pick = negatives[np.linspace(0, len(negatives) - 1, min(64, len(negatives))).astype(int)]
    return np.unique(np.r_[pos_pick, neg_pick, near_threshold, high, low])


def run_c(harness: Path, x: np.ndarray):
    payload = [str(len(x))]
    payload.extend(" ".join(f"{float(v):.9g}" for v in row) for row in x)
    proc = subprocess.run(
        [str(harness)],
        input="\n".join(payload) + "\n",
        text=True,
        capture_output=True,
        check=True,
    )
    rows = []
    for line in proc.stdout.splitlines():
        status, score, percent = line.split()
        rows.append((int(status), float(score), float(percent)))
    return rows


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: bearing_rf_cross_test.py <harness>", file=sys.stderr)
        return 2
    harness = Path(sys.argv[1]).resolve()
    x, y, groups, _sessions = exporter.build_dataset()
    threshold, _fpr, _fnr = exporter.pick_high_recall_threshold(x, y, groups)
    clf = exporter.train_final_model(x, y)
    idx = selected_indices(x, y, clf, threshold)
    x_cases = x[idx].astype(np.float32)
    py_score = clf.predict_proba(x_cases)[:, 1]
    c_rows = run_c(harness, x_cases)
    if len(c_rows) != len(x_cases):
        print(f"C harness returned {len(c_rows)} rows for {len(x_cases)} cases", file=sys.stderr)
        return 3

    mismatches = []
    max_abs = 0.0
    for case_index, (py, (status, c_score, _percent)) in enumerate(zip(py_score, c_rows)):
        expected_status = 1 if py >= threshold else 0
        diff = abs(float(py) - c_score)
        max_abs = max(max_abs, diff)
        if diff > TOL or status != expected_status:
            mismatches.append((case_index, float(py), c_score, status, expected_status, diff))

    print(
        f"bearing-rf cross-check: cases={len(x_cases)} threshold={threshold:.9g} "
        f"max_abs_score_diff={max_abs:.3g} mismatches={len(mismatches)}"
    )
    for row in mismatches[:10]:
        case_index, py, c_score, status, expected_status, diff = row
        print(
            f"mismatch[{case_index}] py={py:.9g} c={c_score:.9g} "
            f"status={status} expected={expected_status} diff={diff:.3g}",
            file=sys.stderr,
        )
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())