# Pitch deck — content & speaker notes

Conveyor-belt fault detection from the boxes, not the track. Draft slide-by-slide
content for the presentation (hand to Claude Design for visual polish). Figures live in
[`analysis/figures/`](analysis/figures). Acronyms expanded for a mixed audience.

Suggested length: ~13 slides, ~6–8 minutes.

---

## Slide 1 — Title
**On-board fault detection for conveyor systems**
*Catching faults from the boxes that ride the line — and getting smarter every shift.*
- Team names + the one-line: a self-improving, on-device condition-monitoring system.

**Notes:** Open with the hook — "Conveyor failures stop a whole line. Today you find them
when something breaks. We detect them early, from sensors *on the boxes themselves* — no
trackside retrofit — and the system improves itself in service."

---

## Slide 2 — The problem
- Conveyor/idler-bearing failures cause unplanned downtime; lines can be **kilometres long**.
- Instrumenting the track doesn't scale. **Instrument the boxes that already travel it.**
- Constraint we embraced: **only box-mounted sensors** (inertial + microphone) — no track sensors.

**Notes:** Stress scale and the strategic choice. A roving sensor on the product carrier
sees the whole line for the price of one device. This is an infrastructure problem as much
as a data problem — which is where our team's strength is.

---

## Slide 3 — What we had to work with
- Box-mounted **inertial sensor** (accelerometer + gyroscope) and a **16 kHz microphone**.
- Annotated recordings of induced faults (faulty idler-wheel bearings; quasi-static wobble).
- A known rig: 55 cm driven wheels, 2.5 cm idler rollers (the parts that fail), empty 39 cm box.

**Notes:** We validated everything against *annotated* recordings, so our numbers are
measured, not hoped-for. The rig geometry let us reason about the physics, not just fit curves.

---

## Slide 4 — There's a real, repeatable signal
*Figure: `analysis/figures/bearing-fault-signature.png`*
- A faulty bearing = a short, repeatable **high-frequency vibration burst** as the box rolls past it.
- Six independent fault events, same shape — not a one-off.

**Notes:** This is the "we found something real" slide. Each red window is a labelled fault;
the vibration envelope spikes the same way every time. Then: we turned this into detectors.

---

## Slide 5 — Two validated fault types
*Figure: `analysis/figures/detector-scorecard.png`*
- **Bearing fault** (vibration): area-under-the-ROC-curve **0.89**.
- **Wobble / instability** (low-frequency sway): **0.90** in its regime.
- Both **held-out** (tested on recordings the model never trained on) — generalisation, not memorisation.

**Notes:** Area-under-the-ROC-curve: 0.5 is a coin-flip, 1.0 is perfect, ~0.9 is strong.
Two physically different faults, two detectors, both validated.

---

## Slide 6 — We measured it like operators, not academics
*Figure: `analysis/figures/detector-fpr-fnr-tradeoff.png`*
- We report **false alarms vs missed faults** at explicit operating points — the numbers that matter on a line.
- Single-pass detector is a strong **screen**, not yet a standalone **alarm** — and we say so.
- The honest gap is exactly what the feedback loop (slide 10) closes.

**Notes:** This is our credibility slide and plays to the infra-consultant mindset. Accuracy
headlines are cheap; we care about false-alarm rate because that's what wakes someone at 3am.
Being upfront about the screen-vs-alarm gap is a strength, not a weakness.

---

## Slide 7 — A second sense: the microphone
*Figures: `analysis/figures/mic-vs-imu-bandwidth.png` + `analysis/figures/audio-cross-recording.png`*
- The microphone sees **160–320× the bandwidth** the inertial sensor can — where the bearing "ring" lives.
- A faulty bearing is simply **louder and more sustained in the high band** — "rattle/squeal" to a human.
- Validated **across two independent recordings**: area-under-curve **0.87** (78 labelled clips).

**Notes:** Redundant, independent confirmation of a fault from a second physical channel.
The bandwidth chart is the "why a mic" proof; the right chart is the validated result.

