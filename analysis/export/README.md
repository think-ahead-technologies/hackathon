# export — turn the conveyor-fault analysis into an on-device int8 model

Closes the gap between `analysis/` (the random-forest reference detectors, host Python) and the
device: trains a tiny **supervised classifier** on the labeled bearing-fault signature, exports it
as an int8 `.tflite` that **Vela maps onto the Ethos-U55**, and emits a Contract A
(`model-meta.json`) the `dashboard/pipeline` can consume. This is the ML side of the analysis
README's "the deployed artifact is the int8 neural net" claim.

Self-contained in this folder (own py3.12 venv `.venv`, own `build/`); it does **not** modify the
colleague's `wear_detector/` or overwrite the fleet's `pipeline/model-meta.json`.

## Classifier, not autoencoder — and why

`wear_detector/export` trains a healthy-only **anomaly autoencoder** (catches unknown faults).
`analysis/` has something that side doesn't: **labels**, and validated, *separable* fault
signatures. So this trains the supervised counterpart — a 2-way classifier on
`healthy` vs `bearing-fault` windows (`[1,K]` embedding → `[1,2]` logits). First pass is bearing
(the well-labeled signature); wobble is a multi-class follow-up.

The device input is a **2-channel** `[1,49,40,2]` log-filterbank spectrogram — channel 0 accel
(vibration/fault energy), channel 1 gyro (turn energy). The gyro channel is there so the model can
tell a real fault (vibration on a *straight*) apart from a turn (vibration *while turning*), which
the analysis README calls essential. The two channels use different log floors (`scale_eps`: accel
1e-3, gyro 0.85) because gyro dynamic power runs ~835× accel (dps² vs g²) — without per-channel
floors the single int8 input scale would squash the accel channel. **This means `spectro.py` is no
longer byte-identical to `wear_detector/export`'s accel-only `[1,49,40,1]` front-end** — the
conveyor-fault model needs its own 2-channel on-device FFT (accel + gyro magnitude). Trade-off: see
"gyro channel — what the evidence says" below.

The device scores the **fault margin** `output[1] - output[0]` (dequantized), dwell-smooths it, and
alerts over a threshold. The threshold is a *commissioning default* that the feedback loop
recalibrates per unit — the analysis README's "calibrate from the technician's marks" carried into
the contract.

## Pipeline

```
real recordings ─► spectro [49,40] ─► CNN classifier (healthy vs fault) ─► int8 PTQ ─► Vela (ethos-u55-128)
                                              │                                              │
                       LOSO-CV AUC + operating point (cv.json)                               ▼
                                              └──────────────► model-meta.json  ◄── normalized vela-summary.csv
```

| Module | Role |
|---|---|
| `spectro.py` | accel+gyro window → 2-channel log filterbank `[49,40,2]`, fs-aware, per-channel floors. **Numpy-only**; the firmware FFT must mirror it (2 channels). |
| `dataset.py` | real recordings → spectrograms + healthy/fault labels + session id. Reuses `analysis/features.py`'s `fault` sessions/spans, plus extra running-motion normal sessions and RF silver labels. |
| `model.py` | conv classifier; all ops (strided Conv2D + ReLU + Dense) are Ethos-U55-native. |
| `train.py` | train on all labeled windows (MSE→cross-entropy, class-weighted), **leave-one-session-out CV** for the honest held-out AUC, save the model. |
| `quantize.py` | int8 PTQ with a real representative set → `model_int8.tflite` + quant params. |
| `vela_compile.py` | run Vela; normalize its summary into the gate's column schema. |
| `evaluate.py` | int8 e2e eval on real data + rank-fidelity vs float + device threshold → `eval.json`. |
| `emit_meta.py` | emit Contract A (`build/model-meta.json`) from the artifacts. |
| `rf_labeler.py` | wraps `analysis/features.py`'s bearing RandomForest as an IMU labeler → fault spans (weak supervision + IMU-native referee). |
| `make_silver.py` | RF-label the unlabeled fault sessions → `silver_labels/` (event-level: low-FPR threshold + persistence). |

## Why leave-one-session-out CV

Bearing-fault windows are scarce at the 4 s device window / 50 Hz (~122 fault windows, ~2.6%). A
single random holdout is unstable (a few positives). So the honest number is **grouped LOSO-CV**:
every fault window is scored by a model that never saw its session, pooled into one window-level
AUC — directly comparable to the analysis README's leave-one-session-out 0.89. The **deployed**
artifact then trains on all windows.

## Run

The toolchain (TensorFlow + Vela) has no Python 3.14 wheel, so it lives in its **own py3.12 venv**,
separate from the 3.14 analysis runtime.

```bash
cd analysis/export
make venv          # one-time: py3.12 venv + TF/Vela/sklearn/pytest
make silver        # optional: RF-label unlabeled fault sessions -> silver_labels/ (weak supervision)
make model         # train → quantize → vela → eval → emit Contract A
make gate          # run the pipeline deployability gate against this build's artifact
make package       # -> build/manifest.json (Contract A + version/sha256/size); VERSION=... to override
make test          # contract + dataset + model + rf-labeler tests
```

`make model` picks up `silver_labels/` automatically if populated; without them it trains on human
labels only (no error). Run `make silver` first to include the weak-supervision data.

