# ABOUTME: Shape/contract tests for the conveyor-fault classifier (run in the py3.12 export venv).
# ABOUTME: Locks I/O to the device contract [1,49,40,1] -> [1,2] and keeps every op Vela-native.
import numpy as np
import pytest

pytest.importorskip("tensorflow")  # TF tests run only in the py3.12 export venv

import model
import spectro


def test_classifier_output_is_two_class_contract():
    clf = model.build_classifier()
    x = np.zeros((2, spectro.N_FRAMES, spectro.N_BANDS, spectro.N_CHANNELS), np.float32)
    y = clf.predict(x, verbose=0)
    assert y.shape == (2, model.N_CLASSES)


def test_classifier_input_matches_device_contract():
    clf = model.build_classifier()
    assert tuple(clf.input.shape) == (None, spectro.N_FRAMES, spectro.N_BANDS, spectro.N_CHANNELS)


def test_classifier_ops_are_vela_friendly():
    # Only Conv2D / ReLU / Flatten(Reshape) / Dense(FullyConnected) may reach the NPU.
    allowed = {"InputLayer", "Conv2D", "Flatten", "Dense", "Reshape"}
    kinds = {type(l).__name__ for l in model.build_classifier().layers}
    assert kinds.issubset(allowed), f"non-Vela op in classifier: {kinds - allowed}"
