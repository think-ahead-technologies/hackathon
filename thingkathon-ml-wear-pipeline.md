# ML spec — acoustic-led wear detection with vibration corroboration

For the ML role to build from. Detects **debris-generating mechanical wear** and stages its
severity, with no bearing specs and no shaft RPM — purely from each unit's learned healthy
baseline. Acoustic is the detector; vibration corroborates; agreement between the two is the
confidence signal.

## Claim boundary (hold this line on stage)

We detect and stage the **wear process** that produces debris, corroborate it across two
independent senses, and — because the sensor rides the conveyor — **localize the anomaly to a
position along the track** and distinguish a **track fault from an onboard (vehicle) fault**.
We do **not** size, count, or identify particles, we do **not** name the faulting sub-component
(no defect-frequency localization — we lack the geometry/RPM to compute it honestly), and track
position is resolved to bin/segment granularity, not absolute metric coordinates. Output is
"debris-generating wear, detected, localized on the track, and severity-staged."

## Why baseline-anomaly, not a physics model

No datasheet specs is a feature, not a gap: a system that needs every machine's geometry doesn't
scale across a real factory of mixed-age equipment. We learn each unit's **own** healthy signature
and watch for drift. Train on healthy data only — no fault labels required to get a working detector.

---

## 0 · Data reality check (from first board recordings)

Reconciling this spec against the first recordings off the board. Three things diverge from the
plan and must be resolved before building:

1. **IMU is recorded at 50 Hz, not kHz.** Δt = 0.02 s → Nyquist 25 Hz. The §3 vibration pipeline
   (impact kurtosis/crest, FFT bands, Hilbert envelope, periodic-impact energy) assumes
   hundreds-of-Hz-to-kHz content and is **not observable at 50 Hz**. Either raise the ODR (likely a
   default config, not a hardware limit — ask the Infineon coaches first) or reframe IMU as a
   low-frequency motion/instability detector (wobble, run-out, imbalance), not a vibration-spectral
   wear detector. **This blocks the vibration physics as written.**
2. **Detector/corroborator roles are inverted vs. the data we have.** Every *fault* recording so far
   is IMU + magnetometer only; every *audio* recording is *healthy* (and one mic died mid-session).
   So today the acoustic autoencoder has zero positive examples and **IMU is the de-facto detector**.
   Build phase 1 on IMU. The acoustic-led framing below returns once fault-session audio exists.
3. **A magnetometer (3-axis, 50 Hz) is present and unspecced** — free heading signal for turn/lap-phase
   localization (§6) and an onboard-rotation cross-check.

Useful confirmations: audio is **16 kHz mono** (matches §1); supervised labels **do exist**
(`fault`, `normal`, `qs_smooth`, `qs_wobbly`, `turn_table`) so §5 is not label-starved — the
kickstart's own model is a supervised conv1d-LSTM on IMU; and there's a real **severity ladder**
(2 → 4 → 8 defective bearings) plus a labeled disturbance (`powerbank_dropped`) for false-positive
testing. Caveat: the ladder is discrete across separate sessions, not a within-run trend.

### 0.1 · What the 50 Hz IMU actually shows (first separability analysis)

Measured on the labeled `fault` vs `normal` windows (1 s, 50% overlap), with a within-session control
and the `turn_table`/severity-ladder sessions. Three findings that constrain the build:

