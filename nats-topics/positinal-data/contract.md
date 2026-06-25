# Topic

`positinal-data` — `data_classification: "derived"`, subject
`edge.position.<line>.<container>` (namespace `edge.>`).

Derived from [`imu-data`](../imu-data/contract.md) (+ optional
[`magnetometer-data`](../magnetometer-data/contract.md) for heading): sensor
fusion / dead-reckoning output mapping each timeframe to a place on the floor
map — which `segment` the unit is in and its `x`/`y` map coordinate.

## Description

One NATS message per resolved position fix. JSON.

```json
{
  "t_us": 123456789,
  "t_host_us": 1750000000000000,
  "segment": "line-3.station-B",
  "x": 12.45,
  "y": 7.80
}
```

| Field       | Type   | Unit    | Notes                                             |
|-------------|--------|---------|---------------------------------------------------|
| `t_us`      | i64    | µs      | Device timestamp of the source sample window end. |
| `t_host_us` | u64    | unix µs | Host wall clock of the fix.                       |
| `segment`   | string | —       | Map segment / zone id the unit resolves into.     |
| `x`         | f64    | m       | Map-frame X (origin = map top-left, +X right).    |
| `y`         | f64    | m       | Map-frame Y (origin = map top-left, +Y down).     |

`segment` + `(x, y)` define the position: the segment is the coarse zone, the
coordinate the fine location within the shared map frame.

## Additional Information

- **Fusion output, not a raw sensor.** Computed downstream from
  [`imu-data`](../imu-data/contract.md) (acc/gyro integration) with magnetometer
  heading correction. Subject to drift — treat as estimate, not ground truth.
- **Map frame.** `x`/`y` in meters in one fixed map coordinate system shared by
  all units. `segment` is the named zone for that coordinate.
- **Rate.** One fix per fusion window — lower and more uniform than the IMU
  sample rate. Derive dt from `t_us`.
- **Timestamps.** `t_us` = device µs of the source window. `t_host_us` = host
  unix µs. Align cross-topic on `t_host_us`.
- **Lifecycle.** Tracks the upstream stream: starts on `S`, ends on `Q` /
  consumer disconnect / shutdown.
