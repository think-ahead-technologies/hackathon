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
- `audio.py` — **acoustic** wear path over the 16 kHz mic recording. Per-window linear-tri band
  energies (8 kHz Nyquist — the spectral resolution the IMU never had), scored by the same
  `PerUnitBaselineDetector` but fit **self-referentially**: with no healthy recording, the mostly-
  nominal run is its own baseline and the built-in track errors surface as the high-energy minority.
- `audio_eval.py` — runs the acoustic scan on a recording and clusters the flagged windows into
  events (the track errors recur across laps): `python -m wear_detector.audio_eval data/test1/<rec>.wav`.
- `audio_localize.py` — **audio↔lap-position correlation**: bins the acoustic anomaly score by
  figure-8 lap phase (laps from the gyro, §6) to ask *do the faults recur at a fixed track position?*
  Phase-locked → high spatial contrast → **track** defect; uniform → **onboard** wear. Needs ≥2
  comparable laps to argue recurrence, else the verdict is held at *inconclusive*.
- `frames.py` — **audio↔camera correlation**: frame names carry a host-µs stamp and the IMU CSV pins
  `host_us = 1e6·t_rel + origin`, so each acoustic anomaly maps to the nearest camera frame on one
  shared clock. `python -m wear_detector.frames data/test1/anomaly_frames` prints the table and
  extracts the matched frames — ground-truth on *what the machine was doing* at each anomaly.
- `burst_features.py` — **per-segment spectral extractor** for high-rate IMU. The 1600 Hz stream is
  delivered as a few contiguous capture segments (the device clock resets between them); this Welch-
  averages FFTs *within* each segment and pools across, so the spectrum never smears across a gap.
  `burst_spectral_features` shares `features.spectral_shape`'s keys — a drop-in for the spectral subset.
- `fault_bundle.py` — **inspection bundle**: per motion-gated event, extract the camera frame + a short
  audio clip + an `index.md` so a human can see/hear each fault. `python -m wear_detector.fault_bundle
  <csv> <wav> <frames-zip-or-dir> <out-dir>`.
- `fault_location.py` — **camera-based location**: cluster fault frames by visual similarity (same view
  = same spot on the line). On test2, 4 of 5 faults cluster to one station — recurrence = a track defect
  there. Used by `fault_bundle` to add a `location` column (needs Pillow; clustering math is dep-free).
- `imu_band_probe.py` — quantifies whether the IMU "hears" the fault (>400 Hz energy at events vs
  baseline; AUC ~0.8 on test2). The high-band signature exists only at >=1600 Hz.
