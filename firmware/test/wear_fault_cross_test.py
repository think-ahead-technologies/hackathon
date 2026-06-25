#!/usr/bin/env python3
"""Cross-check wear_fault.c against Python feature extraction on real sessions."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from statistics import median

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data" / "provided" / "thingkathon_kickstart" / "thinkathon_kickstart" / "data"
sys.path.insert(0, str(REPO))

from wear_detector import features  # noqa: E402
from wear_detector.io_imu import infer_fs, load_imu, load_labels, window_label  # noqa: E402

CRITERIA_FEATURES = [
    "a_env_rms",
    "a_env_p2p",
    "a_jerk_mad",
    "a_rms",
    "a_std",
    "a_p2p",
    "a_rms_z",
]

NORMAL_SESSION = "Session-2026-06-17--11-25-33_all_normal"
FAULT_SESSION = "Session-2026-06-17--13-49-38_faulty_bearing"
WINDOW_S = 1.0
OVERLAP = 0.5


def iter_windows(session_name: str):
    session = DATA / session_name
    t, accel, gyro = load_imu(str(session))
    labels = load_labels(str(session))
    fs = infer_fs(t)
    n = max(2, int(round(WINDOW_S * fs)))
    step = max(1, int(round(n * (1.0 - OVERLAP))))
    for start in range(0, len(t) - n + 1, step):
        end = start + n
        label = window_label(float(t[start]), float(t[end - 1]), labels) if labels else None
        yield label, accel[start:end], gyro[start:end], fs


def python_is_fault(feat: dict[str, float], baseline: dict[str, float]) -> bool:
    ratios = {name: feat[name] / (baseline[name] + 1e-12) for name in CRITERIA_FEATURES}
    energy_ratio = max(ratios["a_rms"], ratios["a_std"], ratios["a_p2p"], ratios["a_rms_z"])
    return (
        ratios["a_env_rms"] >= 2.8
        and ratios["a_env_p2p"] >= 2.8
        and ratios["a_jerk_mad"] >= 2.4
        and energy_ratio >= 2.1
    )


def build_baseline() -> dict[str, float]:
    rows = []
    for index, (_label, accel, gyro, fs) in enumerate(iter_windows(NORMAL_SESSION)):
        rows.append(features.extract(accel, gyro, fs))
        if index >= 39:
            break
    if not rows:
        raise RuntimeError("normal reference session produced no baseline windows")
    return {name: float(median(row[name] for row in rows)) for name in CRITERIA_FEATURES}


def selected_cases(baseline: dict[str, float]):
    cases = []
    normal_count = 0
    for label, accel, gyro, fs in iter_windows(NORMAL_SESSION):
        feat = features.extract(accel, gyro, fs)
        expected = python_is_fault(feat, baseline)
        cases.append(("normal", label, expected, accel, fs))
        normal_count += 1
        if normal_count >= 24:
            break

    fault_count = 0
    background_count = 0
    for label, accel, gyro, fs in iter_windows(FAULT_SESSION):
        feat = features.extract(accel, gyro, fs)
        expected = python_is_fault(feat, baseline)
        if label == "fault" and fault_count < 24:
            cases.append(("fault-label", label, expected, accel, fs))
            fault_count += 1
        elif label is None and background_count < 24:
            cases.append(("fault-session-bg", label, expected, accel, fs))
            background_count += 1
        if fault_count >= 24 and background_count >= 24:
            break

    if not any(expected for *_rest, expected, _accel, _fs in cases):
        raise RuntimeError("selected real windows produced no Python FAULT cases")
    return cases


def run_c(harness: Path, baseline: dict[str, float], cases) -> list[tuple[int, float, float]]:
    payload = []
    payload.append(" ".join(f"{baseline[name]:.17g}" for name in CRITERIA_FEATURES))
    payload.append(str(len(cases)))
    for _kind, _label, _expected, accel, fs in cases:
        payload.append(f"{len(accel)} {fs:.17g}")
        payload.extend(f"{row[0]:.17g} {row[1]:.17g} {row[2]:.17g}" for row in accel)
    proc = subprocess.run(
        [str(harness)],
        input="\n".join(payload) + "\n",
        text=True,
        capture_output=True,
        check=True,
    )
    out = []
    for line in proc.stdout.splitlines():
        status, percent, score = line.split()
        out.append((int(status), float(percent), float(score)))
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: wear_fault_cross_test.py <harness>", file=sys.stderr)
        return 2
    harness = Path(sys.argv[1]).resolve()
    baseline = build_baseline()
    cases = selected_cases(baseline)
    c_rows = run_c(harness, baseline, cases)
    if len(c_rows) != len(cases):
        print(f"C harness returned {len(c_rows)} rows for {len(cases)} cases", file=sys.stderr)
        return 3

    mismatches = []
    expected_faults = 0
    observed_faults = 0
    for idx, ((kind, label, expected, _accel, _fs), (status, percent, score)) in enumerate(zip(cases, c_rows)):
        actual = status == 1
        expected_faults += int(expected)
        observed_faults += int(actual)
        if actual != expected:
            mismatches.append((idx, kind, label, expected, status, percent, score))

    print(
        f"real-window cross-check: cases={len(cases)} "
        f"python_faults={expected_faults} c_faults={observed_faults} mismatches={len(mismatches)}"
    )
    for row in mismatches[:10]:
        idx, kind, label, expected, status, percent, score = row
        print(
            f"mismatch[{idx}] kind={kind} label={label} expected={expected} "
            f"c_status={status} percent={percent:.3f} score={score:.3f}",
            file=sys.stderr,
        )
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())