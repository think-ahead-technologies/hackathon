# ABOUTME: CI deployability gate — fails the build if a Vela-compiled model violates the edge contract.
# ABOUTME: Turns "we trained a model" into "we enforce a deployability policy in the pipeline".

import argparse
import csv
import json
import os
import sys


def fallback_ratio(cpu_ops: float, npu_ops: float) -> float:
    """Fraction of operators that fell back to the CPU (off the NPU)."""
    total = cpu_ops + npu_ops
    return cpu_ops / total if total else 0.0


def evaluate(row: dict, policy: dict, artifact_bytes: int | None = None) -> list[tuple]:
    """Return [(check_name, ok, detail)] for each policy check. Pure — no IO."""
    cols = policy["columns"]
    th = policy["thresholds"]
    results = []

    sram = float(row[cols["sram_kib"]])
    results.append(("SRAM working set", sram <= th["max_sram_kib"],
                    f"{sram:.0f} KiB (budget {th['max_sram_kib']} KiB)"))

    inf = float(row[cols["inference_ms"]])
    results.append(("Inference latency", inf <= th["max_inference_ms"],
                    f"{inf:.1f} ms (budget {th['max_inference_ms']} ms)"))

    ratio = fallback_ratio(float(row[cols["cpu_ops"]]), float(row[cols["npu_ops"]]))
    results.append(("CPU-fallback ratio", ratio <= th["max_cpu_fallback_ratio"],
                    f"{ratio * 100:.1f}% (budget {th['max_cpu_fallback_ratio'] * 100:.0f}%)"))

    if artifact_bytes is not None:
        kib = artifact_bytes / 1024
        results.append(("Flatbuffer size", kib <= th["max_flatbuffer_kib"],
                        f"{kib:.0f} KiB (flash slot {th['max_flatbuffer_kib']} KiB)"))
    return results


def passed(results: list[tuple]) -> bool:
    return all(ok for _, ok, _ in results)


def _read_row(path: str) -> dict:
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"no data rows in {path}")
    return rows[0]


def main() -> int:
    ap = argparse.ArgumentParser(description="Deployability gate for a Vela-compiled model.")
    ap.add_argument("--summary", required=True, help="Vela performance summary CSV")
    ap.add_argument("--policy", required=True, help="gate policy JSON")
    ap.add_argument("--artifact", help="compiled .tflite (for the flatbuffer-size check)")
    args = ap.parse_args()

    row = _read_row(args.summary)
    policy = json.load(open(args.policy))
    artifact_bytes = os.path.getsize(args.artifact) if args.artifact and os.path.exists(args.artifact) else None

    results = evaluate(row, policy, artifact_bytes)

    print(f"\nDeployability gate · {row.get('network', '(model)')}")
    print("-" * 52)
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<20} {detail}")
    if artifact_bytes is None:
        print("  ----  Flatbuffer size      skipped (no --artifact; run make package)")
    print("-" * 52)

    if passed(results):
        print("GATE PASSED — model satisfies the edge contract.\n")
        return 0
    fails = [name for name, ok, _ in results if not ok]
    print(f"GATE FAILED — {', '.join(fails)}. Model must not reach a device.\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
