# Firmware — flash-resident model swap + NATS client (PSE84)

Implements **Part 2 of `model-pipeline.md`**: a model rollout that is a *model update*, not a
firmware reflash — A/B flash slots, signature verification, manifest validation, and
shadow/rollback — plus the device's NATS client (Contract B out, Contract C in).

## What's real vs. what's a placeholder

This repo separates the **vendor-neutral decision logic** (real, host-tested) from the
**hardware integration** (a documented seam to fill against the BSP). The split is deliberate:
the logic is the part worth getting provably right; the I/O is board-specific and unverifiable
off-target.

| Layer | Files | Status |
|---|---|---|
| A/B slot promote/rollback | `src/model_slot.c` | ✅ real + unit-tested |
| Contract A manifest validation | `src/model_contract.c` | ✅ real + unit-tested |
| Shadow drift / promote-rollback verdict | `src/shadow.c` | ✅ real + unit-tested |
| NATS wire framing (CONNECT/PUB/MSG) | `src/nats_proto.c` | ✅ real + unit-tested |
| NATS nkey auth: nonce parse + base64 sig + CONNECT creds | `src/nats_proto.c` | ✅ real + unit-tested |
| Power-fail-atomic metadata (2-copy + seq + CRC32) | `src/meta_store.c` | ✅ real + unit-tested |
| Contract A manifest parsing (fixed-schema JSON) | `src/manifest.c` | ✅ real + unit-tested |
| Contract C framing + chunked-flatbuffer reassembly | `src/deploy.c` | ✅ real + unit-tested |
| Hardware seam (interface) | `include/platform_hal.h` | ✅ stable interface the logic depends on |
| Hardware seam (PSE84 impl) | `src/platform_hal_pse84.c` | ⚠️ **researched scaffold, unverified** — real MTB/PSA/WCM APIs, confirm signatures on-target |
| TFLM interpreter lifecycle | `src/model_loader.cc` | 🔶 real TFLM API, on-target build only |
| Orchestration (update flow steps 1-8) | `src/device_main.c` | 🔶 skeleton, on-target build only |

`platform_hal.h` is **not an API that exists** — it's the spec of what the firmware needs from
the board, named after the operations Part 2 requires. Every symbol must be confirmed against the
real Infineon SMIF / TF-M / mbedTLS / connectivity calls before it runs. `model-pipeline.md` flags
exactly these as placeholders.

## Host tests

The pure logic builds and tests on any host — no board, no TFLM:

```bash
cd firmware
make test        # cc -Wall -Wextra -Werror; runs all suites
```

```
130 checks, 0 failures
```

### Cross-language wire-format test

```bash
make crosslang   # builds harness_deploy (real deploy.c + manifest.c), runs the Python driver
```

`make crosslang` feeds the **actual bytes** from the Platform-side framer
(`dashboard/pipeline/deploy_frame.py`) through the real C `deploy_parse_header` / `deploy_rx_accept` /
`parse_manifest`, and asserts the model reassembles **byte-identical** and the manifest parses. This
closes the sender↔device wire-format gap without a board — if the Python and C formats ever drift, this
goes red (verified: corrupting one header byte makes the C parser reject the stream).

### Live NATS login test (nkey auth)

```bash
make nats-login   # needs docker; boots a throwaway nats-server and authenticates with our C code
```

`make nats-login` proves the nkey-auth wire path against the **authoritative** server, not a second
client: it provisions a one-line fleet via the dashboard's `provision.py`, boots `nats-server` with that
auth config, then runs the handshake using the **real** `nats_parse_info_nonce` / `nats_b64_encode` /
`nats_build_connect` — the driver signs the live nonce with the device seed (Ed25519, standing in for
`hal_nkey_sign`, since the host build has no PSA). It asserts the server **accepts** our CONNECT, that a
publish to the device's own subject succeeds, that publishing as another device is denied (*Permissions
Violation*), and that an anonymous CONNECT is denied (*Authorization Violation*). This closes the one gap
the unit tests can't — that a CONNECT we assemble, with a real signature over a real nonce, authenticates.

`model_loader.cc` and `device_main.c` are **excluded from the host build** — they need the TFLM
library and the Ethos-U kernel, present only in the on-target ModusToolbox/CMSIS build. They use
the real TFLM C++ API and the HAL seam; they compile when cross-built against the BSP.

## How the pieces fit (the update flow)

```
 models.line1.deploy  ─►  on_deploy()                         (device_main.c)
   1. parse manifest (Contract A)        parse_manifest()      [TODO: BSP JSON]
   2. contract_validate() vs firmware    ── src/model_contract.c  ✅ tested
   3. write INACTIVE slot                hal_flash_erase/program  ⚠️ HAL
   4. sha256 + signature vs root-of-trust hal_sha256/verify       ⚠️ HAL
   5. load candidate, AllocateTensors    model_loader.cc          🔶 TFLM
   6. shadow N live windows              shadow_observe()      ── src/shadow.c  ✅ tested
   7. shadow_decide() PROMOTE | ROLLBACK ── src/shadow.c        ✅ tested
   8. meta_promote() + hal_meta_write()  ── src/model_slot.c    ✅ tested + ⚠️ HAL atomic write
```

The same NATS socket carries Contract B (inference results) out and Contract C (deploys) in.
Subjects match the dashboard: the device publishes to `edge.line1.cnc-7` so it crosses the
**Vector** boundary gateway, exactly like `fakegen` and the serial shim. See
`dashboard/docs/device-nats.md` for the wire-protocol crib.

## What's still on the embedded team

