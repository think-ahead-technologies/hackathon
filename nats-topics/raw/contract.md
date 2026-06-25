# Topic

`raw` — `data_classification: "raw"`, subject `edge.raw.<line>.<container>`
(namespace `edge.>`). Device-internal data plane; blocked at the Vector
boundary (never egresses).

The unsplit, lossless device frame stream. Every CRC-valid frame the PSOC™ Edge
kit emits (IMU, status, camera, audio) plus host metadata/commands — byte-for-byte
as received, stamped with the host wall clock. Source of truth for all derived
topics (`imu-data`, `magnetometer-data`, `camera-data`, `positinal-data`).

Authoritative format: `binary/README.md` (IMULOG01) and the reference
implementation `binary/imu_log.py`.

## Description

One NATS message per log record. All values **little-endian**.

Each message carries one record. Record header (11 B) followed by payload:

```
Record header:  u8 type | u64 t_host_us (unix µs) | u16 len
Payload:        u8[len]  (verbatim device frame, CRC already verified)
```

Stream-level metadata (sent once at stream open, equivalent to the IMULOG01
file header — magic `IMULOG01`, `u64 t_start_us`, `u32 baud`) is delivered as a
META record (type `0x01`).

### Record types

| Type | Name   | Payload                                                                                  |
|------|--------|------------------------------------------------------------------------------------------|
| 0x01 | META   | UTF-8 JSON: `port`, `baud`, last known sensor config                                     |
| 0x02 | CMD    | ASCII command sent to the device (`S`, `Q`, `CFG,…`)                                      |
| 0x10 | IMU    | 24 B: `i32 t_us, i16 acc[3], i16 gyr[3], i16 temp_raw, i16 mag[3]` (legacy: 18 B, no mag) |
| 0x20 | STATUS | `u8 imu_src, u8 mag_src`, reason text                                                     |
| 0x30 | CAMERA | `u32 frame_id, u16 w, u16 h`, JPEG bitstream (Wi-Fi only)                                 |
| 0x40 | AUDIO  | `u32 seq, u16 rate, u8 channels, u8 bits`, PCM                                            |

IMU/STATUS/CAMERA type values mirror the wire frame types (`uart_stream.h`).

### Struct formats (Python `struct`, little-endian)

- IMU payload: `<i3h3hh3h` (24 B). Legacy min: 18 B (no magnetometer).
- Camera header: `<IHH` (8 B) + JPEG bytes.
- Audio header: `<IHBB` (8 B) + PCM bytes.

## Additional Information

- **Lossless.** Payloads stored byte-for-byte; only host receive time added.
  Corrupted frames are never published — CRC validated upstream.
- **Raw counts, not engineering units.** IMU values are i16 ADC counts. Scale to
  g / dps / °C / µT downstream using the most recent `CFG` (CMD record).
  Fallback: ±4 g / ±2000 dps (UI/firmware defaults).
- **Timestamps.** `t_us` = device-side µs (per frame). `t_host_us` = host unix µs
  receive time. `t_start_us` (META) = stream open, host unix µs.
- **Camera/audio = Wi-Fi only** (TCP 5000); UART (115200) lacks bandwidth.
- **Lifecycle.** Stream opens on `S` (start), closes on `Q` (stop) / last
  consumer disconnect / shutdown.
