# Wear detection — architecture & rationale (from-scratch design)

Grounded in three datasets, each cited where used:
- **`thinkathon_kickstart`** — 50 Hz IMU, **figure-8** rig, healthy *and* labeled bearing-fault sessions
  (incl. a 2/4/8-bearing severity ladder). A few sessions also have a mic.
- **`data/test1`** (100 Hz) / **`data/test2`** (1600 Hz) — **conveyor** rig, multimodal (IMU + 16 kHz
  audio + onboard camera). test2 has **human clip labels** (`data/test2/labels.csv`).

Numbers below are post-ground-truth. Where an earlier draft overclaimed, the correction is called out.

## Thesis (revised after ground truth)

**The foundation — a clean healthy baseline and human labels — matters more than the sensor or the
model.** With them, a plain detector reaches **AUC 0.79–0.87** (kickstart bearing faults). Without them,
nothing we built beat **~0.75** (test2). On that foundation: **fuse modalities** (which one leads is
*fault-dependent*), use the **camera as the label oracle**, hang everything off **one clock**, and
**measure every detector against labels** instead of trusting it by ear or eye.

## What ground truth changed (corrections to earlier drafts)

1. **"Audio is THE primary sensor" → lead modality is fault-dependent.** On kickstart the IMU *alone*
   detects bearing faults at **AUC 0.79** (50 Hz, energy-only, no audio). On the conveyor the faults are
   acoustic. The durable answer is fusion, not a fixed primary.
2. **"High sample rate is load-bearing" → fault-specific, not universal.** Kickstart bearing faults are
   caught at **50 Hz** once a healthy baseline exists. 1600 Hz mattered only for test2's particular
   >400 Hz signature. A clean baseline beats a faster sensor.
3. **The squeal/tonal detector was detecting the engine.** It "found" 45–49 squeals (~1.5 kHz) — that is
   the steady motor tone. Against test2 labels it scores **AUC 0.47 (no separation)**. Lesson burned in:
   validate against labels (`label_eval.py`) before believing any detector.
4. **The IMU "AUC 0.79–0.84" was vs *acoustic events*, not human labels.** Against the labels: audio
   loudness 0.75, IMU crest/impulsiveness 0.74, **fusion 0.78**. They're complementary (corr 0.42) —
   crest is the *worst* audio feature but the *best* IMU one. One labeled fault type ("vibration") is
   invisible to both. So fusion is real but, on the conveyor with no baseline, still weak.
5. **Two rigs, not one.** kickstart = figure-8: the gyro lap localizer works (11 clean 122 s laps, and
   the bearing fault is correctly `locus=onboard`). test1/test2 = conveyor: the lap model is wrong there
   → localize by **station pass-by** / camera instead. The localizer isn't broken, it's rig-specific.

## The foundation is the biggest lever (now proven both directions)

| Dataset | Setup | Best result |
|---|---|---|
| thinkathon_kickstart | healthy baseline + clean labeled bearing faults | **AUC 0.79 / dwell 0.87** |
| test2 (conveyor) | no baseline, ~6 noisy in-run labels, heterogeneous faults | rms 0.75 / fusion 0.78 (weak) |

Same family of algorithm; the gap is entirely data setup. Three deliberate captures beat any model change:
1. **One clean healthy run** → calibrated FPR + per-unit baselines (the thing kickstart has and test2 lacks).
2. **A single-route, multi-pass run** → track-vs-onboard localization (test1 had too few comparable passes).
3. **A no-handling run** → a clean negative set (operators were ~⅓ of test1's "anomalies").

## Architecture

```
        ┌── one clock: device t_dev (within-stream) + host_us (cross-modal) ──┐
mic 16kHz ─▶ acoustic detector (baseline-relative, directed) ──┐
IMU ───────▶ per-unit/self baseline · impulsiveness · spectral ─┤
   gyro/orientation gate · transit/dwell/handled                ├─▶ FUSION ─▶ Contract B alert
camera ────▶ frames indexed by host_us ─────────────────────────┘   (score + state + locus
            └── anomaly → auto-extracted frame → operator label ──┘     + frame + reason)
```

- **Foundation first.** A per-unit *healthy* baseline when one exists (kickstart-style, the strong case);
  the self-baseline (median/MAD + directed score, `detector.py` / `audio.py`) as the fallback when it
  doesn't (test1/test2). Same code path — collecting a healthy run upgrades it.
- **Sync layer.** The recorder's clocks are messy: bursty `t_rel`, a device clock `t_dev` that resets
  between contiguous capture segments, IMU dropouts. One module owns "give me modality X on a clean
  timeline with gaps annotated"; nothing downstream re-derives fs (`io_imu.load_merged_csv*`).
- **Two detectors, fused.** Audio (loudness/directed) and IMU (impulsiveness via crest, plus spectral
  via the per-segment `burst_features.py` extractor) carry *complementary* evidence (corr 0.42) — fuse,
  don't pick. Bearing faults lean IMU; the conveyor faults lean audio.
- **Gating/state.** `motion_gate.py` + a gravity-orientation gate (for active pickups); segment
  transit/dwell/handled so anomalies are scored only in comparable states.
- **Camera = label oracle.** `frames.py` aligns each anomaly to its frame on the shared clock; the
  `clip_grid.py` browser tool turns clips into human labels; `label_eval.py` scores every detector
  against those labels (audio, IMU, and fusion). This is the loop that ends the guessing.
- **Decision.** Emit an alert that says *score + machine-state + locus + a frame + why*, not a bare
  number crossing a threshold.

## Keep / change

**Keep:** the per-unit/self baseline directed detector (it hits 0.79/0.87 with a baseline); shared-clock
multi-modal correlation; motion gate; per-segment burst extractor; the figure-8 localizer **for figure-8
rigs**; and the measurement discipline (`clip_grid.py` + `label_eval.py`) — that's the most valuable
thing we built.

**Change / demote:** "audio is primary" → fusion, fault-dependent; "needs 1600 Hz" → a clean baseline
matters more; the **tonal/squeal detector → demote**, it's an engine detector (AUC 0.47 vs labels);
treat the localizer as rig-specific (figure-8 vs conveyor-station), not universal.

## On the embedded model

Earlier draft said the 50 Hz IMU autoencoder "solves the wrong problem" — that was too strong. On
kickstart it detects bearing faults at **50 Hz, AUC 0.79**, *given a healthy baseline* — so for
bearing-type faults the on-device IMU model is viable as-is. It only falls short for faults whose
signature is above its Nyquist (test2's >400 Hz tones) or when no baseline exists. So: keep the IMU
model for bearing/impulsive faults; add audio on-device only if the target fault is acoustic-dominant;
and either way, ship the baseline + a labeling path, because that — not the model — is what moved AUC
from 0.75 to 0.87.
