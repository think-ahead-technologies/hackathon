# Wear detection — architecture & rationale (from-scratch design)

What we'd build if we started over, grounded in what the `data/test1` (100 Hz) and `data/test2`
(1600 Hz) recordings actually showed. Each claim cites the evidence; the modules referenced exist.

## Thesis

**Audio is the primary sensor. The high-rate IMU is a corroborating second channel. The camera is the
label oracle. Everything hangs off one clock, and the system assumes "no healthy data" and "operators
in the loop" from day one.**

## What the data forced (the non-obvious calls)

1. **Audio-primary, IMU-corroborating — not IMU-only.** Every fault we could find was acoustic (~9
   events in test1, ~6 in test2). But the IMU is *not* blind: at the motion-gated acoustic events the
   IMU **>400 Hz energy fraction is elevated** (rank AUC ≈ 0.79–0.84 vs in-motion baseline), with the
   spectrum shifting into 200–700 Hz (×1.6–1.9). It's faint and the broadband/impulsiveness features
   (rms, crest, kurtosis) miss it entirely — only a targeted high-band feature sees it. So fuse the two;
   don't pick one. Reproduce: `python -m wear_detector.imu_band_probe <csv> <wav>`.

2. **High sample rate is load-bearing for the IMU channel.** The fault signature lives at 200–700 Hz.
   At 50–100 Hz (test1) Nyquist is ≤50 Hz — that band *cannot exist*, so the IMU genuinely is blind
   there. At 1600 Hz it appears. The 1600 Hz upgrade isn't a nicety; it's what makes the IMU a sensor
   at all for this fault. And it only pays off with the per-segment extractor (below).

3. **Self-supervised by default.** There is no healthy recording and may never be one cheaply. The
   robust within-session baseline (median/MAD + directed score, `detector.py` / `audio.py`) works with
   zero labels because the run is mostly nominal. A per-unit healthy baseline is a drop-in upgrade on
   the same code path when a clean run exists.

4. **Operators are a first-class signal, not noise.** ~⅓ of test1's acoustic "anomalies" were a person
   handling the unit (confirmed in-frame). The motion gate (`motion_gate.py`) suppresses parked/handled
   events via gyro energy; an active pickup that rotates the unit still needs a gravity-orientation gate
   or the camera. Build state-awareness in, don't bolt it on.

5. **The camera is ground truth — make it a labeling instrument.** The highest-leverage thing we built
   was audio↔frame correlation on a shared clock (`host_us = 1e6·t_rel + origin`, `frames.py`). It's
   what separated "mechanical pass-by" from "human hand in frame." Productize it: every anomaly
   auto-extracts its frame into an operator triage gallery → tag (fault / handling / station-pass) →
   that closes the dashboard label loop with visual evidence and grows a labeled set for free.

6. **Localize with the right physical model.** The rig is a **conveyor with an onboard cart passing
   fixed stations** (the green drive wheel / rollers recur in-frame), *not* a figure-8 track. The
   inherited gyro figure-8 lap localizer (`localize.py`) is the wrong model — it's why test1 came back
   "inconclusive". Localize by **station pass-by**; track-vs-onboard = does the anomaly recur at the
   same station across passes.

## Architecture

```
        ┌── one clock: device t_dev (within-stream) + host_us (cross-modal) ──┐
mic 16kHz ─▶ acoustic detector (self-baseline, directed score) ──┐
IMU 1600Hz ▶ per-segment spectral (200-700Hz) + state machine ───┤
   gyro/orientation gate · transit/dwell/handled                 ├─▶ fusion ─▶ Contract B alert
camera ────▶ frames indexed by host_us ──────────────────────────┘   (score + state + station
            └── anomaly → auto-extracted frame → operator label ──┘     + frame + reason)
```

- **Sync layer first.** The recorder's clocks are messy: bursty `t_rel`, a device clock `t_dev` that
  resets between captures, IMU dropouts, and (test2) the high rate delivered as 7 contiguous capture
  *segments*, not a uniform stream. One module owns "give me modality X on a clean timeline with gaps
  annotated"; nothing downstream re-derives fs. (`io_imu.load_merged_csv*`.)
- **Acoustic detector** — self-baseline, directed score, empirical-CDF normalization (`audio.py`).
- **IMU channel** — `burst_features.py`: find contiguous segments and Welch-average FFTs *within* each
  (never across a gap), then read the 200–700 Hz shape. Fuse its score with the acoustic score.
- **Gating/state** — `motion_gate.py` + a gravity-orientation gate; segment transit/dwell/handled so
  anomalies are scored only in comparable states.
- **Camera** — label oracle + (later) a tiny "person in frame?" auto-filter.
- **Fusion/decision** — emit an alert that says *score + machine-state + station + a frame + why*,
  not a bare number crossing a threshold.

## Keep / change

**Keep:** self-baseline directed detector; shared-clock multi-modal correlation; motion gate;
per-segment burst extractor; the contract/supply-chain framing and dashboard label loop.

**Change / demote:** figure-8 localizer (wrong model → station pass-by); "IMU is just a gate"
(→ corroborating detector at ≥1600 Hz); on-device IMU model tuned at 50 Hz with the energy-only
profile (→ 1600 Hz + 200–700 Hz bands, or move on-device detection to audio and keep IMU for gating).

## The biggest lever is data collection, not algorithms

Roughly half the effort fought data problems: no healthy baseline, operator handling, clock ambiguity,
wrong rig model. Three deliberate captures would beat any model change:

1. **One clean healthy run** → calibrated FPR + per-unit baselines.
2. **A single-route, multi-pass run** → track-vs-onboard localization (test1 had too few comparable
   passes to conclude).
3. **A no-handling run** → a clean negative set.

## On the embedded model

The device currently runs an int8 IMU autoencoder over a 50 Hz-era accel→[49,40] spectrogram. Given
the evidence, that's viable **only** if it runs at 1600 Hz and targets the 200–700 Hz band; otherwise
move on-device *detection* to audio and keep the IMU model for cheap on-device *gating*. Shipping a
50 Hz IMU wear-detector for a fault whose signature is above 50 Hz Nyquist solves the wrong problem.