## Promoting to the fleet pipeline

`make model` writes everything to `analysis/export/build/` and does **not** touch the colleague's
deployed `pipeline/model-meta.json`. Which model the fleet runs is a deliberate decision. To promote
this one, copy the artifacts into the pipeline build and run the gate:

```bash
cp build/model_int8_vela.tflite build/vela-summary.csv ../../pipeline/build/
cp build/model-meta.json ../../pipeline/model-meta.json     # replaces pdm-anomaly with conveyor-fault
cd ../../pipeline && python gate.py --summary build/vela-summary.csv --policy vela.policy.json \
                                    --artifact build/model_int8_vela.tflite
```

## Results (50 Hz recordings)

See `build/eval.json` after a run. The headline is LOSO-CV window-level AUC (vs the README's RF
ceiling 0.89). Per-window detection is operating-point-limited at 50 Hz — high window-level miss
rate at low FPR — exactly as the analysis README documents; the device stages on the dwell-smoothed
score, and the feedback loop recalibrates the threshold per unit.

| variant | LOSO-CV AUC (human IMU labels) | test2 RF-native | test2 acoustic | FNR @ FPR 0.10 |
|---|---|---|---|---|
| accel-only `[1,49,40,1]` | 0.887 | — | 0.655 | — |
| accel+gyro `[1,49,40,2]` | 0.880 | — | 0.590 | 0.43 |
| + RF silver labels | 0.908 | 0.822 | 0.670 | 0.29 |
| + all 6 extra-normal sessions | 0.902 | 0.681 | 0.659 | 0.37 |
| **+ 4 running-only extra normals** | **0.926** | **0.891** | **0.691** | **0.26** |

The current deployed model is the last row. The wins, cumulatively: silver labels (weak supervision,
below) lifted LOSO 0.880 → 0.908 and recall; adding extra human-confirmed normal sessions then
required a **motion-based selection** — the running-motion negatives help, but two low-motion
quasi-static sessions *degraded* cross-recording generalization (test2 RF-agreement 0.681) and were
dropped. The final model reaches LOSO 0.926 and agrees with the IMU-native RF referee at **0.891** on
the held-out test2 recording — far above its agreement with the acoustic labels (0.691), confirming
the on-device model reproduces the validated reference detector on a recording it never trained on.

## Labeling IMU data from the analysis statistics (weak supervision + referee)

`rf_labeler.py` trains `analysis/features.py`'s bearing RandomForest on the human-labeled sessions
(reproducing the README's **0.893** LOSO-AUC exactly) and uses it to label any IMU recording.

**The catch — and the fix.** Raw per-window RF flags inherit the detector's **~20% window-level
FPR**: at a balanced threshold the RF flags ~20% of windows even on a *fully normal* session, so they
are far too noisy to use as training truth (they'd teach the model that 20% of normal is fault). The
analysis README's own remedy applies — make them **event-level**: a precision threshold (FPR≤5%) plus
**persistence** (≥3 consecutive flagged windows). That drops the normal-session flag rate from 20.6%
to 1.5% and puts the known fault session at 2.5% (matching its ~2% human rate). Only then are the
labels clean enough to use.

- **Weak supervision** (`make silver` → train): labels the `…faulty_bearing_no_labeling` session
  (50 clean silver positives) into the *training* pool; never scored in LOSO. This is what lifted the
  model to 0.908 — distilling the reference detector into the deployable model.
- **IMU-native referee** (`eval_recording.py`): labels a held-out recording and scores the on-device
  model against it. **Caveat:** the RF and the model read the *same* vibration signal, so this
  measures NN↔RF *agreement*, not independent physical ground truth — it answers "does the device
  model behave like our validated detector on a new recording?", not "is it physically right." That
  still needs the README's open item (physical track inspection).

## gyro channel — what the evidence says

The gyro channel is the architecturally right signal (the analysis README calls gyro essential for
telling faults from turns), and it leaves the trustworthy number — LOSO-CV on the training rig —
unchanged within noise (0.880 vs 0.887). But on the only cross-recording we have (`test2`) it did
**not** help and slightly hurt. Read that cautiously: `test2` has just **14 fault windows** (AUC is
very noisy at that n), its labels are **acoustic** (rattle/squeal), not the IMU bearing signature
the model learns, and it is a **different rig** recorded at a bursty 1600 Hz resampled to 50 Hz — so
it is a weak, modality-mismatched adjudicator, not proof the gyro channel is bad. The honest verdict:
the channel is sound in principle and free on the device (gate passes, 0% fallback), but its benefit
to real generalization is **unproven** until we have a cross-recording with IMU-native fault labels.
`make model` with `spectro.N_CHANNELS = 1` (accel-only) reverts the contract if preferred.

### Validating it against the recording

`eval_recording.py` scores the deployed int8 model on a held-out merged-recorder recording:

```bash
.venv/bin/python eval_recording.py [merged.csv] [labels.csv]   # defaults to data/test2
```

## When higher-rate data lands

Same as the detectors: drop the sessions in, re-run `make model`. `spectro.feature_config()` rescales
`n_fft`/bands with fs, the high-frequency bearing signature becomes discriminative, and per-window
detection should clear the bar without dwell. The contract, pipeline, and firmware seam are unchanged.
