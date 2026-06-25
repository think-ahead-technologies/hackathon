# Pitch deck — 5-minute cut

Conveyor fault detection. **Hard limit: 5 min talk + 3 min Q&A.** Audience has been here
2 days — **no context/setup slides.** Headline = the self-improving loop. Theme (Bob Ross)
lives in the *visuals* (Claude Design); the spoken script stays tight. Figures in
[`analysis/figures/`](analysis/figures). Acronyms expanded for a mixed audience.

**Hook:** "Most fault detectors ship and slowly rot. Ours ships and gets *sharper* every shift."
**Throughline (repeat at the end):** "Every fault we catch teaches it to catch the next one faster."

Target ~4.5 min, 6 slides + title — leaves breathing room.

---

### Slide 1 — Title + hook  (~15s)
- Project name + one line.
- Say the hook. That's it. Don't explain the setup.

**Notes:** "Most fault detectors ship and slowly rot. Ours ships and gets sharper every
shift. Here's how."

---

### Slide 2 — What we detect  (~50s)  · *figure: detector-scorecard.png*
- Two physically different faults, from box sensors: **bearing wear** and **wobble/instability**.
- Both **validated on held-out data**: area-under-the-ROC-curve **~0.9** (0.5 = chance, 1.0 = perfect).

**Notes:** Lead straight into results — they know the rig. Two faults, two detectors, both
~0.9 on recordings the model never saw. Real, not a single lucky number.

---

### Slide 3 — Real, and measured honestly  (~45s)  · *figure: detector-fpr-fnr-tradeoff.png*
- The signal is repeatable; we report **false alarms vs missed faults**, not just accuracy.
- One pass is a strong **screen**, not yet a clean **alarm** — and we say so.

**Notes:** This is the credibility beat — we think like operators. False-alarm rate is what
wakes someone at 3am. We name the gap on purpose, because the next slide closes it.

---

### Slide 4 — A second sense, and a happy accident  (~50s)  · *figure: audio-annotation-impact.png*
- The **microphone** independently confirms bearing faults (area-under-curve **0.87**, two recordings).
- Our automated pass was wrong (0.57) until a **human's labels caught the error** → 0.89.  *(happy accident)*

**Notes:** Two independent physics agreeing is strong. Then the human story: the annotator's
ear caught what our model missed — a person and the model improving each other. That's the
feedback loop in miniature — which is the whole product.  *(Bob Ross "happy accident" line lands here.)*

---

### Slide 5 — The product: a loop that improves itself  (~75s)  · *figure: feedback-loop-concept.png*
- **Detect on-device → operator confirms in the UI → model retrains/recalibrates → pushed back.** Gets sharper every shift; tunes to each site.
- **Localisation** makes it actionable: "a fault — *there*, segment X" → a work order, and something the operator can actually confirm. (Without *where*, the loop has nothing to close on.)
- Runs **on the device**; only a verdict leaves. (Production deploy path — signed, A/B-rollback, zero-trust — is built; *happy to go there in questions*.)

**Notes:** THE money slide. This closes slide 3's honest gap: high recall + a human filter +
self-calibration. Localisation is the enabler (don't oversell it — table-stakes — but it's
what makes the alert actionable). Park the deploy/security stack as Q&A; don't burn time on it.

---

### Slide 6 — Team + close  (~35s)
- Built by the team, each owning a link in the loop (analysis · annotation · localisation · embedded/flashing · infrastructure & UI · over-the-air updates).
- **Where we are:** validated detectors + localisation + the loop design + deploy scaffolding.
- Close on the throughline: *"Every fault we catch teaches it to catch the next one faster."*

**Notes:** Diverse contributions, mostly infrastructure people turning an edge-AI idea into a
deployable operations system. End on the throughline.

---

## Q&A backup (have these ready, don't present)
- **Deploy/security:** Vela gate → cosign-sign → registry → GitOps promote → A/B flash with rollback; per-device credentials + TLS (CRA-aligned). The *minimal* loop just recalibrates the on-device threshold from feedback — no reflash needed.
- **Why audio works:** faults are louder & sustained in the high band; mic sees 160–320× the inertial-sensor bandwidth (`mic-vs-imu-bandwidth.png`).
- **The physics:** bimodal vibration spectrum; real bearing energy is >25 Hz and aliases on a slow sensor → case for higher sample rate / on-device (`bearing-spectrum-vs-kinematics.png`).
- **Severity:** signature grows with defect burden then saturates — a direction, not a counter (`fault-severity-trend.png`).
- **Detectors don't interfere:** orthogonal signatures (`detector-crosstalk.png`); wobble needs per-unit recalibration (`wobble-recalibration.png`).
- **On-device plan:** PSoC Edge E84 — features on the M55, optional neural net on Ethos-U55, connectivity on M33. Cheap detector = filter + average + threshold, no neural net needed for v1. See `NEXT_STEPS.md`.
- **Localisation "how":** *(pending Johannes' upload — confirm method before claiming it as a differentiator; see open question below.)*

### Figure index
| slide | figure |
|---|---|
| 2 | detector-scorecard.png |
| 3 | detector-fpr-fnr-tradeoff.png |
| 4 | audio-annotation-impact.png |
| 5 | feedback-loop-concept.png |

### Open question before the pitch
Confirm with Johannes which method drives the localisation (the repo's committed contract
says IMU dead-reckoning + magnetometer heading; he described video wheel-counting). Only
claim the "how" we can substantiate. Producer code is being uploaded.
