# ABOUTME: Conv autoencoder for healthy IMU spectrograms; the encoder is the device model.
# ABOUTME: Encoder ops (strided Conv2D + ReLU + Dense) are Ethos-U55/Vela-native; decoder is train-only.
import tensorflow as tf
from tensorflow.keras import layers

from wear_detector.export import spectro

EMBED_DIM = 8  # K: bottleneck width == device output contract [1, K]


def build_encoder():
    """[*,49,40,1] -> [*,K]. Only these ops reach Vela, so all are Ethos-U55 accelerable."""
    inp = layers.Input((spectro.N_FRAMES, spectro.N_BANDS, 1), name="spectrogram")
    x = layers.Conv2D(8, 3, strides=2, padding="same", activation="relu")(inp)    # 25x20
    x = layers.Conv2D(16, 3, strides=2, padding="same", activation="relu")(x)     # 13x10
    x = layers.Conv2D(32, 3, strides=2, padding="same", activation="relu")(x)     # 7x5
    x = layers.Flatten()(x)
    emb = layers.Dense(EMBED_DIM, name="embedding")(x)  # linear embedding for distance scoring
    return tf.keras.Model(inp, emb, name="encoder")


def build_autoencoder(encoder=None):
    """Full AE for training. Decoder is discarded before deployment, so its ops are unconstrained."""
    encoder = encoder or build_encoder()
    emb_in = layers.Input((EMBED_DIM,))
    x = layers.Dense(7 * 5 * 32, activation="relu")(emb_in)
    x = layers.Reshape((7, 5, 32))(x)
    x = layers.Conv2DTranspose(32, 3, strides=2, padding="same", activation="relu")(x)  # 14x10
    x = layers.Conv2DTranspose(16, 3, strides=2, padding="same", activation="relu")(x)  # 28x20
    x = layers.Conv2DTranspose(8, 3, strides=2, padding="same", activation="relu")(x)   # 56x40
    x = layers.Conv2D(1, 3, padding="same")(x)
    out = layers.Resizing(spectro.N_FRAMES, spectro.N_BANDS)(x)  # exact [49,40,1]
    decoder = tf.keras.Model(emb_in, out, name="decoder")
    ae = tf.keras.Model(encoder.input, decoder(encoder.output), name="autoencoder")
    return ae, encoder
