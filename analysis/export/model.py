# ABOUTME: Conv classifier for IMU spectrograms; healthy-vs-bearing-fault, the device model.
# ABOUTME: All ops (strided Conv2D + ReLU + Dense) are Ethos-U55/Vela-native; no decoder needed.
import tensorflow as tf
from tensorflow.keras import layers

import spectro

N_CLASSES = 2  # device output contract [1, 2]: [healthy_logit, fault_logit]


def build_classifier():
    """[*,49,40,1] -> [*,2] logits. Only Vela-native ops, so it maps fully onto the NPU.

    Same conv stack as the wear_detector encoder (strided Conv2D + ReLU + Flatten +
    Dense), with a 2-way logit head instead of an embedding — supervised on the labeled
    bearing-fault signature rather than reconstructing healthy windows. Input carries both
    the accel and gyro spectrogram channels so the model can discount turns.
    """
    inp = layers.Input((spectro.N_FRAMES, spectro.N_BANDS, spectro.N_CHANNELS), name="spectrogram")
    x = layers.Conv2D(8, 3, strides=2, padding="same", activation="relu")(inp)    # 25x20
    x = layers.Conv2D(16, 3, strides=2, padding="same", activation="relu")(x)     # 13x10
    x = layers.Conv2D(32, 3, strides=2, padding="same", activation="relu")(x)     # 7x5
    x = layers.Flatten()(x)
    x = layers.Dense(16, activation="relu")(x)
    logits = layers.Dense(N_CLASSES, name="logits")(x)  # raw logits; device argmaxes
    return tf.keras.Model(inp, logits, name="conveyor_fault_classifier")
