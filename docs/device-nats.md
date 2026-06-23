# Contract B over NATS — device wire-protocol crib

For the firmware that publishes inference results directly to the fabric over Wi-Fi
(AIROC CYW55513 → TCP → NATS). The serial path uses `serial-shim/` instead; this doc
is for the **direct-to-NATS** path where the board hand-rolls a tiny NATS client.

NATS core is a line-based text protocol over a plain TCP socket. You need exactly four
verbs to publish: `CONNECT`, `PUB`, `PING`, `PONG`. No client library required.

## 1. Connect

- **Transport:** plain TCP to `nats://<laptop-ip>:4222`. No TLS, no auth in the demo.
  (Find the laptop IP with `ipconfig getifaddr en0` on the host; the board and laptop
  must be on the same network.)
- On connect the **server speaks first**, sending one line:

  ```
  INFO {"server_id":"...","max_payload":1048576,...}\r\n
  ```

  You can ignore the contents for publishing; just read until `\r\n`.

- Then **you** send `CONNECT` with your options. Set `verbose:false` so the server does
  **not** ack every `PUB` with `+OK` (less for the MCU to parse):

  ```
  CONNECT {"verbose":false,"pedantic":false,"name":"cnc-7","lang":"c","version":"0.1","protocol":1}\r\n
  ```

Every protocol line — including the ones you send — ends in `\r\n` (CRLF), not just `\n`.

## 2. Publish a Contract B message

Format: `PUB <subject> <payload-byte-count>\r\n<payload>\r\n`

- **Subject:** `edge.<line>.<container_id>` — e.g. `edge.line1.cnc-7`. Publish to `edge.*`,
  **not** `inference.*`: the Vector boundary gateway consumes `edge.>`, audits it, and
  forwards only `inference`-classified messages onward. Going straight to `inference.*`
  bypasses the trust boundary — don't.
- **Byte count** is the length of the JSON payload in bytes (UTF-8), **excluding** the
  trailing `\r\n`. Count bytes, not characters.

### Payload (Contract B + the gateway tag)

```json
{"ts":"2026-06-15T10:00:00Z","container_id":"cnc-7","model_version":"pdm-anomaly@2026.06.15-a3f1","anomaly_score":0.83,"fault_class":null,"location":"spindle","data_classification":"inference","bytes":47}
```

| Field | Required | Notes |
|---|---|---|
| `ts` | yes | ISO-8601 UTC. The bridge needs a parseable timestamp. |
| `container_id` | yes | Must match the subject's container segment. |
| `anomaly_score` | yes | float; the bridge opens an alert at `>= 0.60`. |
| `model_version` | recommended | shown on the dashboard / annotations. |
| `fault_class` | optional | `null` for pure anomaly detection. |
| `location` | optional | e.g. `spindle`. |
| `data_classification` | **yes for the boundary** | must be `"inference"` or Vector drops it. |
| `bytes` | recommended | the message's wire size; feeds Vector's egress audit metric. Omit and it counts as 0. |

### Exact bytes on the wire

For a 47-byte payload `{"container_id":"cnc-7","anomaly_score":0.83,...}` the publish frame is:

```
PUB edge.line1.cnc-7 47\r\n
{"container_id":"cnc-7","anomaly_score":0.83,...}\r\n
```

(written as one contiguous byte stream; the newline after `47` separates the header from
the payload, and the final `\r\n` terminates the payload).

## 3. Keepalive

The server periodically sends `PING\r\n`. You **must** answer `PONG\r\n` or it will close
the connection after `max_outstanding_pings`. That's the only background obligation — a
loop that reads lines and replies `PONG` to any `PING` is enough. You may also send your
own `PING\r\n` to check liveness.

## 4. Minimal publish loop (pseudocode)

```
tcp_connect(laptop_ip, 4222)
read_line()                       // INFO ... (ignore)
send("CONNECT {\"verbose\":false,\"protocol\":1}\r\n")

every cycle:
    json   = build_contract_b()           // see payload above
    n      = byte_length(json)
    send("PUB edge.line1.cnc-7 " + n + "\r\n")
    send(json + "\r\n")

on any received line starting with "PING":
    send("PONG\r\n")
```

That is the entire client. ~50 lines of C on top of the board's TCP socket.

## 5. Verifying without the dashboard

From the laptop, watch what the board publishes (raw, before the gateway):

```bash
# any NATS CLI / container; subscribe to the device data plane
nats sub 'edge.>'              # needs the nats CLI, or:
docker run --rm --network dashboard_default natsio/nats-box \
  nats sub --server nats://nats:4222 'edge.>'
```

If your Contract B messages show up there, the wire format is correct; the rest of the
pipeline (Vector → bridge → Timescale → Grafana) is already proven by `fakegen`.

## Notes / scope

- **The demo fabric (`make up`) has no auth/TLS; the secure path (`make up-secure`) does.** Only the
  `CONNECT` line changes: when the server requires auth its `INFO` carries a `nonce`, and the board adds
  two fields — `"nkey":"U…"` (its public key) and `"sig":"…"` (base64 of the Ed25519 signature over the
  nonce). Everything else above is identical. The secure path also runs over **TLS**: the board completes
  a TLS handshake before the `INFO` line, verifying the server cert against the provisioned CA
  (`nats/tls/ca.pem`) — a transport-only concern (the bytes above are unchanged), see the firmware
  README's "TLS transport" note. The firmware implements the auth path:
  `nats_parse_info_nonce` + `hal_nkey_sign` + `nats_b64_encode` +
  `nats_build_connect(…, nkey, sig)` (see `firmware/src/nats_proto.c`); no nonce → it connects
  anonymously. Issue the per-device seed + server config with `make provision`, and `firmware/`'s
  `make nats-login` validates the whole handshake against a real `nats-server`. The seed is the CRA
  unique credential and lives in the board's protected storage (see the dashboard README, "Device
  identity / NATS auth").
- **JetStream is not required to publish.** A plain core `PUB` is fire-and-forget and is
  all the demo needs. JetStream's persistence/ack adds a request/reply handshake
  (`PUB $JS.API...` + parsing the ack) — only worth it if the board must guarantee
  delivery across a fabric restart. Skip it for the demo.
- **Trace context** (`traceparent`/`tracestate`) is optional. Include them as payload
  fields (not NATS headers — Vector doesn't forward headers) if the firmware emits OTel
  spans; the bridge will continue the trace and the service graph will show the device hop.
```
