# Edge Ops dashboard + label loop

Local-first operations dashboard for the Thin[gk]athon edge-AI demo. The whole stack runs
in `docker compose` on one laptop — no hyperscaler dependency. It consumes **Contract B**
(telemetry) and **Contract D** (labels) from the team specs and visualizes the demo spine:

```
perturb → edge detects → alert → operator labels → retrain data captured
```

## Run

```bash
cd dashboard
make up            # build + start the full stack with synthetic telemetry (Phase 1)
# or, without make:
docker compose --profile fake up --build
```

`make help` lists every target (`up`, `down`, `test`, `verify`, `label`, `logs`, `psql`, …).

### Driving the demo

| Command | Does |
|---|---|
| `make reset` | clean slate — fresh DB, metrics, and traces (down + up) |
| `make perturb` | drive `cnc-7` anomalous **on cue** (no stage `curl`) — score jumps over threshold |
| `make heal` | return the line to healthy on cue |
| `make smoke` | **pre-demo confidence check** — builds current code, waits for readiness, then asserts the whole loop end-to-end and prints `READY` / `NOT READY` |

`fakegen` defaults to **auto** (flat-then-rising) so `make up` shows the loop unattended; `perturb`/`heal`
override it on cue via a `control.<line>` message (bridge `POST /control` → fakegen). `make smoke` always
builds (`up --build`) so it validates current code, never a stale image. Cold-start races are handled by
healthchecks (`nats`, `bridge`, `timescale` gate their dependents) plus the smoke test's readiness polling.

| Surface | URL | Notes |
|---|---|---|
| Grafana (5-panel ops) | http://localhost:3000 | anonymous viewer, dark theme — no login |
| Label UI (operator) | http://localhost:8080 | open alerts + label buttons + deploy button |
| Bridge HTTP | http://localhost:8000 | `/alerts`, `POST /label`, `POST /deploy`, `/model_events`, `/metrics`, `/healthz` |
| Prometheus | http://localhost:9090 | spanmetrics + boundary + service-graph + bridge metrics |
| Vector | localhost:9598 | data-minimization gateway metrics (blocked vs egressed) |
| zot (registry) | https://localhost:5001 | sovereign OCI registry for signed model artifacts |
| Tempo | localhost:3200 | trace storage; view the edge→cloud waterfall |
| OTel Collector | localhost:4318 (OTLP), :8889 (Prom) | receives spans, derives latency + service-graph |
| NATS monitoring | http://localhost:8222 | JetStream stream/consumer state |
| Timescale | localhost:5432 | `postgres` / `postgres` |

Without the board, `--profile fake` runs `fakegen`, which publishes a flat-then-rising
anomaly score on `inference.line1.cnc-7`. Within ~20s the score crosses 0.60, the time-series
panel turns coral, and an alert appears in the feed and the label UI.

For real telemetry (Phase 2), drop the `--profile fake` and get Contract B from the board onto the
fabric. The board (KIT_PSE84_AI) has onboard Wi-Fi (AIROC CYW55513), so in production it can speak
NATS over TCP directly; for a demo the robust path is **USB-UART → `serial-shim` → NATS**, which needs
no venue network. The board prints one Contract B JSON object per line over its KitProg3 USB-UART
(115200 8N1); the shim reads those lines and publishes them to `edge.<line>.<container>` — the same
device data plane `fakegen` uses, so it flows through the Vector boundary gateway unchanged.

```bash
# macOS — Docker can't reach USB serial, so run the shim on the host:
make up-real                                    # stack without fakegen
ls /dev/tty.usbmodem*                           # find the board's port
make serial-shim SERIAL_PORT=/dev/tty.usbmodem1101

# Linux edge node — pass the device through to a container instead:
SERIAL_PORT=/dev/ttyACM0 docker compose --profile serial up -d --build serial-shim
```

The shim skips boot banners, blank lines, and baud-mismatch gibberish (it only forwards lines that
parse as Contract B), and carries trace context in the message body so the device→bridge service-graph
edge and the latency panels keep working with a real board attached.

## Services

- **nats** — message fabric, JetStream enabled so inference buffers/replays across a bridge restart.
- **vector** — the data-minimization gateway (trust boundary). Consumes the device data plane
  (`edge.>`), **drops `raw`/`features`**, forwards only `inference` to `inference.*` (Contract B), and
  meters blocked-vs-egressed bytes. Load-bearing: remove it and raw never reaches the bridge. The
  policy is reviewable code — [`vector/vector.yaml`](vector/vector.yaml) + [`vector/POLICY.md`](vector/POLICY.md).
