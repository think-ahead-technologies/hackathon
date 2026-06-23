# ABOUTME: Unit tests for the deployability gate's policy logic.
# ABOUTME: Guards that a compliant model passes and a bad one is rejected for the right reasons.

import csv

from gate import evaluate, fallback_ratio, passed

POLICY = {
    "columns": {"sram_kib": "total_sram_used", "cpu_ops": "cpu_operators",
                "npu_ops": "npu_operators", "inference_ms": "batch_inference_time"},
    "thresholds": {"max_sram_kib": 3500, "max_inference_ms": 50,
                   "max_cpu_fallback_ratio": 0.10, "max_flatbuffer_kib": 1024},
}


def _row(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))[0]


def test_fallback_ratio():
    assert fallback_ratio(1, 41) == 1 / 42
    assert fallback_ratio(0, 0) == 0.0   # no divide-by-zero


def test_good_model_passes():
    results = evaluate(_row("samples/vela-summary.csv"), POLICY, artifact_bytes=312 * 1024)
    assert passed(results) is True


def test_bad_model_fails_on_the_right_checks():
    results = evaluate(_row("samples/vela-summary-bad.csv"), POLICY)
    assert passed(results) is False
    failed = {name for name, ok, _ in results if not ok}
    assert "SRAM working set" in failed
    assert "Inference latency" in failed
    assert "CPU-fallback ratio" in failed


def test_oversized_flatbuffer_fails():
    results = evaluate(_row("samples/vela-summary.csv"), POLICY, artifact_bytes=2000 * 1024)
    failed = {name for name, ok, _ in results if not ok}
    assert "Flatbuffer size" in failed


def test_size_check_skipped_without_artifact():
    results = evaluate(_row("samples/vela-summary.csv"), POLICY, artifact_bytes=None)
    names = {name for name, _, _ in results}
    assert "Flatbuffer size" not in names   # skipped, not failed
