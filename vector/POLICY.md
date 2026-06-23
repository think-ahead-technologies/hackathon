# Data-minimization policy (trust boundary)

The sovereignty claim made auditable: **raw sensor data and extracted features never
leave the device; only inference results cross the boundary.** This document is the
human-readable policy; [`vector.yaml`](./vector.yaml) is the enforced, reviewable version.

## Classification

| Class | Example | Size (typ.) | Disposition |
|---|---|---|---|
| `raw` | sensor window (acoustic/IMU samples) | ~4 KB | **blocked at edge** |
| `features` | FFT band energies, RMS, kurtosis | ~512 B | **blocked at edge** |
| `inference` | `{ts, container_id, model_version, anomaly_score, …}` (Contract B) | ~50 B | **egressed** |

## Enforcement

The Vector gateway sits at the trust boundary. It consumes the device data plane
(`edge.>`), classifies every event, and:

- **drops** anything classified `raw` or `features` — they are metered, then discarded;
- **forwards** only `inference` onward to the fabric (`inference.*`, Contract B), which the
  bridge persists and the dashboard renders.

Remove the policy and raw data would reach the fabric — so the gateway is load-bearing,
not decorative. The egress panel is computed from `boundary_bytes_total{disposition}`:
what was **blocked** vs what **egressed**, in real bytes.

## CRA / sovereignty rationale

- **Data minimization** (Cyber Resilience Act / GDPR-aligned): only the minimal artifact
  required downstream leaves the device; the high-volume raw signal stays local.
- **Reviewable boundary**: the policy is config in Git, diffable and auditable — not a claim
  buried in application code.
- **No hyperscaler dependency**: the gateway runs on the edge node; nothing about the
  control requires a cloud service.

## Demo notes (local stack)

- In production this gateway runs **on-device, ahead of the fabric**. In the local demo it
  runs as a service consuming `edge.>` and forwarding `inference.*` — the same enforcement,
  one process boundary over.
- Trace context (`traceparent`) travels in the message **body**, not NATS headers, because
  Vector does not forward arbitrary NATS headers. This keeps the end-to-end trace intact
  across the gateway hop.
- Payload sizes are representative constants (like the synthetic scores); the enforcement
  path carrying them is real.