- **bridge** — subscribes `inference.>`, `labels.>`, and `models.>`, persists to Timescale, opens
  alerts over `THRESHOLD` (default 0.60), queues a `retrain_queued` event once
  `RETRAIN_AFTER_LABELS` (default 1) actionable labels accumulate, records Contract C deploys, and
  emits a `cloud.ingest` span per message.
- **serial-shim** — Phase 2 device→fabric transport. Reads Contract B inference lines off the board's
  USB-UART and publishes them to `edge.>` (through the Vector boundary, like `fakegen`). Profile `serial`
  on Linux (device passthrough); on macOS run it on the host with `make serial-shim SERIAL_PORT=…`.
- **timescale** — Grafana datasource for time-series; hypertable `scores`, plus `alerts` and `labels`.
- **grafana** — provisioned datasources (Timescale + Prometheus) + the committed `ops.json` dashboard.
- **label-ui** — static operator page; a click `POST`s to the bridge, which publishes Contract D.
- **otel-collector** — receives OTLP spans from the device path and the bridge; the **spanmetrics**
  connector derives a duration histogram per hop and the **service_graph** connector derives the
  edge→cloud service map, both exposed for Prometheus on `:8889`; also forwards raw traces to Tempo.
- **prometheus** — scrapes the collector (spanmetrics + service-graph + egress) and the bridge `/metrics`.
- **tempo** — stores the raw traces; Grafana's Tempo datasource renders the
  `sensor→inference→fabric→cloud` waterfall and the service map (`Edge Ops · traces` dashboard).

### Data path (with the minimization gateway)

```
fakegen/device ──edge.*  {raw, features, inference}──▶ Vector gateway ──drop raw/features──▶ ✗
   (trace context in the message body)                     │
                                                           └──forward inference──▶ inference.* ──▶ bridge ──▶ Timescale
```

### Observability path (the spec's stretch goal, wired up)

```
fakegen/device ──spans: sensor→inference→fabric.publish──▶ OTel Collector ──┬─spanmetrics───▶ Prometheus ──▶ Grafana
   (trace context in body, survives the Vector hop)             ▲          ├─service_graph─▶ Prometheus    (p95/hop, boundary,
bridge ──span: cloud.ingest (continues the trace)────────────────┘          └─raw traces────▶ Tempo ────────▶  service map, waterfall)
```

`fabric.publish` (CLIENT, fakegen) and `cloud.ingest` (SERVER, bridge) form the one cross-service
span pair, so the service_graph connector draws a single `fakegen → bridge` edge = the edge→cloud hop.
Tempo runs **2.10.7**, not 3.0.x: Tempo 3.0 removed the monolithic ingester write path in favour of a
heavier distributor/live-store/block-builder architecture that isn't worth running on a demo laptop.

The device tags egress bytes with `data.classification` (`raw`/`features` stay at 0 on the device,
`inference` is what actually leaves) — an OTel counter, scraped as `egress_bytes_total`. The latency
panel is `histogram_quantile(0.95, ...)` over `spanmetrics_duration_milliseconds_bucket` per `span_name`.

## Build phases (acceptance criteria from the spec)

1. **Lights on** — `--profile fake`; the score panel crosses threshold and an alert appears, from fake data.
2. **Real telemetry** — point the device at NATS; perturbing the rig moves the live panel.
3. **Close the loop** — click a label in the UI; the alert row flips to `labeled`/`dismissed`
   and a row lands in `labels` (the data ML retrains from).

### Closing the loop visibly (Contract C)

A label doesn't just get stored — it drives the rest of the spine:

```
label click → retrain_queued event → (platform) deploy → models.line1.deploy → deployed event
                                                                                      │
                              annotation on the live score panel + 'Edge Ops · model loop' dashboard
```

- The bridge auto-emits a `retrain_queued` model event once `RETRAIN_AFTER_LABELS` actionable
  labels accumulate (default 1, so it fires on the first real label — tune up for a slower demo).
- Hit **Deploy retrained model** on the label UI (or `make deploy`, or `POST /deploy`) to publish
  Contract C on `models.<line>.deploy`; the bridge records a `deployed` event with an auto-incremented
  version (`pdm-anomaly@retrained-N`) or one you pass.
- Both events show as **purple annotations** on the main score panel and in the
  **`Edge Ops · model loop`** dashboard (labels captured · retrains queued · current version · event feed).

## Model pipeline (Vela gate → sign → GitOps promote)

