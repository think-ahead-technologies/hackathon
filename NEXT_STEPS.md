# NEXT STEPS — from laptop analysis to on-device detection

**Where we are:** we have validated fault detectors (the "shiny analysis") running on a
laptop in [`analysis/`](analysis/README.md) — bearing faults and wobble from the inertial
sensor, and a bearing-fault audio indicator, all with measured false-positive / false-negative
rates. **Where we need to get:** that detection running *on the sensor device*, its output
flowing into the UI, and (time permitting) a feedback loop that improves it.

This is the plan and the division of labour. Acronyms expanded on first use.

---

## The arc

```
 [A] laptop analysis        →   [B] minimal algorithm    →   [C] compile + push      →   [D] telemetry + UI      →   [E] feedback loop
     (done / ongoing)            (shrink to C-able form)      (onto the device)            (get data back)             (improve over time)

     analysis/README.md          a few cheap features /       firmware reflash             Contract B telemetry,       operator marks FP/FN,
     reference detectors         thresholds, fixed-point      pipeline (Contract C)        dashboard + label-ui        retrain/recalibrate (Contract D)
```

Each step feeds the next. The good news: the **team already built the scaffolding** for
C, D and E (the dashboard README's spine: `perturb → edge detects → alert → operator
labels → retrain`, Contracts A/B/C/D). The new work is plugging *our* detector into it.

---

## Workstreams (parallelisable)

### A — Analysis  *(owner: Martin; Claude assisting)*  — ONGOING, not blocking
- 1600 Hz inertial burst analysis (show the bearing ring directly, un-aliased).
- Per-unit baseline calibration so thresholds transfer across devices (the "scales to a
  km-long line" story).
- **Deliverable that unblocks B:** a one-page *minimal-detector spec* — the exact
  features, window sizes and thresholds the device must compute (candidates below).

### B — Minimal algorithm  *(owner: needs a colleague)*  — depends on A's spec
Shrink the laptop detectors to something compilable to C and tiny enough for the device.
- **Strong candidates (cheap by design):**
  - bearing: high-frequency vibration envelope (root-mean-square of >5 Hz accelerometer)
    **gated by gyroscope** to reject turns;
  - audio: median high-band (>2 kHz) loudness over a short window.
  Both are a high-pass filter + windowed root-mean-square + a threshold — trivially
  C-able, no neural network needed.
- Decide: cheap-feature detector (ours, supervised, explainable) vs the int8 neural-net
  autoencoder already prototyped in [`wear_detector/export/`](wear_detector/export) — or
  ship the cheap one first as the baseline.
- **Deliverable:** reference C (or fixed-point Python) implementation whose output matches
  the laptop detector on the recorded data (bit-for-bit-ish), plus its measured
  false-positive / false-negative rates so we know what we're deploying.

### C — Compile + push to device  *(owner: needs a colleague)*  — depends on B
- Integrate the minimal algorithm into the device firmware
  ([`firmware-app/`](firmware-app) already reads the real BMI270 inertial sensor live on
  the CM55 core and runs inference).
- Use the existing reflash path ([`firmware/`](firmware) — A/B flash slots, signature
  verification, Contract C chunked model push over NATS) to get it on the device.
- **Deliverable:** the device computes the detector locally and emits a verdict/score.

### D — Telemetry + UI  *(owner: needs a colleague)*  — depends on C
- Stream the device's verdicts/scores back via **Contract B (telemetry)** into the
  dashboard ([`README.md`](README.md) / `dashboard/`) — **two purposes:**
  1. populate the UI for its own sake (operator sees live alerts);
  2. **validate the minimal model** — compare on-device output against the laptop model
     and the labelled recordings to confirm we didn't lose accuracy in the shrink.
- **Deliverable:** live device alerts visible in the UI + a validation comparison
  (on-device vs laptop false-positive / false-negative rates).

### E — Feedback loop  *(owner: needs a colleague)*  — depends on D, do as much as time allows
- Operator marks alerts as false-positive / false-negative in the UI
  ([`label-ui/`](label-ui), clip-grid labeling tool) → **Contract D (labels)** → retrain
  /recalibrate → redeploy via C.
- **Highest-value, lowest-effort slice:** auto-**recalibrate the threshold** from the
  operator's marks (no retraining needed). Analysis already shows the threshold needs
  per-recording/per-unit calibration, so this alone is a real win. See the design warning
  in [`analysis/README.md`](analysis/README.md) (don't only label flagged items).

### Localisation  *(owner: Johannes)* — ✅ DONE
Maps a detection timestamp to a track position. Johannes has a **pre-rendered video**
showing (a) the camera feed and (b) a live green dot on a track map marking current
position. This unblocks workstream **F** below.

### F — Use localisation  *(owner: needs a colleague; details TBC tomorrow)* — NEW, now unblocked
Now that timestamp → track-position exists, wire it in. Items (flesh out tomorrow):
- **Get the data stream:** obtain the underlying position data from Johannes (timestamp
  → track position), not just the rendered video — that's what we can join to detections.
- **Join detections to positions:** tag every fault detection with *where* on the track
  it happened → a **track health map** (which segments are faulty, how often).
- **Validate the "false alarms":** check whether our extra detections cluster at
  consistent track positions (→ likely *real* faults we hadn't labelled) vs scattered
  (→ noise). This is the test that was blocked on localisation; it could show our true
  false-positive rate is lower than measured.
- **UI:** show live detections on Johannes' map + camera view (green dot + alert overlay)
  — a compelling demo visual; combine with the telemetry from **D**.
- **(stretch)** per-position / per-segment baselines so the detector adapts to where the
  box is on the track.

Joins the spine at **D/E** (UI + feedback); does not block B→C.

---

## Critical path & suggested staffing

```
A.spec → B → C → D → E
```
- A and Localisation run in parallel and don't block B–E once A's minimal-detector spec
  is handed over (small — it's a list of features + thresholds).
- B → C → D is the spine to get *something live*; aim to get the cheap bearing detector
  all the way to the UI first, then add audio, then E.
- If short on people: **B+C together** (one firmware-capable person) and **D+E together**
  (one dashboard-capable person) is a sensible split.

## Definition of done (demo)
A perturbed bearing → the device detects it locally → an alert appears in the UI → an
operator can mark it → that mark adjusts the model. Even reaching "alert appears in the
UI from an on-device detection" (through step D) is a complete, demoable story.
