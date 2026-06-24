# ABOUTME: Emits Contract A (model-meta.json) for the pipeline from the trained int8 artifacts.
# ABOUTME: Single source of truth — input/output quant, front-end config, and per-unit centroid.
import json
import os

from wear_detector.export.train import BUILD

PIPELINE_META = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "dashboard", "pipeline", "model-meta.json")

# Vela reports an 11.4 KiB SRAM working set; 128 KiB gives generous TFLM interpreter +
# tensor-metadata headroom. Confirm the real arena on-target (firmware tensor_arena).
ARENA_BYTES = 131072


def build_meta():
    quant = json.load(open(os.path.join(BUILD, "quant.json")))
    base = json.load(open(os.path.join(BUILD, "baseline.json")))
    cfg = dict(base["feature_config"])
    cfg["fs"] = round(cfg["fs"], 3)

    def io(side):
        q = quant[side]
        return {"shape": q["shape"], "dtype": q["dtype"],
                "scale": q["scale"], "zero_point": q["zero_point"]}

    return {
        "_comment": "Contract A for the on-device wear model. Anomaly detector: the int8 "
                    "encoder maps a [1,49,40,1] spectrogram to a [1,K] embedding; the device "
                    "scores L2 distance to the per-unit healthy centroid, dwell-smooths it, and "
                    "alerts over threshold. Detects unknown faults (healthy-only training). "
                    "package.py merges version/sha256/size into the signed manifest.",
        "model_id": "pdm-anomaly",
        "target": "pse84/ethos-u55-128",
        "input": io("input"),
        "output": io("output"),
        "arena_bytes": ARENA_BYTES,
        "feature_config": cfg,
        "embedding": {
            "score": "l2_to_centroid",
            "dim": base["embed_dim"],
            "centroid": base["centroid"],
            "threshold": base["threshold"],
            "dwell_w": base["dwell_w"],
            "fpr_target": base["fpr_target"],
        },
    }


def main():
    meta = build_meta()
    with open(PIPELINE_META, "w") as fh:
        json.dump(meta, fh, indent=2)
        fh.write("\n")
    print(f"wrote {PIPELINE_META}")
    print(f"  output {meta['output']['shape']} int8 scale={meta['output']['scale']:.5g}")
    print(f"  embedding K={meta['embedding']['dim']} threshold={meta['embedding']['threshold']:.3f} "
          f"dwell_w={meta['embedding']['dwell_w']}")


if __name__ == "__main__":
    main()
