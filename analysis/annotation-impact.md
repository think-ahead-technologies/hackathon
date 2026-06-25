# Contribution spotlight — manual audio annotation (Waldemar)

*Pitch material: how the hand-annotation of the audio stream tightened our models.
Figure: `figures/audio-annotation-impact.png`. Acronyms expanded on first use.*

## One-liner

> Waldemar's manual audio labels turned the microphone from an **untested hypothesis**
> into a **validated fault detector** (area-under-the-ROC-curve **0.87**, proven across
> two independent recordings) — and caught a feature-design error in our automated
> analysis along the way.

## Before the labels

- All prior fault data was **inertial-sensor + magnetometer only** — there were **zero
  labelled faulty-audio recordings**. We could show the microphone has **160–320× the
  bandwidth** of the inertial sensor, but with no ground truth we **could not validate**
  an audio detector at all. Audio was a promising idea, nothing more.

## What the labels delivered

- **78 hand-labelled 5-second clips (13 fault)** across two recordings (test1: 54 clips;
  test2: 24 clips), each tagged fault / no-fault and by character (rattle / squeal).
- **Enabled validation:** the audio detector reached **area-under-curve 0.89** on one
  recording and **0.87 pooled across both** — equal-error false-positive rate 0.17,
  false-negative rate 0.15. A genuinely useful detector where there had been none.
- **Caught our mistake:** our first automated pass scored only 0.57 (≈ chance) because
  it normalised away loudness and averaged out transients. The human labels are what
  exposed that — once we matched the features to what the ear hears (sustained
  high-frequency loudness), accuracy jumped to 0.89. *Human domain expertise corrected
  the machine analysis.*
- **Proved it generalises:** the second labelled recording let us show the detector
  transfers across recordings (the ranking holds; only the threshold needs per-unit
  calibration) — the difference between "worked once" and "works."

## Why it matters for the pitch (diverse contributions)

This is a clean example of complementary roles compounding: the **analysis** found a
candidate signal, but it took the **annotator's ear** to create the ground truth that
validated an entire second sensing modality *and* fixed the analysis. Audio is now a
credible, independent detection channel — and the labelling pipeline that produced it is
exactly the feedback loop the deployed system runs on.
