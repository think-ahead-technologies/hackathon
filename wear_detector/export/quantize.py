# ABOUTME: Int8 PTQ of the trained encoder -> model_int8.tflite (int8 in/out) for Ethos-U55.
# ABOUTME: Representative set is real healthy+fault windows; emits the input/output quant params.
import json
import os

import numpy as np
import tensorflow as tf

from wear_detector.export import dataset, model
from wear_detector.export.train import BUILD, ENCODER_PATH, _nchw1

TFLITE_PATH = os.path.join(BUILD, "model_int8.tflite")
QUANT_PATH = os.path.join(BUILD, "quant.json")


def _representative(seed=0, n=300):
    d = dataset.build(seed=seed)
    pool = np.concatenate([d["X_train"], d["X_fault"]])  # span normal + anomalous range
    rng = np.random.default_rng(seed)
    pool = pool[rng.permutation(len(pool))[:n]]
    x = _nchw1(pool)

    def gen():
        for i in range(len(x)):
            yield [x[i:i + 1]]
    return gen


def _quant_params(interp, detail):
    scale, zero = detail["quantization"]
    return {"shape": [int(s) for s in detail["shape"]],
            "dtype": np.dtype(detail["dtype"]).name,
            "scale": float(scale), "zero_point": int(zero)}


def main(seed=0):
    encoder = tf.keras.models.load_model(ENCODER_PATH)
    conv = tf.lite.TFLiteConverter.from_keras_model(encoder)
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
    params = {"input": _quant_params(interp, inp), "output": _quant_params(interp, out),
              "embed_dim": model.EMBED_DIM, "bytes": len(tfl)}
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
