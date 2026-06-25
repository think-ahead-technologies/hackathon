# Host bridge server — device → binary log → browser

Sits between the PSOC™ Edge kit and the `bmi270_web_streaming.html` frontend,
recording every CRC-valid sensor frame to a binary log file before relaying
the stream to the browser. Two device links carry the same protocol:

```
USB:    PSOC Edge ──UART 115200 (KitProg3)──┐
Wi-Fi:  PSOC Edge SoftAP ──TCP 5000─────────┤
   BMI270 + BMM350 + USB webcam (JPEG)      ▼
   binary frames (magic+CRC)          imu_server.py ──WebSocket──> browser
                                            │     (live plots, 3D cube, camera)
                                            ▼
                              logs/imu_YYYYmmdd_HHMMSS_mmm.bin
                              (IMULOG01, host-timestamped)
```

Camera: a UVC webcam on the kit's USB-C host port is captured on the CM55,
JPEG-encoded, and streamed as type-0x30 frames (Wi-Fi only — the UART lacks
bandwidth). The browser shows a live preview whenever connected; frames are
recorded to the log while a recording is active. Export them with
`python imu_log.py extract <log>.bin`.

For Wi-Fi, the board hosts its own network (SoftAP, see
`proj_cm33_ns/wifi_config.h`): join SSID **PSOC-IMU** (password
`psoc-imu-1234`) on the laptop, then run with `--tcp 192.168.10.1`. The fixed
IP means there is no discovery step; the link auto-reconnects with backoff.

Design notes:

- **Transparent pipe.** Serial bytes are forwarded to the browser unmodified
  (WebSocket binary messages) and browser commands (`S`, `Q`, `CFG,…` as text
  messages) are forwarded to the serial port unmodified. The frontend's frame
  parser, CRC check, and sensor fusion are reused as-is; the firmware is
  untouched.
- **Logging tap.** The server independently parses and CRC-validates frames
  and writes them — stamped with the host wall clock — to an IMULOG01 file.
  Corrupted frames are never logged.
- **Exclusive port.** A serial port has a single owner. While the server runs,
  the browser must use the *Host server* transport (preselected when the page
  is served from `http://localhost:8765`). Direct WebSerial remains available
  when the server is not running. Bonus of the server transport: several
  browser tabs can watch the same stream.

## Usage

```powershell
pip install -r requirements.txt
python imu_server.py                  # USB: auto-detects the KitProg3 COM port
python imu_server.py --tcp 192.168.10.1   # Wi-Fi: board SoftAP, TCP port 5000
# python imu_server.py --serial-port COM5 --baud 115200 --http-port 8765 --log-dir logs
```

Open <http://localhost:8765> in Chrome/Edge → **Connect Device** →
**Start Stream**. Log files appear in `host_server/logs/`.

A new log file opens on each `S` (start) command and closes on `Q` (stop),
when the last browser tab disconnects (the server then also sends `Q` to the
device), or on server shutdown. If the device is already streaming when the
server starts, a file is opened automatically.

## Decoding logs

```powershell
python imu_log.py info logs\imu_20260611_153000.bin     # summary
python imu_log.py dump logs\imu_20260611_153000.bin -o out.csv
```

The CSV contains scaled engineering units (g, dps, °C, µT) *and* raw counts.
Scale factors come from the `CFG` commands recorded in the file (fallback:
±4 g / ±2000 dps, the UI defaults).

## Tests (no hardware needed)

```powershell
python test_roundtrip.py    # frame parser, CRC rejection, log write/read, CSV scaling
# serial path, against a virtual loopback port:
python imu_server.py --serial-port loop:// --http-port 8799   # terminal 1
python _e2e_client.py                                          # terminal 2
# Wi-Fi/TCP path, against a fake firmware device:
python _fake_device.py 5001                                    # terminal 1
python imu_server.py --tcp 127.0.0.1:5001 --http-port 8799     # terminal 2
python _e2e_tcp.py                                             # terminal 3
```

## Log format (IMULOG01)

All values little-endian. See `imu_log.py` for the reference implementation.

```
Header (20 B):  char[8] magic = "IMULOG01" | u64 t_start_us (unix µs) | u32 baud
Record:         u8 type | u64 t_host_us (unix µs) | u16 len | u8[len] payload
```

| Type | Name   | Payload                                                        |
|------|--------|----------------------------------------------------------------|
| 0x01 | META   | UTF-8 JSON: port, baud, last known sensor config               |
| 0x02 | CMD    | ASCII command sent to the device (`S`, `Q`, `CFG,…`)           |
| 0x10 | IMU    | verbatim 24 B wire payload: `i32 t_us, i16 acc[3], i16 gyr[3], i16 temp_raw, i16 mag[3]` |
| 0x20 | STATUS | verbatim status payload: `u8 imu_src, u8 mag_src`, reason text |
| 0x30 | CAMERA | verbatim camera payload: `u32 frame_id, u16 w, u16 h`, JPEG bitstream |

IMU record types reuse the wire frame type values (`uart_stream.h`), and the
payload is stored byte-for-byte as received — the log is lossless with respect
to the device data, with the host receive time added.
