# Topic

`localized-anomaly-data` — `data_classification: "inference"`, subject
`edge.localized-anomaly.<line>.<container>` — e.g. `edge.localized-anomaly.line1.cnc-7`
(namespace `edge.>`).

The join of [`anomaly-data`](../anomaly-data/contract.md) (the on-device
inference score) with [`positinal-data`](../positinal-data/contract.md) (the
floor-map fix from the localizer): every anomaly the board reports is pinned to
**where the box was on the loop** when it fired. One enriched anomaly in → one
located anomaly out. This is what the operator map / Grafana annotation reads:
"score 0.83 at `line1.left`, x=0.0 y=1.83 m".

Computed downstream inside the trust boundary by the `anomaly-localizer`
service — *not* published by the board. It carries the actionable inference
result, so it is classified `"inference"` and crosses the Vector boundary like
the bare anomaly (so the located alert reaches the dashboard). See
[`anomaly-data`](../anomaly-data/contract.md) for the egress gate.

## Description

One NATS message per upstream anomaly. UTF-8 JSON. All
[`anomaly-data`](../anomaly-data/contract.md) fields pass through unchanged; the
location of the latest position fix for that `<line>.<container>` is added.

```json
{
  "ts": "2026-06-15T10:00:00Z",
  "container_id": "cnc-7",
  "model_version": "pdm-anomaly@2026.06.15-a3f1",
  "anomaly_score": 0.83,
  "fault_class": null,
  "location": "spindle",
  "segment": "line1.left",
  "x": 0.0,
  "y": 1.83,
  "pos_t_host_us": 1750000000000000,
  "pos_age_ms": 120,
  "data_classification": "inference",
  "bytes": 96
}
```

| Field                 | Type        | Required    | Notes                                                                       |
|-----------------------|-------------|-------------|-----------------------------------------------------------------------------|
| `ts`                  | string      | yes         | ISO-8601 UTC. Pass-through from the anomaly. Bridge needs a parseable ts.    |
| `container_id`        | string      | yes         | Pass-through. Matches the subject's `<container>` segment.                   |
| `anomaly_score`       | f64         | yes         | 0.0–1.0. Pass-through. Bridge opens an alert at `>= 0.60`.                   |
| `model_version`       | string      | recommended | Pass-through.                                                               |
| `fault_class`         | string/null | optional    | Pass-through. `null` for pure anomaly detection.                            |
| `location`            | string      | optional    | Pass-through. Component **inside the machine** (e.g. `"spindle"`).          |
| `segment`             | string/null | added       | Floor-map zone of the latest fix (e.g. `"line1.left"`). `null` if no fix.   |
| `x`                   | f64/null    | added       | Map-frame X, **metres** (origin = map top-left, +X right). `null` if no fix. |
| `y`                   | f64/null    | added       | Map-frame Y, **metres** (origin = map top-left, +Y down). `null` if no fix.  |
| `pos_t_host_us`       | u64/null    | added       | Host unix µs of the fix used. `null` if no fix yet for this stream.          |
| `pos_age_ms`          | i64/null    | added       | `ts - pos_t_host_us` in ms — how stale the location is. `null` if no fix.    |
| `data_classification` | string      | **yes**     | `"inference"` or Vector drops it. Set by this service, not pass-through.     |
| `bytes`               | int         | recommended | Wire size of this message; feeds Vector's egress audit. Recomputed here.    |

`segment` + `(x, y)` are the **floor location of the box** (where on the loop);
`location` is the **component within the machine** (which part is faulty). They
are orthogonal — keep both.

## Additional Information

- **Last-known-position join.** The service keeps the most recent
  [`positinal-data`](../positinal-data/contract.md) fix per `<line>.<container>`
  and stamps it onto each incoming anomaly. Streaming, fire-and-forget — no
  windowed time alignment. `pos_age_ms` exposes how fresh that fix is so a
  consumer can discount a stale location.
- **Anomaly is never dropped.** If no fix has arrived yet for a stream, the
  anomaly still egresses with `segment`/`x`/`y`/`pos_*` = `null`. A located
  anomaly is better than a bare one, but a bare one beats a lost cycle.
- **Timestamps.** `ts` (ISO-8601 UTC) comes from the anomaly; `pos_t_host_us`
  (unix µs) comes from the position fix. The two source topics use different
  clocks — `pos_age_ms` is the bridge between them.
- **Classification = `"inference"`.** This is the located actionable alert, gated
  the same way as the bare anomaly. Anything other than `"inference"` is dropped
  at the Vector boundary; the device data plane (`edge.raw.*`) never egresses.
- **Subject mirrors the inputs.** `edge.anomaly.*` + `edge.position.*` →
  `edge.localized-anomaly.*`. Publish to `edge.*`, **not** `inference.*` — going
  straight to `inference.*` bypasses the trust boundary.
- **Trace context.** Optional `traceparent`/`tracestate` pass through as payload
  fields (not NATS headers — Vector drops headers) so the bridge continues the
  OTel trace.
- **Fire-and-forget.** Core `PUB`, no JetStream. A lost message = a lost located
  cycle; the next anomaly re-reports. Fine for the demo.
