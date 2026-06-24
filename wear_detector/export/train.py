# ABOUTME: Trains the conv autoencoder on healthy spectrograms only; saves the encoder.
# ABOUTME: Healthy-only reconstruction => unknown faults score far from the learned manifold.
import os

import numpy as np
import tensorflow as tf

from wear_detector.export import dataset, model
from wear_detector.evaluate import auc

BUILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build")
ENCODER_PATH = os.path.join(BUILD, "encoder.keras")


def _nchw1(x):
    return x.reshape((-1, x.shape[1], x.shape[2], 1)).astype("float32")


def main(epochs=60, seed=0):
    os.makedirs(BUILD, exist_ok=True)
    tf.random.set_seed(seed)
    d = dataset.build(seed=seed)
    x_tr = _nchw1(d["X_train"])
    print(f"fs {d['fs']:.0f} Hz | train {len(x_tr)} healthy | "
          f"test {len(d['X_healthy_test'])} healthy / {len(d['X_fault'])} fault")

    ae, encoder = model.build_autoencoder()
    ae.compile(optimizer=tf.keras.optimizers.Adam(1e-3), loss="mse")
    ae.fit(x_tr, x_tr, epochs=epochs, batch_size=32, validation_split=0.1,
           verbose=2, shuffle=True)

    # Float-domain sanity: distance to the healthy embedding centroid should separate
    # healthy from fault before we ever quantize.
    z_tr = encoder.predict(x_tr, verbose=0)
    centroid = z_tr.mean(axis=0)
    dist = lambda x: np.linalg.norm(encoder.predict(_nchw1(x), verbose=0) - centroid, axis=1)
    a = auc(dist(d["X_fault"]), dist(d["X_healthy_test"]))
    print(f"\nfloat embedding distance-to-centroid AUC: {a:.3f}")

    encoder.save(ENCODER_PATH)
    print(f"saved encoder -> {ENCODER_PATH}")
    return a


if __name__ == "__main__":
    main()
