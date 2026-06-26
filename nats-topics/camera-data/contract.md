# Topic

`camera-data` — `data_classification: "derived"`, subject
`edge.camera.<line>.<container>` (namespace `edge.>`).

Per-frame stream of JPEG-encoded still images from the UVC webcam on the PSOC™
Edge kit's USB-C host port. Captured on the CM55, JPEG-encoded on-device, and
**published directly to NATS by the device** over its Wi-Fi/TLS connection — no
host bridge and no `raw`-topic round-trip.

## Description

One NATS message per camera frame. The message body is **binary** (not JSON):
a fixed 12-byte little-endian header followed by the complete JPEG bitstream.
The JPEG is carried **as raw bytes** — there is no base64 and no JSON envelope.

```
NATS message body (little-endian):
  offset 0   u32 frame_id     monotonic device frame counter
  offset 4   u16 width        image width in pixels
  offset 6   u16 height       image height in pixels
  offset 8   u32 t_us         device capture time, microseconds (0 if unavailable)
  offset 12  u8[] jpeg        complete JPEG bitstream (SOI…EOI), byte-for-byte
```

The header is exactly **12 bytes** (`CAM_PROTO_HDR_LEN`); everything after it is
the `.jpg`. Total body length = `12 + len(jpeg)`.

### Fields

| Field      | Type   | Bytes | Description                                                        |
|------------|--------|-------|-------------------------------------------------------------------|
| `frame_id` | u32 LE | 0–3   | Monotonic frame counter from the device.                          |
| `width`    | u16 LE | 4–5   | Image width in pixels.                                            |
| `height`   | u16 LE | 6–7   | Image height in pixels.                                           |
| `t_us`     | u32 LE | 8–11  | Device-side capture time, microseconds (frame clock). 0 if unset. |
| `jpeg`     | bytes  | 12–   | Complete JPEG bitstream (SOI…EOI). Write directly → `.jpg`.        |

A consumer that wants a host wall-clock timestamp (`t_host_us` in the old
JSON form) stamps it on receive — there is no host bridge in the path to add it.

## Additional Information

- **One frame per message.** No chunking — each message is a full, standalone
  JPEG. Slice off the 12-byte header and the remainder is a byte-for-byte valid
  `.jpg`.
- **Binary, not JSON.** This topic is the one image-carrying topic and is
  deliberately binary: sending the JPEG as raw bytes avoids the ~33% base64
  inflation and the JSON envelope. Consumers must read the fixed binary header
  rather than `json.loads`.
- **Self-describing dimensions.** Trust `width`/`height` from the header; do not
  infer from the JPEG payload.
- **No application CRC.** Integrity is provided by the NATS connection's TLS
  record MAC; there is no extra checksum in the body.
- **Lossy by codec, lossless by transport.** JPEG is lossy compression done
  on-device; the bitstream is transported intact (no re-encode downstream).
- **Lifecycle.** Frames flow while a recording/preview is active; the stream
  stops on `Q` (stop), last consumer disconnect, or shutdown.
