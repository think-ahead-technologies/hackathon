# ABOUTME: Trains the conveyor-fault classifier on all labeled windows; saves the device model.
# ABOUTME: Reports honest leave-one-session-out CV AUC + operating point (the README-comparable number).
import json
import os

import numpy as np
import tensorflow as tf

import dataset
import model
import metrics
import spectro

BUILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build")
MODEL_PATH = os.path.join(BUILD, "classifier.keras")
CV_PATH = os.path.join(BUILD, "cv.json")

EPOCHS = 40
FPR_TARGET = 0.10  # operate at low miss-rate: a technician filters false alarms cheaply (README)


def _as_input(x):
    """Ensure the model input tensor shape [N, n_frames, n_bands, n_channels] float32."""
    return x.reshape((-1, spectro.N_FRAMES, spectro.N_BANDS, spectro.N_CHANNELS)).astype("float32")


def _fault_margin(logits):
    """Score = fault_logit - healthy_logit. Monotonic in P(fault); device computes the same."""
    return logits[:, 1] - logits[:, 0]


def _class_weight(y):
    n = len(y); pos = int(y.sum()); neg = n - pos
    return {0: n / (2.0 * neg), 1: n / (2.0 * pos)}  # balance the scarce fault class


def _fit(x, y, epochs, seed):
    tf.random.set_seed(seed)
    clf = model.build_classifier()
    clf.compile(optimizer=tf.keras.optimizers.Adam(1e-3),
                loss=tf.keras.losses.SparseCategoricalCrossentropy(from_logits=True),
                metrics=["accuracy"])
    clf.fit(x, y, epochs=epochs, batch_size=32, class_weight=_class_weight(y),
            verbose=0, shuffle=True)
    return clf


def loso_cv(X, y, groups, epochs, seed, silver=None):
    """Leave-one-session-out CV: pool out-of-fold fault-margin scores -> one window-level AUC.

    Every fault window is scored by a model that never saw its session — the honest
    generalization estimate, directly comparable to the README's leave-one-session-out.
    Silver (RF-labeled) windows always stay in the training fold and are never scored, so
    the number remains a clean estimate on human labels even when silver data augments training.
    """
    x = _as_input(X)
    g = np.asarray(groups)
    silver = np.zeros(len(y), bool) if silver is None else np.asarray(silver, bool)
    oof = np.full(len(y), np.nan)
    for held in sorted(set(g[~silver])):  # only human sessions are ever held out
        te = (g == held) & ~silver
        tr = ~(g == held)               # train on everything else, silver included
        if y[tr].sum() == 0 or (y[tr] == 0).sum() == 0:
            continue  # a fold with no positives (or no negatives) to learn from — skip
        clf = _fit(x[tr], y[tr], epochs, seed)
        oof[te] = _fault_margin(clf.predict(x[te], verbose=0))
    scored = ~np.isnan(oof)
    return oof, scored


def main(seed=0, epochs=EPOCHS):
    os.makedirs(BUILD, exist_ok=True)
    d = dataset.build_all(seed=seed)
    X, y, groups, silver = d["X"], d["y"], d["groups"], d["silver"]
    n_silver = int(silver.sum())
    print(f"fs {d['fs']:.0f} Hz | {len(y)} windows | {int(y.sum())} fault "
          f"({y.mean()*100:.1f}%) across {len(set(groups))} sessions"
          + (f" | +{n_silver} silver (RF-labeled, train-only)" if n_silver else ""))

    # Honest held-out number first (separate models, never deployed).
    oof, scored = loso_cv(X, y, groups, epochs, seed, silver=silver)
    cv_auc = metrics.auc(oof[scored], y[scored])
    op = metrics.operating_point(oof[scored], y[scored], FPR_TARGET)
    print(f"\nLOSO-CV window-level AUC: {cv_auc:.3f}  (README leave-one-session-out RF: 0.89)")
    print(f"  @ FPR {op['fpr']:.2f}: TPR {op['tpr']:.2f} / FNR {op['fnr']:.2f}  thr={op['threshold']:.3f}")

    # The deployed artifact: one model trained on every labeled window.
    clf = _fit(_as_input(X), y, epochs, seed)
    clf.save(MODEL_PATH)
    with open(CV_PATH, "w") as fh:
        json.dump({"loso_auc": cv_auc, "fpr_target": FPR_TARGET, "operating_point": op,
                   "n_windows": int(len(y)), "n_fault": int(y.sum())}, fh, indent=2)
    # Front-end geometry the firmware FFT must mirror — single source of truth for the contract.
    with open(os.path.join(BUILD, "feature-config.json"), "w") as fh:
        json.dump(spectro.feature_config(d["fs"]), fh, indent=2)
    print(f"\nsaved classifier -> {MODEL_PATH}")
    return cv_auc


if __name__ == "__main__":
    main()
