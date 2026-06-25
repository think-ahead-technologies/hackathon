# ABOUTME: End-to-end int8 eval: runs the quantized tflite on real windows, checks rank fidelity.
# ABOUTME: Headline number is train.py's LOSO-CV AUC; this confirms quantization didn't lose it.
import json
import os

import numpy as np
import tensorflow as tf

import dataset
import metrics
from train import BUILD, MODEL_PATH, CV_PATH, _as_input, _fault_margin

TFLITE_PATH = os.path.join(BUILD, "model_int8.tflite")
EVAL_PATH = os.path.join(BUILD, "eval.json")


def _int8_margins(tfl_path, x):
    interp = tf.lite.Interpreter(model_path=tfl_path)
    interp.allocate_tensors()
    inp, out = interp.get_input_details()[0], interp.get_output_details()[0]
    in_scale, in_zp = inp["quantization"]
    out_scale, out_zp = out["quantization"]
    margins = np.empty(len(x), dtype=np.float64)
    for i in range(len(x)):
        q = np.round(x[i:i + 1] / in_scale + in_zp).astype(np.int8)
        interp.set_tensor(inp["index"], q)
        interp.invoke()
        logits = (interp.get_tensor(out["index"]).astype(np.float64) - out_zp) * out_scale
        margins[i] = logits[0, 1] - logits[0, 0]
    return margins


def main(seed=0):
    d = dataset.build_all(seed=seed)
    X, y = d["X"], d["y"]
    x = _as_input(X)

    clf = tf.keras.models.load_model(MODEL_PATH)
    float_m = _fault_margin(clf.predict(x, verbose=0))
    int8_m = _int8_margins(TFLITE_PATH, x)

    float_auc = metrics.auc(float_m, y)
    int8_auc = metrics.auc(int8_m, y)
    # Rank agreement: does int8 order windows the same as float? (quantization fidelity)
    order_f = np.argsort(float_m); order_i = np.argsort(int8_m)
    rank_corr = float(np.corrcoef(np.argsort(order_f), np.argsort(order_i))[0, 1])

    cv = json.load(open(CV_PATH))
    # Device threshold lives in the *deployed int8* margin domain. The LOSO operating
    # point estimates the FPR/FNR you'll actually see; this threshold is the commissioning
    # default the feedback loop later recalibrates per unit (README).
    int8_op = metrics.operating_point(int8_m, y, cv["fpr_target"])
    print(f"in-sample float AUC {float_auc:.3f} | int8 AUC {int8_auc:.3f} "
          f"(rank corr {rank_corr:.3f})")
    print(f"honest LOSO-CV AUC  {cv['loso_auc']:.3f}  (README RF ceiling 0.89)")
    print(f"int8 device threshold {int8_op['threshold']:.4f} (margin); "
          f"expected FPR {cv['operating_point']['fpr']:.2f} / FNR {cv['operating_point']['fnr']:.2f}")

    out = {"loso_auc": cv["loso_auc"], "insample_float_auc": float_auc,
           "insample_int8_auc": int8_auc, "int8_vs_float_rank_corr": rank_corr,
           "loso_operating_point": cv["operating_point"], "int8_operating_point": int8_op,
           "fpr_target": cv["fpr_target"]}
    with open(EVAL_PATH, "w") as fh:
        json.dump(out, fh, indent=2)
    print(f"wrote {EVAL_PATH}")
    return out


if __name__ == "__main__":
    main()
