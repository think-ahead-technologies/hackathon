# Topic

`anomaly-data` — `data_classification: "inference"`, subject
`edge.anomaly.<line>.<container>` — e.g. `edge.line1.cnc-7` (namespace `edge.>`).

On-device inference output: per-cycle anomaly score (+ optional fault class) for
predictive maintenance. **This is "Contract B"** — the only topic the firmware
publishes from the inference model, not derived downstream from
[`raw`](../raw/contract.md). The board hand-rolls a tiny NATS client and PUBs
directly (AIROC CYW55513 → TCP → NATS). Wire-protocol crib:
[`../../docs/device-nats.md`](../../docs/device-nats.md).

The Vector boundary gateway consumes `edge.>`, audits it, and forwards only
`data_classification:"inference"` messages onward (→ bridge → Timescale →
Grafana). Publish to `edge.*`, **not** `inference.*` — going straight to
`inference.*` bypasses the trust boundary.

## Description

One NATS message per inference cycle. UTF-8 JSON.

```json
{
  "ts": "2026-06-15T10:00:00Z",
  "container_id": "cnc-7",
  "model_version": "pdm-anomaly@2026.06.15-a3f1",
  "anomaly_score": 0.83,
  "fault_class": null,
  "location": "spindle",
  "data_classification": "inference",
  "bytes": 47
}
```

| Field                 | Type        | Required        | Notes                                                              |
|-----------------------|-------------|-----------------|--------------------------------------------------------------------|
| `ts`                  | string      | yes             | ISO-8601 UTC. Bridge needs a parseable timestamp.                  |
| `container_id`        | string      | yes             | Must match the subject's `<container>` segment.                    |
| `anomaly_score`       | f64         | yes             | 0.0–1.0. Bridge opens an alert at `>= 0.60`.                       |
| `model_version`       | string      | recommended     | Shown on dashboard / annotations.                                  |
| `fault_class`         | string/null | optional        | `null` for pure anomaly detection; else the classified fault.      |
| `location`            | string      | optional        | e.g. `"spindle"`.                                                  |
| `data_classification` | string      | **yes**         | Must be `"inference"` or Vector drops the message.                 |
| `bytes`               | int         | recommended     | Message wire size; feeds Vector's egress audit. Omit → counts 0.   |

## Additional Information

- **Not derived from `raw`.** Unlike `imu-data` / `camera-data` / etc., this is
  model output published by the board itself. No upstream split — the score is
  computed on-device.
- **Alert threshold.** Bridge raises an alert at `anomaly_score >= 0.60`.
- **`data_classification` is the gate.** Anything other than `"inference"` is
  dropped at the Vector boundary. The device data plane (`edge.raw.*`,
  classification `"raw"`) never egresses; derived topics carry `"derived"`.
- **Timestamp.** `ts` is ISO-8601 UTC (not the µs `t_us`/`t_host_us` of the
  sensor topics) — this topic is bridge-facing, not frame-aligned.
- **Direct-to-NATS path.** Firmware PUBs over plain TCP 4222 with a ~50-line
  hand-rolled client (`CONNECT`/`PUB`/`PING`/`PONG`). Secure path adds nkey/sig
  auth + TLS — payload unchanged. See [`../../docs/device-nats.md`](../../docs/device-nats.md).
- **Fire-and-forget.** Core `PUB`, no JetStream. Lost message = lost cycle; the
  next cycle re-reports. Fine for the demo.
- **Trace context.** Optional `traceparent`/`tracestate` as payload fields (not
  NATS headers — Vector drops headers); the bridge continues the OTel trace.
