# Topic

`magnetometer-data` — `data_classification: "derived"`, subject
`edge.mag.<line>.<container>` (namespace `edge.>`).

Derived from [`raw`](../raw/contract.md): the magnetometer triplet (`mag[3]`)
of each IMU record (type `0x10`) split out and scaled from raw counts to µT.
Accel/gyro/temp egress on [`imu-data`](../imu-data/contract.md).

## Description

One NATS message per magnetometer sample. JSON, µT.

```json
{
  "t_us": 123456789,
  "t_host_us": 1750000000000000,
  "mag_ut": [12.34, -45.67, 8.90]
}
```

| Field       | Type   | Unit    | Notes                                    |
|-------------|--------|---------|------------------------------------------|
| `t_us`      | i64    | µs      | Device-side timestamp (per frame).       |
| `t_host_us` | u64    | unix µs | Host receive wall clock.                 |
| `mag_ut`    | f64[3] | µT      | x, y, z. `raw / 256.0`.                  |

Source: BMM350. Carried in the IMU wire payload `<i3h3hh3h` (24 B) — the
trailing `i16 mag[3]`.

## Additional Information

- **Scaling.** Fixed: `mag_ut = raw / 256.0`. No `CFG` dependency (unlike
  accel/gyro range in [`imu-data`](../imu-data/contract.md)).
- **Legacy frames carry no mag.** 18 B IMU payloads predate the BMM350 — no
  message emitted (raw `mag` would be zeros).
- **Lossy by design.** Only the mag triplet, converted to float. For byte-exact
  frames use [`raw`](../raw/contract.md).
- **Timestamps.** `t_us` = device µs (per frame, not wall clock).
  `t_host_us` = host unix µs receive time. Align cross-topic on `t_host_us`;
  `t_us` shares the same clock as `imu-data` (same source frame).
- **Rate.** BMM350 ODR, typically below accel/gyro. Derive dt from `t_us`.
- **Lifecycle.** Tracks `raw`: starts on `S`, ends on `Q` / consumer disconnect /
  shutdown.
