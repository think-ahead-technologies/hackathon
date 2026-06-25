# CM55 — BMI270 + BMM350 Motion Studio (WebSerial)

The CM55 core reads the on-board **Bosch BMI270** 6-axis IMU (SCB0 I2C, P8_0/P8_1)
and **Bosch BMM350** 3-axis magnetometer (**I3C** controller, P3_0/P3_1 — a
separate peripheral, not the IMU's I2C bus) and streams samples to the browser
UI over the KitProg3 USB serial port.

UI: [`web_streaming/bmi270_web_streaming.html`](web_streaming/bmi270_web_streaming.html)
(open in Chrome/Edge, 115200 baud, click *Connect*).

## Why the magnetometer

The orientation cube's roll and pitch are corrected by the accelerometer
(gravity is an absolute "down" reference), but **yaw** has no such reference on a
6-axis IMU — gravity is unchanged by rotation about the vertical axis — so
gyro-only yaw integrates its bias and drifts. The BMM350 supplies an absolute
**heading** (Earth's magnetic field). The web UI tilt-compensates the field with
roll/pitch and fuses the resulting heading into yaw with a complementary filter,
turning unbounded drift into bounded magnetic noise.

Calibrate before relying on heading: click **Calibrate Mag**, slowly rotate the
board through all orientations (figure-8), then **Finish**. Hard/soft-iron
offsets are stored in the browser (localStorage). Magnetometers are disturbed by
nearby metal/motors/cables — keep the board clear of them.

## Source layout

| Path | Purpose |
|------|---------|
| `main.c` | FreeRTOS stream task: poll browser commands, read IMU+mag, send frames |
| `bmi270/sensor_i2c.{c,h}` | Shared owner of the sensor I2C bus (init + read/write) |
| `bmi270/imu_app.{c,h}` | BMI270 bring-up / config / read (synthetic fallback) |
| `bmi270/uart_stream.{c,h}` | Binary frame encoder + command parser |
| `bmi270/sensorapi/` | Vendored Bosch BMI270 SensorAPI |
| `bmm350/mag_app.{c,h}` | I3C bring-up + BMM350 read (synthetic fallback) |
| `bmm350/mtb_bmm350.{c,h}` | Vendored Infineon I3C BMM350 helper |
| `bmm350/sensorapi/` | Vendored Bosch BMM350 SensorAPI |

Both drivers fall back to a synthetic waveform if the sensor or its SensorAPI is
absent, so the link is demonstrable without hardware.

## Wire format

Little-endian binary frame, one per sample (see `uart_stream.h` for the full
spec). The IMU payload is **24 bytes**:

```
int32 t_us · int16 acc[3] · int16 gyr[3] · int16 temp · int16 mag[3]
```

`mag` is in 1/16 µT per count (`µT = raw / 16`). The magnetometer fields were
appended after `temp`, so the earlier offsets are unchanged — an older 18-byte
decoder still reads acc/gyr/temp, and the web UI falls back to gyro-only yaw if
mag is absent.

For documentation related to the overall example, see the [top-level README](../README.md).
