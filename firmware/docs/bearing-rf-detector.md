# Bearing RandomForest Detector

This document explains the generated C RandomForest detector in `firmware/src/bearing_rf.c` and the intended board integration path for live sensor windows, console logs, and Contract B publishing over NATS.

The detector is the C export of the bearing-fault RandomForest from the Python analysis pipeline. It does not train on the board. Training happens offline in `analysis/export_bearing_rf_c.py`, which regenerates the static model tables in `firmware/src/bearing_rf.c` and the public API in `firmware/include/bearing_rf.h`.

## Scope

The current C RandomForest module performs this stage only:

```text
10 bearing features -> RandomForest score -> OK / FAULT
```

It does not currently compute the 10 features from raw IMU samples. On the board, the integration layer must collect a 1 second sensor window, compute the feature vector in the same order as the Python analysis, and then call the C detector.

The existing `firmware/src/wear_fault.c` is a separate conservative baseline-ratio detector. It is useful as an explainable fallback. The RandomForest detector is the closer port of the Python `analysis` accuracy path.

## Generated Model

The exported model is intentionally compact.

| Item | Value |
|---|---:|
| Source script | `analysis/export_bearing_rf_c.py` |
| C implementation | `firmware/src/bearing_rf.c` |
| Public header | `firmware/include/bearing_rf.h` |
| Feature count | 10 |
| Trees | 150 |
| Max tree depth | 10 |
| Total nodes | 22382 |
| Local RF threshold | `0.20974355` |
| Model size in source form | about 855 KB |

Why not export the original unlimited forest? The full `300` tree, unlimited-depth RF has about `328496` nodes. The compact `150` tree, max-depth-10 forest preserved the same validation level in our check while being much more realistic for firmware flash.

Validation result from the C/Python parity test:

```text
make bearing-rf-cross CC='python -m ziglang cc'
bearing-rf cross-check: cases=310 threshold=0.20974355 max_abs_score_diff=2.96e-08 mismatches=0
```

## C API

Include:

```c
#include "bearing_rf.h"
```

Constants:

```c
#define BEARING_RF_FEATURE_COUNT 10
#define BEARING_RF_TREE_COUNT 150
#define BEARING_RF_NODE_COUNT 22382
#define BEARING_RF_THRESHOLD 0.20974355f
```

Types:

```c
typedef enum {
    BEARING_RF_STATUS_OK = 0,
    BEARING_RF_STATUS_FAULT = 1,
    BEARING_RF_STATUS_INVALID_INPUT = 2,
} bearing_rf_status_t;

typedef struct {
    bearing_rf_status_t status;
    float fault_percent;
    float score;
} bearing_rf_result_t;
```

Functions:

```c
float bearing_rf_score(const float features[BEARING_RF_FEATURE_COUNT]);

bearing_rf_result_t bearing_rf_detect_features(
    const float features[BEARING_RF_FEATURE_COUNT]
);
```

### Inputs

`bearing_rf_score()` and `bearing_rf_detect_features()` accept exactly one 10-element feature vector. These are not raw accelerometer samples. The feature order must match `analysis/features.py`:

| Index | Feature | Meaning |
|---:|---|---|
| 0 | `acc_hp_rms` | RMS of high-pass accelerometer magnitude above 5 Hz |
| 1 | `acc_hp_p2p` | Peak-to-peak high-pass accelerometer magnitude |
| 2 | `acc_crest` | Peak divided by RMS for high-pass accelerometer magnitude |
| 3 | `acc_kurt` | Kurtosis of high-pass accelerometer magnitude |
| 4 | `acc_band_5_10` | FFT energy fraction in 5-10 Hz |
| 5 | `acc_band_10_15` | FFT energy fraction in 10-15 Hz |
| 6 | `acc_band_15_25` | FFT energy fraction in 15-25 Hz |
| 7 | `acc_centroid` | Spectral centroid of high-pass accelerometer magnitude |
| 8 | `gyro_rms` | RMS of gyroscope vector magnitude, used to separate turns from fault vibration |
| 9 | `acc_lf_rms` | Low-frequency accelerometer magnitude variation below 2 Hz |

