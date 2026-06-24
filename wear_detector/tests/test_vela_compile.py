# ABOUTME: Tests the Vela->gate normalization (column rename + unit conversion + op-count parse).
# ABOUTME: Pure functions, TF-free — guards the ML->Platform handoff against Vela schema drift.
import pytest

from wear_detector.export import vela_compile as vc


def test_parse_op_counts():
    stdout = "CPU operators = 0 (0.0%)\nNPU operators = 5 (100.0%)\n"
    assert vc.parse_op_counts(stdout) == (0, 5)


def test_parse_op_counts_raises_when_absent():
    with pytest.raises(ValueError):
        vc.parse_op_counts("no operator summary here")


def test_normalize_row_maps_columns_and_units():
    vela_row = {
        "sram_memory_used": "11.421875",
        "off_chip_flash_memory_used": "14.8125",
        "inference_time": "4.4408e-05",  # seconds
        "cycles_npu": "13105",
        "cycles_total": "22204",
    }
    norm = vc.normalize_row(vela_row, cpu_ops=0, npu_ops=5, model_id="pdm-anomaly")
    assert norm["network"] == "pdm-anomaly"
    assert float(norm["total_sram_used"]) == pytest.approx(11.4219, abs=1e-3)
    # seconds -> milliseconds
    assert float(norm["batch_inference_time"]) == pytest.approx(0.0444, abs=1e-3)
    assert norm["cpu_operators"] == "0" and norm["npu_operators"] == "5"


def test_normalized_row_satisfies_gate_schema():
    # The gate's policy column names must all be present in our normalized row.
    import json
    import os
    policy = json.load(open(os.path.join(os.path.dirname(vc.__file__),
                                         "..", "..", "dashboard", "pipeline", "vela.policy.json")))
    norm = vc.normalize_row({"sram_memory_used": "1", "off_chip_flash_memory_used": "1",
                             "inference_time": "1e-3", "cycles_npu": "1", "cycles_total": "1"},
                            cpu_ops=0, npu_ops=1)
    for col in policy["columns"].values():
        assert col in norm, f"gate expects column {col!r} that the normalizer doesn't emit"