Stages 4–6 of [`model-pipeline.md`](../model-pipeline.md) — *everything right of the model artifact*,
which is the Platform's differentiator ("the model is commodity; the platform around it is the hard
part"). Board-free; lives in `pipeline/`.

```
Vela summary CSV ─► gate ─► manifest + sha256 ─► oras push → zot registry ─► cosign sign (in-registry)
                                                                                       │
  desired-state.json (bump a version) ─► promote: observed vs desired → cosign verify (registry) → Contract C ─► dashboard
```

**One-time setup:** `make keygen` (cosign keypair + zot TLS cert), then `make up`.

| Command | Does |
|---|---|
| `make gate` | run the deployability gate on the sample Vela summary (SRAM / latency / CPU-fallback / flatbuffer-size policy) |
| `make gate MODEL=bad` | watch the gate **reject** a bad model with reasons — a bad model never reaches a device |
| `make keygen` | one-time: cosign keypair (private key gitignored) + a self-signed zot TLS cert |
| `make package` | build manifest + sha256, sign the manifest for the device (**`cosign sign-blob`** → raw ECDSA-P256), **`oras push`** artifact + manifest + device sig to zot, then **`cosign sign`** the OCI artifact in-registry |
| `make promote` | GitOps reconcile: desired (`desired-state.json`) vs observed (`model_events`) → **`cosign verify`** the registry artifact → publish the Contract C deploy *event* |
| `make deploy-artifact` | pull the desired model from the registry (`oras pull`) and stream its *bytes* to devices as chunked Contract C frames on `models.<line>.artifact` |

Notes:
- The gate is **real logic** on a **committed sample** Vela summary CSV (`pipeline/samples/`). When ML
  hands over an int8 `.tflite`, `pip install ethos-u-vela` produces the real CSV and the gate runs
  unchanged — thresholds + column names live in `pipeline/vela.policy.json`.
- **Signed artifact in a sovereign registry:** `make package` pushes the model + manifest to **zot**
  (a self-hosted CNCF OCI registry, TLS) with **oras**, then **cosign**-signs it in-registry. cosign is
  pinned in a container (`v2.5.3`) and signs **offline** (no public Rekor transparency log) — keys you
  hold. zot serves HTTPS with a throwaway self-signed cert; the tools connect skip-verify
  (`--allow-insecure-registry` / `--insecure`).
- **Two delivery planes, registry is the source of truth.** `models.<line>.deploy` carries the JSON
  deploy *event* (bridge → dashboard annotation). `models.<line>.artifact` carries the model *bytes*:
  `make deploy-artifact` pulls the desired version from the zot registry and re-frames it as chunked
  Contract C frames (`deploy_frame.py`, wire-format-matched to the firmware's `deploy.c`) on NATS. The
  device speaks NATS, not OCI — so the gateway pulls from the registry and bridges the bytes onto the
  fabric the MCU can actually consume.
- **Two signatures, two trust domains, one key.** `cosign sign` produces the OCI/registry signature
  that gates promotion (verified by `promote.py`). `cosign sign-blob` over `manifest.json` produces a
  *detached* signature the **device** verifies — converted from ASN.1 DER to raw `r||s` by `der2raw.py`
  so the MCU's PSA `psa_verify_hash` can check it directly. The device verifies the manifest (which
  binds the flatbuffer by `sha256`), not the cosign OCI sig (different format, needs the registry).
- `promote` is a true reconcile loop: it compares the Git desired version to the **deployed** version in
  our system of record (`model_events`) and **refuses to deploy unless `cosign verify` passes** on the
  registry artifact — the supply-chain gate before a model can reach a device.