- `motion_gate.py` — **motion gate**: only assess wear while the unit is actually moving. The carrier
  rotates through the line under power (tens of dps of yaw) while a parked or hand-held unit sits near
  zero, so gating each anomaly on the gyro energy at its peak instant drops handling/parked artifacts.
  Threshold is data-driven (a percentile of the run's windowed gyro RMS), so it isn't a per-rig magic
  number; an IMU dropout defaults to *keep* (absence of data isn't evidence of rest).
- `io_imu.py::load_imu_csv` — loader for the merged-recorder CSV (`data/test1/*.csv`): 100 Hz IMU in
  SI units, bursty timestamps reconstructed to a uniform timeline. `iter_windows` takes a `.csv` path
  and a `session_label` for the unlabeled recordings (e.g. tag a whole fault session `"fault"`).
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
.venv/bin/python -m wear_detector.audio_eval \
    data/test1/merged_20260623_17xx.wav                      # acoustic anomaly events
```

## Acoustic anomaly path (16 kHz, no healthy data)

`data/test1/` is a single recording from a track with **faults built in** — and there is no healthy
recording to fit against. Two facts drive the approach: the IMU upgrade to 100 Hz is real but its
8 kHz-short Nyquist still hides the bearing/wear band, while the 16 kHz **mic** reaches it; and the
faults are *in the track*, so they recur at fixed positions lap after lap while most of the run is
nominal. So `audio.py` fits the robust per-unit baseline (median/MAD) on the session's **own**
windows — the nominal majority defines "normal," and the track errors stand out as the high-energy
minority. The directed score (wear only adds energy) and the empirical-CDF mapping are unchanged from
the IMU detector; only the front-end (band energies of the audio window) and the baseline source
(self vs. a separate healthy unit) differ. On `data/test1` this surfaces ~9 distinct anomaly events
across the 270 s run — candidate track-error positions. Without a healthy reference this is detection,
not calibrated FPR; a healthy lap recording would turn the self-baseline back into a true per-unit one.

The camera frames (`frames.py`) ground-truth those events, and they split in two: genuine in-transit
mechanical pass-bys (the green drive wheel / roller stations) and **operator handling** of the unit
(legs/gloved hands in frame, mostly at the end of the run). The latter are not machine faults, so
`motion_gate.py` removes them — an anomaly heard while the unit is parked or being handled (near-zero
gyro) is dropped. On `data/test1` the gate keeps **5 of 9** events (the in-transit mechanical ones,
including one over an IMU dropout, kept conservatively) and suppresses **4** (three handling events +
one parked station-dwell). Limit worth knowing: a handling event *with* motion (an active pickup that
rotates the unit) can still pass the IMU gate — separating that needs the orientation/gravity vector
or the camera, not motion energy alone.

### Localizing it: track defect vs. onboard wear

A built-in *track* fault recurs at the same position every lap; *onboard* wear is everywhere. So
`audio_localize.py` segments figure-8 laps from the gyro (§6, in the IMU wall clock) and bins the
acoustic anomaly score by lap phase, sharing the recording-start origin with the audio. Phase-locked
energy → high spatial contrast → **track**; uniform → **onboard**. The wiring is verified on synthetic,
time-aligned IMU+audio (faults injected at a fixed lap phase resolve to `track`, contrast > 2, peak at
the injected phase). On `data/test1` the call is **inconclusive**: the run yields only ~3 laps and the
route varies enough that the dominant variant holds a single lap — high *within-lap* contrast, but you
can't argue *across-lap* recurrence from one lap. A longer or less route-variable recording (≥2
comparable laps) is what turns this into a real track-vs-onboard verdict.

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

## When higher-fs data lands

No code change needed: point the loaders at the new recording. The window sizer and feature profile
switch on the inferred fs; the spectral/envelope/`crest`/`kurtosis` features that were dead at 50 Hz
now carry the bearing signature.

`data/test2` (`merged_1600hz`) is the first taste: the IMU steps at 625 µs (**1600 Hz nominal**,
Nyquist 800 Hz), and `detector_feature_names(1600)` automatically expands from 9 energy features to
**28** — the spectral bands, centroid, roll-off, HF ratio and kurtosis all switch on, exactly as
designed. The acoustic + motion-gate + camera pipeline runs on it unchanged (`frames.py` now also
reads a directory, e.g. a 7z extracted with `bsdtar`): 6 acoustic events, the motion gate drops 1
(a person at the rig, confirmed in-frame), the other 5 are in-transit drive-wheel/roller pass-bys.

**How test2 is actually sampled (and the per-segment extractor).** The device clock `t_dev_us` steps
a uniform 625 µs and only resets 7 times, so the recording is **7 contiguous 1600 Hz capture segments**
(0.6–8.3 s each, ~26 s of device time); the bursty host `t_rel` is just slow USB drain of the buffer,
not signal gaps. The signal is therefore contiguous *within* a segment but has real time gaps *between*
segments. `burst_features.py` is the right front-end: it finds the segments (`split_bursts`), Welch-
averages overlapping FFTs *within* each (no FFT across a boundary), and pools across segments —
`burst_spectral_features` returns the same keys as `features.spectral_shape`, so it's a drop-in.
`python -m wear_detector.burst_features <csv>` prints the per-segment summary.

Two honest results on test2: (1) because there are only 7 segments (6 boundaries in 41 k samples), a
naive glued FFT is barely smeared here — burst-aware ≈ glued (centroid 112 vs 118 Hz); the per-segment
method matters more when captures are short/many. (2) Even with correct 1600 Hz extraction the IMU
energy is **low-band** (centroid ~110 Hz, ~4% above 400 Hz), so in test2 the discriminative fault
signal is in the 16 kHz **audio**, not the IMU high bands.
