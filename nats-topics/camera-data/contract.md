# Topic

`camera-data` — `data_classification: "derived"`, subject
`edge.camera.<line>.<container>` (namespace `edge.>`). Derived from the `raw`
topic's type-`0x30` CAMERA records (see [`../raw/contract.md`](../raw/contract.md)).

Per-frame stream of JPEG-encoded still images from the UVC webcam on the PSOC™
Edge kit's USB-C host port. Captured on the CM55, JPEG-encoded on-device, and
streamed as type-`0x30` frames. **Wi-Fi only** (TCP 5000) — the UART (115200)
lacks the bandwidth.

## Description

One NATS message per camera frame. Each message is **UTF-8 JSON**. The image
itself is a complete JPEG file, **base64-encoded** into the `data` field.

```json
{
  "frame_id": 12044,
  "width": 640,
  "height": 480,
  "format": "jpeg",
  "encoding": "base64",
  "t_us": 1843221004,
  "t_host_us": 1750845632123456,
  "data": "/9j/4AAQSkZJRgABAQAAAQ..."
}
```

### Fields

| Field        | Type   | Description                                                                 |
|--------------|--------|-----------------------------------------------------------------------------|
| `frame_id`   | u32    | Monotonic frame counter from the device (`u32 frame_id` of the wire frame). |
| `width`      | u16    | Image width in pixels (`u16 w`).                                            |
| `height`     | u16    | Image height in pixels (`u16 h`).                                           |
| `format`     | string | Image codec. Always `"jpeg"`.                                              |
| `encoding`   | string | Transport encoding of `data`. Always `"base64"`.                          |
| `t_us`       | u32    | Device-side capture time, microseconds (frame clock).                       |
| `t_host_us`  | u64    | Host unix receive time, microseconds (host wall clock).                     |
| `data`       | string | Base64 of the complete JPEG bitstream (SOI…EOI). Decode → write `.jpg`.     |

The `frame_id`, `width`, `height` mirror the raw camera header (`<IHH`); `data`
is the raw JPEG bitstream that followed it, base64-encoded.

## Additional Information

- **One frame per message.** No chunking — each message is a full, standalone
  JPEG. Decode `data` from base64 to get a byte-for-byte valid `.jpg`.
- **Self-describing dimensions.** Trust `width`/`height` from the header; do not
  infer from the JPEG payload.
- **Timestamps.** `t_us` = device-side µs (per frame). `t_host_us` = host unix µs
  receive time — use for wall-clock alignment with other topics.
- **Lossy by codec, lossless by transport.** JPEG is lossy compression done
  on-device; the bitstream is transported intact (no re-encode downstream).
- **Wi-Fi only.** Frames exist only over the TCP 5000 link (SoftAP `PSOC-IMU`).
  No camera data on UART transport.
- **Lifecycle.** Frames flow while a recording/preview is active; stream closes on
  `Q` (stop), last consumer disconnect, or shutdown.