---

## Slide 8 — Human + machine: the annotation paid off
*Figure: `analysis/figures/audio-annotation-impact.png`*
- Before manual labels: audio was an **untested hypothesis** (zero labelled faulty-audio existed).
- Manual labels **validated** the audio detector **and caught a feature-design error** in our automated pass (0.57 → 0.89).
- Domain expertise (the ear) and analysis compounding — a preview of the feedback loop.

**Notes:** Credit Waldemar's annotation. This is the "diverse contributions compound" moment
and it motivates why human-in-the-loop is the engine, not a bolt-on.

---

## Slide 9 — Where on the track? (localisation)
*Use Johannes' rendered video: camera feed + live green dot on the track map.*
- Every detection can be placed on the track — timestamp → position.
- Turns "a fault happened" into "a fault happened **there**" → a track health map; send a technician to the spot.

**Notes:** Play ~10 s of Johannes' video. This is the visual that lands with judges. Note it
also lets us check whether repeated detections cluster at one place (a real fault) vs scatter.

---

## Slide 10 — The USP: a system that improves itself
*Figure: `analysis/figures/feedback-loop-concept.png`*
- The product isn't a classifier — it's a **loop**: device detects → operator triages in the UI → corrections retrain/recalibrate → update pushed back to the device.
- The system gets **better the more it's used**; it tunes itself to each site/unit.
- (We simulated this to back the shape — `feedback-loop-potential.png`.)

**Notes:** This is the headline. Frame it as an operations system, not a model. The earlier
honest false-alarm gap is closed here: high recall + a human filter + self-calibration.

---

## Slide 11 — On the device, by design
- Runs **on the sensor box** — the chip captures more than it can transmit, so inference belongs there; only a verdict leaves.
- Target: Infineon PSoC Edge E84 + neural accelerator; the cheap detectors are a filter + average + threshold (no neural network needed for v1).
- Deploy path already scaffolded (firmware reflash, telemetry, label UI). See [`NEXT_STEPS.md`](NEXT_STEPS.md).

**Notes:** Key insight: the transfer link is the bottleneck, not the sensor — so the high-rate
signal only exists on-device. Our v1 detector is cheap enough to hand-write in C; no tooling
bottleneck. The infrastructure for deploy + feedback already exists.

---

## Slide 12 — Built by a team, playing to strengths
- **Martin** — statistical analysis & model understanding.
- **Waldemar** — high-level analysis, planning, model generation + audio annotation.
- **Johannes** — localisation (video processing).
- **Vitalii** — device hardware + flashing models on-device.
- **Manu & Rico** — infrastructure & UI.
- **Waldemar, Manu & Rico** — over-the-air model updates driven by UI feedback.

**Notes:** The "diverse contributions" slide. Each link in the loop has an owner; mostly
infrastructure consultants turning an edge-AI idea into a deployable operations system.

---

## Slide 13 — Where we are / the ask
- **Done:** validated detectors (vibration + audio), localisation, deploy scaffolding, the loop design.
- **Next:** minimal detector on-device → live alerts in the UI → close the feedback loop.
- One line: *self-improving, on-device fault detection that scales to the whole line.*

**Notes:** End on the demo-able milestone (on-device detection → alert in the UI) and the
vision (a line that monitors itself and gets smarter every shift).

---

### Figure index (all in `analysis/figures/`)
| slide | figure |
|---|---|
| 4 | bearing-fault-signature.png |
| 5 | detector-scorecard.png |
| 6 | detector-fpr-fnr-tradeoff.png |
| 7 | mic-vs-imu-bandwidth.png, audio-cross-recording.png |
| 8 | audio-annotation-impact.png |
| 9 | Johannes' localisation video |
| 10 | feedback-loop-concept.png (backing: feedback-loop-potential.png) |

Deeper material if asked: `analysis/README.md` (full results + the spectral/kinematic
analysis, severity trend, cross-talk, wobble recalibration).
