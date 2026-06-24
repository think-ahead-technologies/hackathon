# wear_detector — multi-feature IMU wear detector with per-unit baseline

Implements phases 1–2 of `thingkathon-ml-wear-pipeline.md`: a per-device, healthy-only
anomaly detector over multi-feature IMU windows. Built against the 50 Hz recordings on disk,
architected so the 3200 Hz upgrade pays off with **no code change**.

## Modules

- `io_imu.py` — loads Imagimob `IMU-Data.data` + `*.label` files; `iter_windows()` yields
  time-ordered labeled windows. Window length is derived from the **inferred** sample rate, so
  50 Hz → 50-sample windows and 3200 Hz → 3200-sample windows automatically.
- `features.py` — sample-rate-aware feature extraction (time, spectral, envelope). FFT bands span
  `[0, Nyquist]`, so the band layout scales with fs. `detector_feature_names(fs)` returns the
  **energy-only** profile below 400 Hz and the **full spectral+envelope** profile at/above it.
- `detector.py` — `PerUnitBaselineDetector`: fit on healthy windows only, robust per-feature
  baseline (median/MAD), score = mean of *positive* standardized deviations (directional —
  wear only adds energy). Raw score → 0..1 via the healthy empirical CDF. `method="mahalanobis"`
  kept for comparison.
- `evaluate.py` — end-to-end on real recordings: per-window AUC, dwell AUC, turn-trap FPR,
  severity ladder. Doubles as the integration/e2e test.
- `contract.py` — §5 `StageMachine` (hysteresis/dwell/trend) + §7 `contract_b()` payload builder.
- `emit_contract_b.py` — streams a session → NDJSON Contract B records; threshold calibrated from
  healthy data to ~5% FPR, staging on the ~5 s causal smoothed score, plus `track_position` /
  `fault_locus` from the localizer.
- `localize.py` — phase 4 (§6): signed-gyro turn detection, figure-8 crossover landmarks, lap
  segmentation, route-variant clustering, per-variant `TrackHealthMap`, track-vs-onboard contrast.
- `localize_eval.py` — phase 4 demo on real recordings.
- `export/` — turns this detector into an **on-device int8 `.tflite`**: a healthy-only
  autoencoder whose encoder (`[1,49,40,1] → [1,K]`) Vela maps 100% onto the Ethos-U55. The device
  scores L2 distance to a per-unit healthy centroid (this baseline, in a learned feature space), so
  it detects **unknown** faults. Feeds the existing `dashboard/pipeline` gate/sign/deploy. See
  [`export/README.md`](export/README.md).

## Getting it on the device

The statistical detector above is the **reference algorithm**; the device runs an int8 neural net on
the Ethos-U55. `export/` bridges the two — `cd export && make venv && make model` trains, quantizes,
Vela-compiles, and emits `dashboard/pipeline/model-meta.json` (Contract A), after which the existing
pipeline (gate → cosign → registry → A/B device swap) ships it. Result: **0 CPU fallback, dwell TPR
0.44 @ 5% FPR**, gate green. Full write-up in [`export/README.md`](export/README.md).

## Run

```bash
.venv/bin/python -m pytest wear_detector/tests/ -q   # unit tests (25)
.venv/bin/python wear_detector/evaluate.py           # detector real-data evaluation
.venv/bin/python wear_detector/localize_eval.py      # phase-4 localization demo
.venv/bin/python -m wear_detector.emit_contract_b \
    thinkathon_kickstart/data/<session> --container unit-08   # NDJSON Contract B
```

## Results on the 50 Hz recordings

| | AUC | notes |
|---|---|---|
| Mahalanobis (per-window) | 0.502 | whitens away the energy signal — see below |
| **Directed (per-window)** | **0.791** | matches the univariate ceiling |
| **Directed + 5 s dwell** | **0.873** | TPR 0.27 @ 5% FPR — wear persists |

Severity ladder (directed, median normalized score): healthy **0.48** → 2/4/8 bearings
**0.77 / 0.76 / 0.74** — clear healthy↔fault step, no monotonic grading (matches §0.1).
Turn-trap FPR 0.005 (gyro excluded from the detector).

## Design notes (why it is built this way)

- **Directional, not Mahalanobis.** The discriminative direction (broadband energy) is also the
  highest-variance healthy direction (idle vs. motion). Mahalanobis's `Σ⁻¹` down-weights exactly
  that direction and a symmetric distance treats "unusually quiet" like "unusually loud" → AUC
  collapses to 0.50. A one-sided deviation score matches the physics and recovers AUC 0.79.
- **fs-aware feature set.** At 50 Hz the spectral/shape features are noise (AUC 0.55–0.66) and
  dilute the score, so they are excluded; the energy features carry detection. At 3200 Hz Nyquist
  reaches 1600 Hz, the spectral + envelope features become discriminative, and
  `detector_feature_names(fs)` includes them automatically.
- **Per-unit baseline.** One baseline per device anchors "this unit's normal" (spec §4). Current
  data is a single rig at mixed operating points — that operating-point spread is the main limit
  on per-window TPR; per-unit *and* per-condition baselining would tighten it.
- **Dwell.** Per-1s-window detection at 50 Hz is weak; a ~5 s rolling mean (spec §5) lifts AUC to
  0.87 because faults are sustained while healthy spikes are transient.

## Contract B staging at 50 Hz (decision-layer reality)

Calibrated to ~5% FPR, the fraction of windows reaching stage ≥ 2:

| Session type | stage ≥ 2 |
|---|---|
| Healthy | ~5–6% |
| Fault — long loop / 2-4-8 ladder | ~5% (no separation) |
| Fault — short controlled (12:05, 12:11) | 16–22% (fires) |
| Fault — other short (12:08, 12:10) | 0% (peak just under threshold) |

Per-session staging discriminates inconsistently at 50 Hz — clean on some controlled fault
recordings, invisible on loop runs where operating-point motion dominates. The emitter and state
machine are correct; the signal is the limit. Expect these rates to pull apart at 3200 Hz.

## Localization at 50 Hz (phase 4, §6)

Track is a **figure-8 with variable routing**, so a fixed lap period smears. The localizer uses the
**signed gyro turn-signal**: the `+ − + +` per-lap signature, with the minority `−` turn as the
figure-8 crossover landmark. Laps are segmented between crossovers (any length) and grouped by route
variant (duration cluster). Measured (contrast = peak_bin / median_bin):

| Session | turn-energy map | fault-anomaly map |
|---|---|---|
| Healthy | contrast 59 @ φ=0.81 | 1.76 → onboard |
| Fault (onboard bearing) | contrast 56 | 1.72 → onboard ✓ |
| Variable-route (3 variants) | contrast 60 | 1.59 → onboard |

Turn energy proves the machinery resolves position (high contrast); the onboard bearing fault is
uniform across positions → correct **onboard** verdict. `emit_contract_b.py` emits per-window
`track_position` (lap phase) and a per-variant accumulated `fault_locus` (unknown → onboard/track).
No track fault exists in the data, so the high-contrast "track" verdict is shown only via the
validation signal.

## When 3200 Hz data lands

No code change needed: drop the new sessions into `thinkathon_kickstart/data/`, point `evaluate.py`
at them. The window sizer and feature profile switch on the inferred fs; the spectral/envelope/
`crest`/`kurtosis` features that were dead at 50 Hz should now carry the bearing signature — the
test of whether per-window (non-dwell) detection becomes strong.
