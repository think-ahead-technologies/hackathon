# ABOUTME: Emits Contract A (model-meta.json) for the conveyor-fault classifier from the int8 artifacts.
# ABOUTME: Writes into this export's build/; promoting to dashboard/pipeline is a deliberate fleet step.
import json
import os

from train import BUILD

META_PATH = os.path.join(BUILD, "model-meta.json")

# Vela reports the real SRAM working set; 128 KiB gives TFLM interpreter + tensor-metadata
# headroom. Confirm the real arena on-target (firmware tensor_arena).
ARENA_BYTES = 131072


def build_meta():
    quant = json.load(open(os.path.join(BUILD, "quant.json")))
    ev = json.load(open(os.path.join(BUILD, "eval.json")))
    cfg = json.load(open(os.path.join(BUILD, "feature-config.json")))
    cfg["fs"] = round(cfg["fs"], 3)

    def io(side):
        q = quant[side]
        return {"shape": q["shape"], "dtype": q["dtype"],
                "scale": q["scale"], "zero_point": q["zero_point"]}

    return {
        "_comment": "Contract A for the on-device conveyor-fault model. Supervised classifier: "
                    "the int8 model maps a [1,49,40,2] accel+gyro spectrogram to [1,2] logits "
                    "[healthy, fault]; the device scores the fault margin (fault_logit - "
                    "healthy_logit), dwell-smooths it, and alerts over threshold. Threshold is "
                    "the commissioning default, recalibrated per unit by the feedback loop. "
                    "package.py merges version/sha256/size into the signed manifest.",
        "model_id": "conveyor-fault",
        "target": "pse84/ethos-u55-128",
        "input": io("input"),
        "output": io("output"),
        "arena_bytes": ARENA_BYTES,
        "feature_config": cfg,
        "classifier": {
            "classes": ["healthy", "fault"],
            "score": "fault_margin",  # output[1] - output[0], dequantized
            "threshold": ev["int8_operating_point"]["threshold"],
            "fpr_target": ev["fpr_target"],
            "expected_fpr": ev["loso_operating_point"]["fpr"],
            "expected_fnr": ev["loso_operating_point"]["fnr"],
            "loso_auc": ev["loso_auc"],
        },
    }


def main():
    meta = build_meta()
    with open(META_PATH, "w") as fh:
        json.dump(meta, fh, indent=2)
        fh.write("\n")
    print(f"wrote {META_PATH}")
    print(f"  output {meta['output']['shape']} int8 scale={meta['output']['scale']:.5g}")
    print(f"  threshold {meta['classifier']['threshold']:.4f} "
          f"LOSO-AUC {meta['classifier']['loso_auc']:.3f} "
          f"expected FPR {meta['classifier']['expected_fpr']:.2f}/FNR {meta['classifier']['expected_fnr']:.2f}")


if __name__ == "__main__":
    main()
