# localizer

Streaming port of [`localization_pipeline`](../localization_pipeline/) as a NATS service.
The offline tool runs over a whole recording (frame dir + IMU CSV); this runs the same detectors
incrementally on the live derived topics and publishes one position fix per advance.

## Topics

| Direction | Subject | Contract |
|---|---|---|
| in  | `edge.camera.<line>.<container>` | [camera-data](../nats-topics/camera-data/contract.md) — wheel counter |
| in  | `edge.imu.<line>.<container>`    | [imu-data](../nats-topics/imu-data/contract.md) — gyro anchors + motion-stop |
| in  | `edge.mag.<line>.<container>`    | [magnetometer-data](../nats-topics/magnetometer-data/contract.md) — missed-turn recovery |
| out | `edge.position.<line>.<container>` | [positinal-data](../nats-topics/positinal-data/contract.md) |

## How it maps to the pipeline

| pipeline.py (batch) | localizer (streaming) |
|---|---|
| `detect_wheels` (dark&moving ROI run-gate, peak frame) | `WheelDetector` — frame-by-frame run state machine |
| `track.detect_turntables` (gyro ~90° anchors) | `AnchorDetector` — incremental yaw integration |
| `recover_missed_turns` (mag heading step in IMU gaps) | `MagRecovery` — rolling hard-iron fit, deferred gap eval |
| `motion_state` (vibration energy + hysteresis + debounce) | `MotionState` — rolling window, causal debounce |
| leg-pin + cap-and-hold reconstruction, `cum_to_xy` | `Position` — snap-at-anchor, `cum_to_xy` verbatim |

A fix is emitted on each accepted wheel (+15 cm; rejected while IMU-stationary) and on each
turntable anchor (snap to the corner). `x`/`y` are in **metres** per the contract (pipeline cm / 100).

## Config (env)

- `START_TT` — first turntable the box reaches (`BR`/`BL`/`TL`/`TR`). Set → absolute, corner-locked
  position; empty → relative cumulative distance only.
- `ROI_FILE` — ROI polygon (default bundled `roi.json`, scaled to the actual frame size).
- `D0` / `NETTHR` / `REL_K` — wheel detector gates (defaults match the validated run).
- `NATS_URL`, `NATS_NKEY_SEED`, `NATS_CA_FILE`, `NATS_TLS_HOSTNAME` — same auth knobs as the splitter.

## Test

```
pip install -r requirements.txt
pytest test_localizer.py
```
