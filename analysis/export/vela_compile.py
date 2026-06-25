# ABOUTME: Runs Vela on the int8 model and normalizes its output to the pipeline gate's schema.
# ABOUTME: Writes into this export's own build/ — promoting to the fleet pipeline is a deliberate step.
import csv
import os
import re
import subprocess
import sys

BUILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build")
VELA = os.path.join(os.path.dirname(sys.executable), "vela")
ACCEL = "ethos-u55-128"
MODEL_ID = "conveyor-fault"


def parse_op_counts(stdout):
    """Pull 'CPU operators = N' / 'NPU operators = M' out of Vela's stdout summary."""
    cpu = re.search(r"CPU operators\s*=\s*(\d+)", stdout)
    npu = re.search(r"NPU operators\s*=\s*(\d+)", stdout)
    if not cpu or not npu:
        raise ValueError("could not find CPU/NPU operator counts in Vela output")
    return int(cpu.group(1)), int(npu.group(1))


def normalize_row(vela_row, cpu_ops, npu_ops, model_id=MODEL_ID):
    """Map a real Vela 5.1 summary row to the gate's expected columns/units.

    Vela reports SRAM/flash in KiB and inference_time in *seconds*; the gate's policy
    columns are total_sram_used (KiB) and batch_inference_time (ms).
    """
    return {
        "network": model_id,
        "total_sram_used": f"{float(vela_row['sram_memory_used']):.3f}",
        "total_off_chip_flash_used": f"{float(vela_row['off_chip_flash_memory_used']):.3f}",
        "cpu_operators": str(cpu_ops),
        "npu_operators": str(npu_ops),
        "npu_cycles": vela_row["cycles_npu"],
        "total_cycles": vela_row["cycles_total"],
        "batch_inference_time": f"{float(vela_row['inference_time']) * 1000.0:.4f}",
    }


def _read_vela_summary():
    for f in os.listdir(BUILD):
        if f.startswith("model_int8_summary") and f.endswith(".csv"):
            with open(os.path.join(BUILD, f), newline="") as fh:
                return next(csv.DictReader(fh))
    raise FileNotFoundError("no Vela summary CSV in build/ — run vela first")


def main():
    tflite = os.path.join(BUILD, "model_int8.tflite")
    proc = subprocess.run(
        [VELA, "--accelerator-config", ACCEL, "--optimise", "Performance",
         "--output-dir", BUILD, tflite],
        capture_output=True, text=True, check=True)
    cpu_ops, npu_ops = parse_op_counts(proc.stdout)
    norm = normalize_row(_read_vela_summary(), cpu_ops, npu_ops)

    summary_out = os.path.join(BUILD, "vela-summary.csv")
    with open(summary_out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(norm.keys()))
        w.writeheader()
        w.writerow(norm)

    print(f"CPU ops {cpu_ops} / NPU ops {npu_ops}  ->  fallback "
          f"{cpu_ops / (cpu_ops + npu_ops) * 100:.1f}%")
    print(f"normalized summary -> {summary_out}")
    print(f"vela artifact      -> {os.path.join(BUILD, 'model_int8_vela.tflite')}")


if __name__ == "__main__":
    main()