If any feature is non-finite, `bearing_rf_score()` returns `-1.0f`, and `bearing_rf_detect_features()` returns `BEARING_RF_STATUS_INVALID_INPUT`.

### Outputs

`bearing_rf_score()` returns the mean class-1 probability across all trees:

```text
score = average(tree_fault_probability)
```

`bearing_rf_detect_features()` returns:

| Field | Meaning |
|---|---|
| `status` | `FAULT` when `score >= BEARING_RF_THRESHOLD`, otherwise `OK` |
| `score` | Raw RF score in `[0, 1]` for valid input |
| `fault_percent` | Display-friendly percentage, currently `score * 100`, clamped at 100 |

Important: the local RF threshold is `0.20974355`, not `0.60`. The bridge's default Contract B alert threshold is `0.60`, so the board should not publish the raw RF score directly as `anomaly_score` unless the bridge threshold is also changed.

## How The C Inference Works

`firmware/src/bearing_rf.c` is generated code with static arrays:

- `TREE_ROOTS[]`: first node index for each tree.
- `NODE_FEATURE[]`: feature index to compare at each node.
- `NODE_THRESHOLD[]`: threshold value for that node.
- `NODE_LEFT[]` and `NODE_RIGHT[]`: next node index for the true/false branch.
- `NODE_PROBABILITY[]`: leaf probability for class `fault`.

For each tree:

1. Start at that tree's root node.
2. If the node is internal, compare `features[NODE_FEATURE[node]] <= NODE_THRESHOLD[node]`.
3. Move to the left or right child.
4. Repeat until a leaf is reached.
5. Add the leaf's fault probability to the ensemble sum.

The final score is the average leaf probability over all `150` trees.

There is no heap allocation in the inference path. The model arrays are `static const` and should live in flash/ROM. Runtime memory is only a few scalar loop variables.

## Board Integration Overview

The board integration should run a simple rolling-window loop:

```text
boot
connect Wi-Fi
connect NATS
repeat forever:
    sample connected sensors
    keep only the last 1 second of numeric sensor samples
    when a 1 second analysis window is ready:
        compute bearing RF features
        call bearing_rf_detect_features(features)
        write debug logs for every stage
        publish Contract B over NATS
        drop samples older than the rolling window
        reply PONG to any NATS PING
```

Audio and video are out of scope for this integration. Do not store audio or video payloads. Keep only numeric samples from the connected sensors required for the 1 second feature window. After each pass, remove samples older than the active window.

## Sensor Window Requirements

Use a ring buffer per sensor stream.

Recommended state:

```c
typedef struct {
    uint64_t t_us;
    float ax, ay, az;
    float gx, gy, gz;
    // Optional extra numeric sensors can be stored here if needed for logging
    // or future features, but the current bearing RF uses only accel + gyro.
} sensor_sample_t;
```

Window policy:

- Window length: `1.0 s`.
- Hop: implementation choice. For analysis parity, use `0.5 s` hop when possible. For a simpler first board demo, one pass per second is acceptable.
- Keep only samples where `now_us - sample.t_us <= 1000000`.
- Do not persist raw windows after analysis.
- Do not store audio/video frames.

Pseudocode:

```c
for (;;) {
    sensor_sample_t sample = read_sensors();
    ring_push(&window, sample);
    ring_drop_older_than(&window, sample.t_us - 1000000ULL);

    nats_service_ping_pong();

    if (!window_ready(&window, 1000000ULL)) {
        log_stage("WINDOW_COLLECT", "samples=%u", window.count);
        continue;
    }

    float features[BEARING_RF_FEATURE_COUNT];
    if (!extract_bearing_features_1s(&window, features)) {
        log_stage("FEATURE_ERROR", "samples=%u", window.count);
        continue;
    }

    bearing_rf_result_t result = bearing_rf_detect_features(features);
    log_stage("RF_SCORE", "score=%.6f threshold=%.6f status=%d",
              result.score, BEARING_RF_THRESHOLD, result.status);

    publish_bearing_contract_b(result, features);
    log_stage("WINDOW_DROP", "kept_samples=%u", window.count);
}
```

