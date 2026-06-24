# ABOUTME: Shape/contract tests for the autoencoder + encoder (run in the py3.12 export venv).
# ABOUTME: Locks encoder I/O to the device contract [1,49,40,1] -> [1,K] and AE reconstruction shape.
import numpy as np
import pytest

pytest.importorskip("tensorflow")  # TF tests run only in the py3.12 export venv

from wear_detector.export import model, spectro


def test_encoder_output_is_embedding_contract():
    enc = model.build_encoder()
    x = np.zeros((2, spectro.N_FRAMES, spectro.N_BANDS, 1), np.float32)
    y = enc.predict(x, verbose=0)
    assert y.shape == (2, model.EMBED_DIM)


def test_encoder_input_matches_device_contract():
    enc = model.build_encoder()
    assert tuple(enc.input.shape) == (None, spectro.N_FRAMES, spectro.N_BANDS, 1)


def test_autoencoder_reconstructs_input_shape():
    ae, enc = model.build_autoencoder()
    x = np.zeros((2, spectro.N_FRAMES, spectro.N_BANDS, 1), np.float32)
    r = ae.predict(x, verbose=0)
    assert r.shape == (2, spectro.N_FRAMES, spectro.N_BANDS, 1)


def test_encoder_ops_are_vela_friendly():
    # Only Conv2D / ReLU / Flatten(Reshape) / Dense(FullyConnected) may reach the NPU.
    allowed = {"InputLayer", "Conv2D", "Flatten", "Dense", "Reshape"}
    kinds = {type(l).__name__ for l in model.build_encoder().layers}
    assert kinds.issubset(allowed), f"non-Vela op in encoder: {kinds - allowed}"