- **Out of scope here:** model training/quantize (ML), real Vela compile (needs ML's `.tflite`), and the
  flash-resident A/B model swap (Embedded/firmware) — see Part 2 of `model-pipeline.md`.

## Device identity + encrypted fabric / NATS auth (CRA secure-by-default)

The open demo fabric has no auth or TLS (any client can publish as any device, in plaintext). For the
EU CRA *secure-by-default* requirements — unique per-device credentials, no shared/default secret, and
confidentiality/integrity in transit — `pipeline/provision.py` issues a **unique Ed25519 nkey per device
and service**, mints a **TLS chain** (CA + server cert), and renders a least-privilege NATS config that
turns both on. No operator/resolver infra: it's static nkey auth + server-auth TLS, pure config.

```bash
make provision     # issue nkeys + mint the TLS chain -> nats/creds/*.nk + nats/tls/* + nats/nats-server.conf
make verify-auth   # boot a throwaway nats-server and prove the four properties below
make up-secure     # run the WHOLE stack over TLS + auth: every client presents its seed and verifies the CA
make nats-secure   # or just the broker under the provisioned config
```

- **Unique credentials.** Each board gets its own seed (`nats/creds/<id>.nk`, mode 0600) — the
  secret that would live in the board's PSA-backed protected storage. There is no shared password.
- **Least privilege.** A device may publish **only** `edge.<line>.<its-own-id>` and subscribe only
  to its own model-rollout/control subjects; `cnc-7` cannot publish as `press-3`. Service permissions
  (`vector`/`bridge`/`fakegen`/`pipeline`) match exactly the subjects their code uses. Roles live in
  `pipeline/provision.py`; the fleet inventory is `pipeline/fleet.json` (add a board, re-run `provision`).
- **Encrypted in transit.** The server presents a cert (SANs `nats`/`localhost`/`127.0.0.1`); every
  client verifies it against `nats/tls/ca.pem`. Server-auth TLS — identity is the nkey, not a client
  cert. The CA private key is never persisted (each `make provision` re-mints the chain).
- **Secure by default.** No anonymous fallback user — an unknown or credential-less client is rejected.

`make verify-auth` proves all four against a real `nats-server` **over TLS**: authorized publish
succeeds, a cross-device publish is denied (*Permissions Violation*), an anonymous client is denied
(*Authorization Violation*), and an untrusted/plaintext connection is rejected at the TLS layer. The
codec, config render, and cert chain are unit-tested (`make test-pipeline`).

**Clients are wired.** `make up-secure` runs the full stack over TLS + auth end-to-end: each python
service (`bridge`/`fakegen`/`serial-shim`/`pipeline`) connects with `nats.connect(..., nkeys_seed=…, tls=…)`
gated on `NATS_NKEY_SEED` + `NATS_CA_FILE` env vars (both unset on the open `make up` demo, so that path
stays plaintext and unchanged); the **Vector** gateway authenticates and verifies the CA via
`vector/vector.secure.yaml`. Verified: telemetry flows fakegen → Vector (boundary enforced) → bridge →
Timescale over TLS with no permission violations, while cross-device / anonymous / untrusted-TLS clients
are rejected. The seed file carries **no trailing newline** — nats-py reads exact file bytes, so a stray
`\n` corrupts the credential (regression-tested).

> **On a real board:** the firmware presents the same kind of nkey seed from PSA-backed protected
> storage and signs the server `nonce` (see `docs/device-nats.md`); `make provision` is the issuance step.

## Tests

```bash
make test             # all four suites below
make test-bridge      # bridge pure logic
make test-vector      # data-minimization policy
make test-pipeline    # deployability-gate policy
make test-serial-shim # serial→NATS shim line parsing + payload shaping
```

- **Bridge** — pure logic (payload validation, the alert decision, retrain threshold, version
  increment) unit-tested with pytest in the target python image.
- **Vector policy** — the data-minimization decisions are tested with `vector test` (no infra):
  raw/features are classified `blocked` and dropped, inference is `egressed` and cleaned to
  Contract B (internal fields stripped, trace context kept), and any unknown class fails **closed**.

The IO layer (NATS + Timescale) is exercised end-to-end by bringing the stack up with `fakegen`.

## Notes / honest scope

- The **p95-latency-by-hop** panel is now real: spans flow through the collector's spanmetrics
  connector into Prometheus histograms. With `fakegen`, the hop *durations* are simulated (like the
  scores); on the real-rig path the device emits real spans and the pipeline is unchanged.
- **Data minimization at the trust boundary** is now *enforced*, not asserted: fakegen publishes the
  device's full data plane (`raw`/`features`/`inference`) to `edge.*`; the **Vector** gateway drops
  `raw`/`features` and forwards only `inference` to the bridge. The panel reads
  `boundary_bytes{disposition}` — real blocked (~9 KB/s raw+features) vs egressed (~0.1 KB/s inference)
  bytes. See [`vector/POLICY.md`](vector/POLICY.md).
- **Trace context rides in the message body**, not NATS headers: Vector doesn't forward arbitrary
  NATS headers, so `traceparent` is carried as a payload field and the bridge extracts it from there —
  which keeps the `cloud.ingest` span and the `fakegen → bridge` service-graph edge intact across the
  gateway hop.
- The Prometheus-backed panels (latency, egress) populate within ~10s of `fakegen` running; the
  Timescale-backed panels (score, alerts, p95 score, feed) are seeded so they're never empty.
- Colors match the deck: coral `#D85A30` (over threshold / raw / active), teal `#1D9E75` (healthy /
  telemetry), purple `#534AB7` (model / control events).