## Feature Extraction On The Board

The RF model expects the exact analysis feature semantics. The Python reference uses `analysis/features.py`:

- high-pass Butterworth filter above 5 Hz on accelerometer magnitude;
- low-pass Butterworth filter below 2 Hz on accelerometer magnitude;
- 1 second windows;
- FFT band fractions over the high-pass signal;
- gyroscope magnitude RMS.

The current C RF module does not implement this raw-sample feature extractor yet. The integration layer must provide `extract_bearing_features_1s()` and validate it against Python before relying on live results.

Recommended implementation path:

1. Implement a board-side feature extractor that outputs the 10 features in the exact order above.
2. Add a C harness that accepts raw 1 second IMU windows and prints the 10 features.
3. Add a Python cross-test against `analysis/features.extract_windows` on real `IMU-Data.data` windows.
4. Only then connect `extract_bearing_features_1s()` to `bearing_rf_detect_features()`.

Note: Python uses `filtfilt`, which is non-causal and uses future samples inside a recording. The board cannot use future samples in a live stream. For firmware, use a causal IIR/filter-state equivalent or a short-window approximation, then validate the resulting detector behavior on recorded sessions.

## NATS Publishing

Use the Contract B wire protocol documented in `docs/device-nats.md`.

Publish to:

```text
edge.<line>.<container_id>
```

Example subject:

```text
edge.line1.cnc-7
```

Do not publish directly to `inference.*`; that bypasses the Vector trust boundary.

The existing firmware helper `nats_build_pub()` builds this frame:

```text
PUB <subject> <payload-byte-count>\r\n<payload>\r\n
```

The NATS connection must also handle server keepalive:

- read server `INFO` first;
- send `CONNECT {"verbose":false,...}\r\n`;
- answer every inbound `PING\r\n` with `PONG\r\n`.

## Contract B Score Mapping

The bridge opens an alert at:

```text
anomaly_score >= 0.60
```

The local RF fault threshold is:

```text
BEARING_RF_THRESHOLD = 0.20974355
```

To keep board-side `FAULT` aligned with bridge alerts without changing the bridge config, publish a normalized wire score where the RF threshold maps to `0.60`:

```c
static float bearing_rf_wire_score(float raw_score) {
    float s = raw_score * (0.60f / BEARING_RF_THRESHOLD);
    if (s < 0.0f) return 0.0f;
    if (s > 1.0f) return 1.0f;
    return s;
}
```

Then:

```c
float anomaly_score = bearing_rf_wire_score(result.score);
```

This means:

- local `OK` stays below the bridge alert threshold in normal cases;
- local `FAULT` reaches at least `0.60` and opens an alert;
- dashboards still see a normalized `0..1` anomaly score.

If the bridge `THRESHOLD` environment variable is changed to `0.20974355`, then the board may publish raw `result.score` directly. Keep one threshold convention only; do not mix both.

Recommended payload:

```json
{
  "ts": "2026-06-25T12:00:00Z",
  "container_id": "cnc-7",
  "model_version": "bearing-rf@2026.06.25-depth10",
  "anomaly_score": 0.73,
  "fault_class": "bearing_fault",
  "location": "line1",
  "data_classification": "inference",
  "bytes": 196
}
```

Set `fault_class` to `"bearing_fault"` when `result.status == BEARING_RF_STATUS_FAULT`; otherwise use `null`.

## Console And Debug Logs

Log every stage with a stable prefix so board output can be followed during demos and field debugging.

Recommended format:

```text
[bearing_rf] stage=<STAGE> key=value key=value
```

Stages:

