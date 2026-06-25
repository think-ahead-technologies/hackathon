# ABOUTME: Int8 PTQ of the trained classifier -> model_int8.tflite (int8 in/out) for Ethos-U55.
# ABOUTME: Representative set is real healthy+fault windows; emits the input/output quant params.
import json
import os

import numpy as np
import tensorflow as tf

import dataset
import model
from train import BUILD, MODEL_PATH, _as_input

TFLITE_PATH = os.path.join(BUILD, "model_int8.tflite")
QUANT_PATH = os.path.join(BUILD, "quant.json")


def _representative(seed=0, n=300):
    d = dataset.build_all(seed=seed)
    X = d["X"]
    rng = np.random.default_rng(seed)
    X = X[rng.permutation(len(X))[:n]]  # real windows spanning healthy + fault
    x = _as_input(X)

    def gen():
        for i in range(len(x)):
            yield [x[i:i + 1]]
    return gen


def _quant_params(detail):
    scale, zero = detail["quantization"]
    return {"shape": [int(s) for s in detail["shape"]],
            "dtype": np.dtype(detail["dtype"]).name,
            "scale": float(scale), "zero_point": int(zero)}


def main(seed=0):
    clf = tf.keras.models.load_model(MODEL_PATH)
    conv = tf.lite.TFLiteConverter.from_keras_model(clf)
    conv.optimizations = [tf.lite.Optimize.DEFAULT]
    conv.representative_dataset = _representative(seed)
    conv.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    conv.inference_input_type = tf.int8
    conv.inference_output_type = tf.int8
    tfl = conv.convert()

    os.makedirs(BUILD, exist_ok=True)
    with open(TFLITE_PATH, "wb") as fh:
        fh.write(tfl)

    interp = tf.lite.Interpreter(model_content=tfl)
    interp.allocate_tensors()
    inp, out = interp.get_input_details()[0], interp.get_output_details()[0]
    params = {"input": _quant_params(inp), "output": _quant_params(out),
              "n_classes": model.N_CLASSES, "bytes": len(tfl)}
    with open(QUANT_PATH, "w") as fh:
        json.dump(params, fh, indent=2)

    print(f"int8 tflite: {len(tfl)} bytes -> {TFLITE_PATH}")
    print(f"input  {params['input']['shape']} {params['input']['dtype']} "
          f"scale={params['input']['scale']:.5g} zp={params['input']['zero_point']}")
    print(f"output {params['output']['shape']} {params['output']['dtype']} "
          f"scale={params['output']['scale']:.5g} zp={params['output']['zero_point']}")
    return params


if __name__ == "__main__":
    main()