1. **Detection works on energy, not impulsiveness.** Faulty bearings lift broadband accel energy and
   jerk ~2× (`accel_rms`, `accel_p2p`, and mean-abs-successive-difference as a hi-freq proxy —
   single-feature AUC ≈ 0.80 pooled, ≈ 0.64–0.73 within a single session). The spec's headline
   early-wear features `crest` and `kurtosis` sit at AUC ≈ 0.55 — **no separation at 50 Hz**, exactly
   as Nyquist=25 Hz predicts. → §3's impact/impulsiveness features stay blocked; build the detector on
   *amplitude/energy*, expect a multi-feature model (single feature won't carry it), and lean hard on
   per-unit baselining (§4) — part of the raw gap is cross-session operating-point drift, not the bearing.
2. **Severity does not grade by amplitude.** The 2 → 4 → 8 ladder shows a clear step healthy→2
   (~1.5×) then **saturates flat (2 ≈ 4 ≈ 8)**. The IMU detects fault *presence*, not fault *count*.
   → §5 severity staging cannot rely on an amplitude dose-response from this data; grade severity from
   trend/persistence over time, or from acoustic once fault audio exists.
3. **Gyro is a turn detector, not a fault feature.** During `turn_table`, `gyro_rms` spikes ~30× vs.
   healthy (AUC turn-vs-normal ≈ 0.98) while accel energy *drops*. Faults do the opposite (accel ↑,
   gyro ~normal). → **gate gyro out of the detector** or every curve is a false positive; use the
   accel-↑/gyro-↑ split as a clean fault-vs-turn discriminator, and reuse gyro as the turn/landmark
   signal for localization (§6).

---

## 1 · Signal acquisition (confirm/tune to the BSP)

**Acoustic (PDM mics) — primary.** Decimate PDM to PCM at **16 kHz** (32 kHz if the mic + decimation
chain allow it — more high-frequency grinding content). Frame at **1024 samples, 50% overlap**
(~32 ms frames). Aggregate frames into a **1 s analysis window** → one feature set per second.

**Vibration (6-axis IMU) — corroborator (currently the de-facto detector — see §0).** 3-axis
accelerometer at the **highest stable ODR** (target 1–3 kHz). **Recorded data is 50 Hz** — confirm
whether the BSP can go higher; at 50 Hz the spectral wear features in §3 are not viable (§0).
Same **1 s window**. Use per-axis and the vector magnitude.

**Magnetometer (3-axis, 50 Hz) — present in recordings, not originally specced.** Use for heading /
turn detection (feeds lap-phase localization in §6) and as an onboard-rotation cross-check.

Both windows are time-aligned so each second produces one acoustic score and one vibration score.

## 2 · Acoustic pipeline (detection)

Per frame → **40 log-mel band energies** (compact and Ethos-U55-friendly), assembled into a
~`40 × 31` log-mel patch per 1 s window. Also compute these scalar features per window — they're
the debris-specific ones and double as interpretable dashboard signals:

- high-frequency energy ratio (HF band / total) — grinding/grit pushes energy up
- spectral flatness — broadband noise from abrasion raises it
- spectral centroid + rolloff — brightness shifts with grit
- crest factor + kurtosis — impulsiveness from particle impacts
- RMS, spectral entropy

**Model:** small **convolutional autoencoder** on the log-mel patch, trained on **healthy audio only**.
Anomaly = reconstruction error. Quantize int8 for the NPU.

```
acoustic_score A  =  normalize( reconstruction_error )     # see §4
```

## 3 · Vibration pipeline (corroboration, no named frequencies)

> **Blocked at 50 Hz (§0).** Everything below assumes ≥1 kHz ODR. At the recorded 50 Hz the
> impact/envelope/high-band features are not observable; either raise the ODR or drop to
> low-frequency motion features only (RMS, peak-to-peak, low-band energy, gross instability).

Per 1 s window, per axis + magnitude, extract broadband wear indicators that need no specs:

- **kurtosis** and **crest factor** — the headline early-wear / impulsiveness indicators
- RMS, peak, peak-to-peak
- band energies across 4–6 FFT bands; high-band energy trend
- **envelope features** (the technique, not the localization): band-pass a high band → Hilbert
  envelope → envelope RMS + envelope kurtosis + total periodic-impact energy. Report as
  "periodic impact energy rising," never as an element-specific defect frequency.

**Model:** either a second small autoencoder on the vibration feature vector, or a
**Mahalanobis distance** from the healthy feature distribution (cheaper, no training, fine for
a corroborator).

```
vibration_score V  =  normalize( recon_error  OR  mahalanobis_dist )   # see §4
```

## 4 · Normalization & per-unit baseline

Run the rig **healthy on-site** for as long as you can spare (capture ambient hall noise too —
a noisy venue is a real risk; the baseline must include it). From the healthy run, record the
distribution of each raw score and convert to a normalized 0–1 scale:

```
A = clip( (raw_A - mean_healthy_A) / k*std_healthy_A , 0, 1 )   # or percentile rank
A_thr = 95th–99th percentile of healthy A     # per channel, tunable
```

Store the baseline **per device** — that's the scalability story and the place device identity
(the BOB/ROSS work) anchors "this is unit 42's normal."

## 5 · Fusion & severity staging (the "assess" step)

Late (decision-level) fusion — keep A and V separate so you can *show agreement*. A small
rule-based state machine over the two thresholds + trend. Rule-based (not a classifier) is the
honest choice when stage labels are scarce, and it's transparent in Q&A.

> **Data note (§0):** labels actually exist (`fault`/`normal`/`qs_*`/`turn_table`), so a supervised
> stager is also on the table — the kickstart's own model is a supervised conv1d-LSTM on IMU.
> Calibrate stage thresholds against the real **2 → 4 → 8 bearing** severity ladder, but note it's
> discrete across separate sessions, not a within-run trend, so don't expect an in-run slope from it.

| Stage | Condition | Meaning |
|---|---|---|
| 0 · Healthy | A < A_thr **and** V < V_thr | nominal |
| 1 · Early / watch | A ≥ A_thr, V < V_thr | acoustic catches grit/grinding first |
| 2 · Established | A ≥ A_thr **and** V ≥ V_thr | both senses agree — high-confidence wear + debris |
| 3 · Advanced | stage 2 **and** rising trend (slope of A,V) or high kurtosis/crest | escalate |

Guards so it doesn't flap:
- **Hysteresis / dwell:** require N consecutive windows (e.g. 3–5 s) above threshold before escalating;
  require sustained drop before de-escalating.
- **Trend:** moving average + slope of A and V over the last 30–60 s feeds the stage-3 test.

```python
def stage(A, V, A_thr, V_thr, trend_rising):
    if A < A_thr and V < V_thr:           return 0
    if A >= A_thr and V <  V_thr:         return 1
    if A >= A_thr and V >= V_thr:
        return 3 if trend_rising else 2
    return 0   # V high alone is unusual; treat as noise / investigate
```

## 6 · Localize — moving-sensor position mapping

The sensor rides the conveyor, so localization is a *position-tracking* problem, not a
signal-decomposition one: every anomaly has a timestamp, and if you know where the unit was at
that time, you know where the fault is. Because the route repeats, the unit passes every point
many times — so you average many looks per location and the map sharpens each lap.

**Position reference (no track specs needed), in order of preference:**
- **External trigger** — a photo-eye or station signal giving a hard once-per-lap (or per-station)
  reset. Ask the Infineon coaches; this is a reasonable thing to request, unlike internal geometry.
- **IMU landmark map** — during the healthy baseline lap, record repeatable events (stops, transfers,
  curves, a characteristic bump) as an ordered landmark sequence = a coarse map of the loop. At
  runtime, map-match live IMU against it and **re-anchor at each landmark** to kill dead-reckoning drift.
- **Lap-phase autocorrelation** — recover the lap period `T` from the IMU envelope's periodicity;
  assign each window a phase `φ = (t mod T)/T ∈ [0,1)` = normalized position. Needs roughly constant
  speed (correct with the trigger if available).

**Position-indexed track-health map.** Discretize the loop into `P` position bins (or
landmark-bounded segments). For each 1 s window, tag it with its bin and its `A`/`V` scores, and
accumulate a running mean/percentile per bin across laps. A localized defect (bad roller, rough
rail, misaligned guide) shows as **persistent elevated anomaly at the same bin every lap**; noise
averages out. This is the cloud-side (ROSS) artifact — the track heatmap.

**Track fault vs onboard fault (the killer discriminator).** A roving sensor disambiguates a
moving source for free:
- anomaly **concentrated at specific bins**, low elsewhere → fault is **on the track** at that position;
- anomaly **uniform across all bins** → fault **travels with the unit** = the container's own running
  gear, not the track.
Metric: spatial contrast = `peak_bin / median_bin` (or variance across bins). High contrast →
track-localized; flat → onboard. State it on stage — no competitor will have it.

## 7 · Output (feeds Contract B)

```json
{ "ts": "...", "container_id": "42", "model_version": "...",
  "anomaly_score": 0.82,          // fused headline, e.g. max(A, V) or weighted
  "acoustic_score": 0.86, "vibration_score": 0.74,
  "severity_stage": 2,            // 0..3 — the "assess" output
  "track_position": 0.37,         // lap phase 0..1, or bin index / landmark-relative
  "fault_locus": "track",         // track | onboard | unknown
  "trend": "rising" }
```

`severity_stage` is what the dashboard colors; `track_position` + `fault_locus` drive the
track-health map and the "where" in the alert.

## 8 · On-device vs cloud split

- **On device:** feature extraction (FFT/mel on the M55 Helium DSP), both autoencoders on the
  Ethos-U55 NPU (int8), the stage state machine, and **position estimation** (landmark match /
  lap phase — all cheap, runs on the M33). Emits Contract B with position + locus.
- **Cloud (ROSS):** retrain the autoencoders on accumulated healthy + operator-labeled windows;
  re-issue thresholds; **accumulate the position-indexed track map across laps and compute the
  track-vs-onboard contrast.** Raw audio/vibration never leaves the device (Ring 0) — only scores
  + position cross the boundary.

## 9 · Build phases

1. **Acoustic detector only** — mel → conv autoencoder → normalized A → threshold. This alone is a
   working "it hears the wear" demo. Build on provided/recorded data first.
2. **Add vibration** — kurtosis/crest/RMS + Mahalanobis → V.
3. **Fuse + stage** — the state machine with hysteresis and trend; wire `severity_stage` into Contract B.
4. **Localize** — add lap-phase/landmark position; accumulate the per-bin track map; compute
   track-vs-onboard contrast. Even crude lap-phase is enough for a compelling heatmap.
5. **Close the loop** — operator label (Contract D) refines thresholds / seeds a later supervised stager.

## 10 · Risks & honesty

- **Ambient noise:** capture the healthy baseline in the actual hall; the autoencoder must learn the venue.
- **Mic reliability (observed, not hypothetical):** one recording shows the mic **failing mid-session**
  (`..._microphone_failed_after_5m`). A single dropout blinds an acoustic-primary detector — another
  reason to keep IMU as the always-on detector and treat audio as a high-resolution corroborator.
- **Mic/IMU mounting:** mount consistently (contact vs airborne changes everything); note placement.
- **Position drift:** dead-reckoning drifts — you *must* re-anchor at landmarks or a once-per-lap
  trigger, or the map smears. Confirm a cycle reference with the coaches early.
- **Don't overclaim:** detect + stage + corroborate + localize *position on the track* and track-vs-onboard.
  No particle sizing, no sub-component naming, no absolute coordinates.
- **Severity stage 1 is your best beat:** acoustic flags wear *before* vibration — show that lead time live.