- **Verify and compile `src/platform_hal_pse84.c`** against the real E84 BSP. It's grounded in the
  documented APIs (`cy_serial_flash_qspi_*`, PSA Crypto `psa_verify_hash`/`psa_hash_compute`,
  `cy_wcm_*` + `cy_socket_*`) but every `VERIFY:` line — flash offsets, the SMIF XIP base, the
  provisioned PSA key id, the QSPI Configurator structs, the `cy_socket_*` enums — must be confirmed
  against the BSP / QSPI Configurator output / arch ref / AN235935. It is **not** compiled or tested here.
- **Boot bring-up not in the HAL:** `cy_serial_flash_qspi_init()`, `psa_crypto_init()`,
  `cy_wcm_init()` + `cy_wcm_connect_ap()` — call these once at startup before the HAL is used.
- **Metadata atomicity:** done — `hal_meta_write()` uses the two-copy + monotonic-sequence + CRC32
  scheme in `meta_store.c` (host-tested, incl. a crash-mid-write simulation). Embedded just confirms
  the two reserved metadata sectors (`META_FLASH_OFFSET_A`/`_B`) in the QSPI Configurator / linker.
- **Device identity / NATS nkey auth (CRA secure-by-default):** when the server's `INFO` carries a
  `nonce`, `device_main.c` signs it and joins with its per-device credential — `nats_parse_info_nonce` +
  `hal_nkey_sign` (Ed25519) + `nats_b64_encode` + `nats_build_connect(..., nkey, sig)`. No nonce → it
  connects anonymously (the open demo). The wire logic is host-tested; **embedded must implement the
  `hal_nkey_*` seam** in `platform_hal_pse84.c`: provision the device's Ed25519 nkey seed as a
  non-exportable PSA persistent key (`DEVICE_NKEY_KEY_ID`, distinct from the ECDSA-P256 model key) and
  confirm `PSA_ALG_PURE_EDDSA` / `PSA_ECC_FAMILY_TWISTED_EDWARDS` are enabled in the TF-M/Mbed TLS build.
  The seed is minted by the dashboard's `make provision` (`nats/creds/<container>.nk`) and its public
  nkey authorised in the NATS server config.
- **TLS transport (CRA confidentiality in transit):** `hal_tcp_connect` is **secure-by-default** — it
  opens a `CY_SOCKET_IPPROTO_TLS` socket, pins the provisioned CA (`DEVICE_TLS_ROOT_CA` ←
  `nats/tls/ca.pem`, in protected storage), requires verification (`CY_SOCKET_TLS_VERIFY_REQUIRED`), and
  the handshake runs in `cy_socket_connect` before any NATS byte. Build with `-DNATS_DISABLE_TLS` for the
  open/plaintext demo. This is a **transport-only** change: the `nats_proto` wire bytes are unchanged and
  identity stays the nkey signature (server-auth TLS, no client cert). The `CY_SOCKET_*` TLS option ids
  are cross-checked against Infineon's secure-sockets API reference + the `mtb-example-wifi-secure-tcp-client`.
  ⚠️ Still **unverified on-target** like the rest of `platform_hal_pse84.c`: confirm it builds/handshakes
  on the E84 BSP and wire the boot-time init (the example also calls `cy_tls_load_global_root_ca_certificates`
  before connecting). No host test exists for the BSP layer.
- **Signing contract:** the device verifies a *detached raw ECDSA-P256 signature* over the
  **manifest** (which binds the flatbuffer by `sha256` and carries the contract) — a different
  format from cosign's OCI signature. The pipeline emits it via `cosign sign-blob` + `der2raw.py`
  (`dashboard/pipeline/`); the device verifies the manifest, then checks the flatbuffer digest.
- Contract C framing + `parse_manifest()`: done — see `src/deploy.c` (frame header + chunk
  reassembly with contiguity/capacity/completion checks) and `src/manifest.c` (fixed-schema parse),
  both host-tested. `device_main.c` subscribes `models.<line>.artifact`, streams model chunks straight
  to the inactive slot, and validates the manifest before accepting any. The **sender now exists**:
  `dashboard/pipeline/deploy_frame.py` (+ `make deploy-artifact`) pulls the model from the registry and
  emits this exact wire format. Embedded only confirms `MODEL_SLOT_BYTES` matches the reserved slot size.
- Wire the spectrogram feature extractor that fills the `[1,49,40,1]` input before inference,
  matching the manifest's `feature_config` exactly (window length, `n_fft`, `hop`, 40-band linear
  triangular filterbank, `log1p(power/scale_eps)`). The reference is `wear_detector/export/spectro.py`
  (numpy-only, deterministic) — the firmware FFT must reproduce its output.
- **Anomaly scoring (replaces a classifier head):** the model is an autoencoder *encoder* — its
  `[1,K]` int8 output is an embedding, not class logits. Dequantize it (`output` scale/zero-point in
  the manifest), compute the **L2 distance to the per-unit healthy `centroid`** (manifest `embedding`),
  dwell-smooth over `dwell_w` windows, and raise an alert when the smoothed distance exceeds
  `threshold`. The centroid is this unit's healthy baseline — recomputed per board at commissioning
  *without* retraining the network. This is what lets the device flag **unknown** fault types.
- Cross-build `model_loader.cc` + `device_main.c` with TFLM + the Ethos-U kernel in ModusToolbox.
- Confirm the deployment-specific HAL values on-target: flash offsets, SMIF XIP base, provisioned
  PSA key ids, crypto build flags (`PSA_WANT_ALG_*`), and the boot bring-up. The library/PSA **API
  signatures** in `platform_hal_pse84.c` are cross-checked against Infineon's headers/examples + the
  Arm PSA Crypto API (see the file's STATUS block); what remains is values + the on-target build.
