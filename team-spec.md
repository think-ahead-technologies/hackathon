# Team specs — Thin[gk]athon distributed AI

Role-based, not name-based — if you have more than three people, double up where noted
(a second platform person on dashboard/observability is the most useful split).
**Demo owner** is a hat someone wears from Wednesday, not necessarily a fourth person.

Three core roles: **Platform & architecture**, **Machine learning**, **Embedded & firmware**.
The whole point of these specs is clean seams: nobody blocks anybody, and each person's
expertise is load-bearing.

---

## Read first: the contracts that bind the team

Freeze these **by end of Tuesday**, even before anything works well. They are the seams
between roles — agree the shape, then build against it independently. Changing a contract
mid-stream is what breaks hackathon teams.

**Contract A — model artifact (ML → Embedded).** ML hands over an int8 `.tflite` plus a
manifest: `model_id`, `version`, input shape + dtype + quant scale/zero-point, output shape
+ what it means (e.g. anomaly score, or `[healthy, fault]`), worst-case tensor-arena size,
`sha256`. Embedded sizes memory and pre/post-processing against this — if the input shape
changes later, the firmware breaks.

**Contract B — telemetry (Embedded → Platform).** The device publishes only inference
results to a known endpoint — subject `inference.<line>.<container>`, payload
`{ts, container_id, model_version, anomaly_score, fault_class?, location?}`, ~50 bytes. Raw
and features stay on the device. Platform builds the dashboard + observability against this
schema.

**Contract C — model deploy (Platform → Embedded).** Platform delivers a new model artifact
+ manifest over `models.<line>.deploy`; Embedded writes it to flash, verifies, loads. For
the demo this can be as simple as "Platform hands the new artifact to Embedded to flash" —
the A/B-slot version is the productionized story you narrate.

**Contract D — the label loop (Platform UI → ML).** Operator labels an alert in the
dashboard; a record `{ts, container_id, feature_window_ref, label}` goes to a labeled-data
store; ML retrains from it. This loop is the demo spine — protect it.

The demo spine all four serve:
`perturb → edge detects → localize + assess → alert + operator labels → retrain + redeploy`.

---

## Role 1 — Platform & architecture

**Mandate.** Everything from the message fabric up, and own the architecture narrative —
that narrative is half the score (Concept = 50%).

**Owns:** the message fabric (NATS or MQTT), ingestion, the dashboard, observability
(OpenTelemetry spans across hops, Prometheus/Grafana), the model-deploy path, the
data-minimization policy (Vector config as a reviewable artifact), the sovereignty story,
the deck and the rings diagram.
**Does not own:** model internals, firmware/flashing, the rig.
**Interfaces:** consumes Contract B, produces Contract C, owns the dashboard side of D.

**Day by day**
- *Tue:* stand up the fabric + a stub dashboard reading **faked** inference messages on the
  agreed subject. Lock Contract B with Embedded. Lock the architecture narrative — Tuesday's
  concept work is refinement, not invention.
- *Wed:* real telemetry flowing; dashboard shows live score + alert history; add OTel spans
  with a `data.classification` attribute at each hop; build the operator-label UI (Contract D).
- *Thu AM:* observability polish, record the backup, **back up the AWS account** (deleted
  after the event). Freeze 13:30.

**Done when:** a dashboard visibly shows the loop happening, and you can tell a coherent,
defensible edge-to-cloud sovereignty story without slides.
**Fallbacks:** NATS fiddly → MQTT + a flat file or SQLite for the buffer; no cloud time →
run the dashboard locally; OTel too much → at least show the data-classification panel.
**Coach:** C H (ZEISS, cloud/software architecture) will recognise this as
the hard part — pull him in early.

## Role 2 — Machine learning

**Mandate.** A model that detects the fault, on the supported path, fast — and a retrain
that visibly improves it.

