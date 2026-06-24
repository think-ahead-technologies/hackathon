# export — turn the wear detector into an on-device int8 model

Closes the gap between `wear_detector` (the reference algorithm, host Python) and the device:
trains a tiny **anomaly-detection autoencoder**, exports the **encoder** as an int8 `.tflite` that
**Vela maps 100% onto the Ethos-U55**, and emits the Contract A the `dashboard/pipeline` already
consumes. This is the ML side of `model-pipeline.md`'s handoff boundary.

## Why an autoencoder embedding, not a classifier

The device contract was `[1,2]` (a healthy-vs-fault classifier). A classifier only recognises the
faults it was trained on — useless for the *unknown* failure that predictive maintenance exists to
catch. So this trains an autoencoder on **healthy windows only** and exports the encoder
`[1,49,40,1] → [1,K]`. The device scores **L2 distance from a per-unit healthy centroid**: anything
unlike normal — including fault modes never seen in training — lands far from the manifold and fires.

That is `wear_detector`'s per-unit median/MAD baseline, carried into a learned feature space: the
centroid is the unit's "normal", recomputable per board at commissioning **without retraining the
network**. The output contract changed `[1,2] → [1,K]` accordingly (`model-meta.json` `embedding`).

## Pipeline

```
real recordings ─► spectro [49,40] ─► AE (healthy only) ─► encoder int8 PTQ ─► Vela (ethos-u55-128)
                                                  │                                     │
                          per-unit centroid + dwell threshold (baseline.json)           ▼
                                                  └──────────► model-meta.json  ◄── normalized vela-summary.csv
                                                                    │                   │
                                                          dashboard/pipeline: package → gate → sign → registry → deploy
```

| Module | Role |
|---|---|
| `spectro.py` | accel window → log filterbank `[49,40]`, fs-aware. **Numpy-only**; the firmware FFT must mirror `feature_config()`. |
| `dataset.py` | real recordings → spectrograms + healthy/fault labels + per-unit id (reuses `evaluate.py`'s split). |
| `model.py` | conv AE; encoder ops (strided Conv2D + ReLU + Dense) are all Ethos-U55-native. Decoder is train-only. |
| `train.py` | train on healthy windows (MSE), save the encoder. |
| `quantize.py` | int8 PTQ with a real representative set → `model_int8.tflite` + quant params. |
| `vela_compile.py` | run Vela; normalize its summary into the gate's column schema (real Vela 5.1 columns differ). |
| `baseline.py` | per-unit centroid + distance threshold (the per-unit baseline). |
| `eval_embedding.py` | e2e eval on real data (int8) + writes `baseline.json`. |
| `emit_meta.py` | emit `dashboard/pipeline/model-meta.json` (Contract A) from the artifacts. |

## Run

The toolchain (TensorFlow + Vela) has no Python 3.14 wheel, so it lives in its **own py3.12 venv**,
separate from the 3.14 runtime detector.

```bash
cd wear_detector/export
make venv          # one-time: py3.12 venv + TF/Vela/pytest
make model         # train → quantize → vela → eval → emit Contract A
make test          # TF tests (3.12) + pure tests (3.14)
```

Then the existing pipeline takes over (board-free), now on the **real** artifact:

```bash
cd ../../dashboard/pipeline
python gate.py --summary build/vela-summary.csv --policy vela.policy.json \
               --artifact build/model_int8_vela.tflite        # PASS
# make package / make promote / make deploy-artifact  → signed → registry → device
```

## Results (50 Hz recordings)

| | value | note |
|---|---|---|
| Vela mapping | **5/5 ops on NPU, 0 CPU fallback** | 11.4 KiB SRAM, 15 KiB flash — passes the gate clean |
| per-window AUC (int8) | **0.732** | matches the float AE (quantization is rank-lossless) and `wear_detector`'s 0.79 ceiling |
| dwell TPR @ 5% FPR | **0.44** | spec §5 operating point; beats the statistical detector's 0.27 |
| severity ladder (median dist) | healthy 7.1 → 8.2 / 8.0 / 7.4 | clear healthy↔fault step, non-monotonic — matches §0.1 |

Per-window detection is operating-point-limited at 50 Hz (healthy high-motion windows look "far from
normal"), exactly as `wear_detector` documents; the device stages on the dwell-smoothed score.

## When 3200 Hz data lands

Same as the detector: drop the sessions in, re-run `make model`. `spectro.feature_config()` rescales
`n_fft`/bands with fs, the high-frequency bearing signature becomes discriminative, and per-window
detection should clear the bar without dwell. The contract, pipeline, and firmware seam are unchanged.
