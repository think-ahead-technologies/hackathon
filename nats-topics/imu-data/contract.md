# Topic

`imu-data` — `data_classification: "derived"`, subject
`edge.imu.<line>.<container>` (namespace `edge.>`).

Derived from [`raw`](../raw/contract.md): the IMU records (type `0x10`) split
out of the device frame stream and scaled from raw ADC counts to engineering
units (g / dps / °C). Accelerometer + gyroscope + die temperature only — the
magnetometer triplet egresses on [`magnetometer-data`](../magnetometer-data/contract.md).

## Description

One NATS message per IMU sample. JSON, SI-ish engineering units.

```json
{
  "t_us": 123456789,
  "t_host_us": 1750000000000000,
  "acc_g":  [0.0123, -0.0041, 0.9987],
  "gyr_dps": [0.12, -0.03, 0.01],
  "temp_c": 24.5,
  "acc_range_g": 4,
  "gyr_range_dps": 2000
}
```

| Field           | Type      | Unit      | Notes                                              |
|-----------------|-----------|-----------|----------------------------------------------------|
| `t_us`          | i64       | µs        | Device-side timestamp (per frame).                 |
| `t_host_us`     | u64       | unix µs   | Host receive wall clock.                           |
| `acc_g`         | f64[3]    | g         | x, y, z. `raw / 32768 * acc_range_g`.              |
| `gyr_dps`       | f64[3]    | dps       | x, y, z. `raw / 32768 * gyr_range_dps`.            |
| `temp_c`        | f64       | °C        | Die temp. `23.0 + temp_raw / 512.0`.               |
| `acc_range_g`   | int       | g         | Full-scale range applied (from latest `CFG`).      |
| `gyr_range_dps` | int       | dps       | Full-scale range applied (from latest `CFG`).      |

Source: BMI270. Wire payload `<i3h3hh3h` (24 B; legacy 18 B drops mag).

## Additional Information

- **Scaling.** Counts → units use the most recent `CFG` (CMD record `0x02`,
  e.g. `CFG,100,4,200,2000,normal` → acc_range=4, gyr_range=2000). Fallback
  defaults: ±4 g / ±2000 dps. Temp fixed: `23.0 + temp_raw/512.0`.
- **Lossy by design.** Unlike [`raw`](../raw/contract.md), only IMU samples are
  emitted and values are converted (float). For byte-exact frames use `raw`.
- **Timestamps.** `t_us` = device µs (monotonic per stream, not wall clock).
  `t_host_us` = host unix µs receive time. Use `t_host_us` to align across
  topics; `t_us` for inter-sample dt.
- **Rate.** Per `CFG` accel/gyro ODR (e.g. 100/200 Hz). Not guaranteed uniform —
  derive dt from `t_us`.
- **Lifecycle.** Tracks `raw`: starts on `S`, ends on `Q` / consumer disconnect /
  shutdown.