**Owns:** data (provided set first), training (DEEPCRAFT or SageMaker), the model artifact +
manifest (Contract A), retraining from labels (Contract D).
**Does not own:** flashing, the fabric, the dashboard.
**Interfaces:** produces Contract A (publish the input/output contract Tuesday so Embedded
can build against it even before the model is good), consumes Contract D.

**Approach.** Signal-first: extract features (FFT / spectral band energies, RMS, kurtosis)
rather than feeding raw signal. With scarce labels, prefer anomaly detection (autoencoder or
isolation forest on healthy data) over classification — it matches the real data situation
and tells a stronger story. Quantize to int8. **Start on the provided dataset** so you are
never blocked by rig-access hours.

**Day by day**
- *Tue:* first detector on provided data; publish the model contract to Embedded immediately
  (shape, quant, output meaning) — the contract, not the accuracy, is the Tuesday deliverable.
- *Wed:* improve the model; wire retrain-from-label; hand v2 artifact to Embedded + Platform.
- *Thu AM:* freeze the model that **demos** best; stop chasing accuracy after freeze.

**Done when:** the model visibly flips on a real perturbation, and a retrain shows measurable
improvement on a labeled fault.
**Fallbacks:** training stalls → a DEEPCRAFT Ready Model, or a plain threshold-on-feature
detector (honest and demoable). **Prep:** DEEPCRAFT is Windows-x64 only — make sure you have
a Windows machine and an account before Tuesday.

## Role 3 — Embedded & firmware

**Mandate.** Get the model running on the board against the real rig and emit results
reliably, on demand.

**Owns:** rig sensor setup, ModusToolbox build/flash, on-device pre/post-processing, the
device's publish of inference (Contract B producer), and — stretch — flash-resident A/B
model loading.
**Does not own:** training, the cloud, the dashboard.
**Interfaces:** consumes Contract A (arena sizing + pre/post), produces Contract B, consumes
Contract C.

**Day by day**
- *Tue:* board powered, sensors streaming, a "hello world" inference flashed (even a stub or
  provided model); agree Contract B with Platform; get one message onto the fabric.
- *Wed:* flash ML's real model; emit real scores; add fault localization/assessment if
  feasible; *stretch* — flash-resident model slot so a swap isn't a full reflash.
- *Thu AM:* harden so the perturbation reliably moves the score; freeze.

**Done when:** perturb the rig → score moves on-device → message hits the fabric, repeatably,
on cue for the demo.
**Fallbacks:** the open Vela/NPU path is slow → use the supported ModusToolbox + DEEPCRAFT
flash; a sensor is flaky → pick the one reliable modality (acoustic or IMU) and nail it
rather than chasing several.
**Coaches:** C S and S K (Infineon, automation) for the rig and
flashing.

## Role 4 — Demo owner (a hat, assigned Wednesday)

**Mandate.** Own the narrative end to end, cut scope ruthlessly, protect the demo. Usually
the platform person or whoever pitches best wears this from Wednesday — it is not a separate
build role.

**Owns:** the deck, the demo script and run-of-show, the backup recording, all scope
decisions about what is in or out of the live demo.

**Duties**
- *Wed:* take the pitch training on the agenda; draft the run-of-show; decide what's in/out;
  record the backup video while the loop works.
- *Thu:* rehearse and time the pitch (plan ~5–7 min + Q&A); assign who says what; route
  technical-depth questions to the right owner during Q&A.

**Done when:** a rehearsed, timed pitch with a backup video and a scope the team can actually
deliver live. If the live loop isn't closing by Wednesday evening, scope the slide down to
"detection live, loop shown" — judges forgive scoped honesty and punish overclaiming.

---

## Scoring reminder (so everyone optimizes the same thing)

Concept 50 / Realisation 30 / Team 20. Platform's architecture + sovereignty narrative is the
Concept weapon; the working loop (ML + Embedded) is Realisation; the run-of-show and clean
role split is Team. Build the minimum that works end-to-end; spend the surplus on the
narrative, not on hand-rolling toolchain.