| Stage | When | Suggested fields |
|---|---|---|
| `BOOT` | firmware starts detector task | `model_version`, `trees`, `nodes`, `threshold` |
| `NATS_CONNECT_START` | TCP/TLS connect starts | `host`, `port`, `subject` |
| `NATS_CONNECT_OK` | CONNECT sent and accepted | `auth=anonymous` or `auth=nkey` |
| `NATS_CONNECT_ERROR` | connect/auth fails | `code`, `reason` |
| `SAMPLE_READ` | one sensor sample read | `t_us`, `sample_count` |
| `WINDOW_COLLECT` | window not full yet | `sample_count`, `age_ms` |
| `WINDOW_READY` | 1 second window is ready | `sample_count`, `window_ms` |
| `FEATURE_START` | feature extraction begins | `sample_count`, `fs_hz` |
| `FEATURE_OK` | feature vector built | selected feature values or `feature_count=10` |
| `FEATURE_ERROR` | feature extraction fails | `reason` |
| `RF_SCORE` | detector has a score | `raw_score`, `wire_score`, `threshold`, `status` |
| `DECISION_OK` | local verdict is OK | `raw_score` |
| `DECISION_FAULT` | local verdict is FAULT | `raw_score`, `fault_class` |
| `NATS_PUB_START` | publish frame is being sent | `subject`, `payload_bytes` |
| `NATS_PUB_OK` | publish completed | `seq`, `payload_bytes` |
| `NATS_PUB_ERROR` | publish failed | `code`, `reason` |
| `NATS_PING` | server PING received | none |
| `NATS_PONG` | PONG sent | none |
| `WINDOW_DROP` | old samples are removed | `dropped`, `kept` |

Example sequence:

```text
[bearing_rf] stage=BOOT model_version=bearing-rf@2026.06.25-depth10 trees=150 nodes=22382 threshold=0.209744
[bearing_rf] stage=NATS_CONNECT_START host=192.168.1.12 port=4222 subject=edge.line1.cnc-7
[bearing_rf] stage=NATS_CONNECT_OK auth=anonymous
[bearing_rf] stage=WINDOW_READY sample_count=50 window_ms=1000
[bearing_rf] stage=FEATURE_OK feature_count=10 acc_hp_rms=0.0182 gyro_rms=4.91
[bearing_rf] stage=RF_SCORE raw_score=0.243100 wire_score=0.695449 threshold=0.209744 status=1
[bearing_rf] stage=DECISION_FAULT fault_class=bearing_fault
[bearing_rf] stage=NATS_PUB_OK seq=42 payload_bytes=196
[bearing_rf] stage=WINDOW_DROP dropped=25 kept=50
```

## Integration Checklist

1. Add `bearing_rf.c` and `bearing_rf.h` to the board build.
2. Keep the generated arrays in flash/ROM. Do not copy the model tables to RAM.
3. Implement a 1 second numeric sensor ring buffer.
4. Keep only the latest 1 second of samples; delete older samples after each pass.
5. Implement and validate `extract_bearing_features_1s()` against Python.
6. Call `bearing_rf_detect_features()` on each analysis pass.
7. Map raw RF score to Contract B `anomaly_score` using the bridge threshold convention.
8. Build Contract B JSON with `data_classification:"inference"`.
9. Publish to `edge.<line>.<container_id>` via `nats_build_pub()`.
10. Print stage logs for every major step.
11. Reply `PONG` to server `PING` while the loop runs.
12. Do not persist raw sensor windows, audio, or video.

## Regenerating The Model

Run from the repository root:

```powershell
python analysis\export_bearing_rf_c.py
```

Then verify:

```powershell
cd firmware
make bearing-rf-cross CC='python -m ziglang cc'
make test CC='python -m ziglang cc'
```

Expected current results:

```text
bearing-rf cross-check: cases=310 threshold=0.20974355 max_abs_score_diff=2.96e-08 mismatches=0
181 checks, 0 failures
```
