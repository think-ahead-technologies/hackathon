# firmware-app — flashable ModusToolbox application for the edge device

This is the **buildable, flashable** ModusToolbox application that wraps the vendor-neutral
firmware logic in [`../firmware`](../firmware). It targets the **PSOC™ Edge E84 AI Kit**
(`APP_KIT_PSE84_AI`) and was forked from the `PSOC_Edge_Wi-Fi_HTTPS_Client` code example, so it
inherits the working 3-core layout, Wi-Fi (AIROC CYW55513), secure-sockets + TLS, and mbedTLS/PSA.

The application code in `../firmware/src` is referenced directly via `SOURCES`/`INCLUDES` in
`proj_cm33_ns/Makefile` — **one source of truth**, shared with the host unit-test build. The MTB
app adds only the board bring-up (`proj_cm33_ns/source/main.c`) and a no-TFLM model-loader stub.

## Scope — connectivity-first build

| Capability | Status |
|---|---|
| Wi-Fi association (SDIO + WCM) | ✅ real — `hal_net_init()` |
| TLS transport + NATS publish (Contract B) | ✅ real (secure-by-default; see TLS note) |
| Contract C deploy frame receive + manifest/sig validate | ✅ real |
| QSPI flash persistence of model slots | ⛔ **stubbed** (`HAL_FLASH_STUB`) — see below |
| On-device TFLM + Ethos-U inference | ⛔ stubbed (`model_loader_stub.c`) — "full image" scope |

**Why flash + ML are stubbed:** serial-flash `v1.4.3` is the legacy cyhal API and does not compile
for the E84 (`cyhal.h` missing); the E84 needs its own SMIF bring-up. And the TFLM/Ethos-U runtime
is a separate large integration. Both are deferred to the "full image" milestone. With the stubs, an
inbound deploy is received and validated but aborts cleanly at the (failing) flash write — it cannot
corrupt anything — while Wi-Fi + TLS + NATS publish run for real.

## Build

```bash
cd firmware-app
export CY_TOOLS_PATHS=/Applications/ModusToolbox/tools_3.8
make getlibs            # first time only (pulls middleware into ../mtb_shared)
make build -j8          # produces build/app_combined.hex (all 3 cores, signed)
make logs               # stream the device debug UART (Ctrl-C to stop); `make logs-reset` from boot
```

Reusing an existing shared library cache (e.g. the one under `~/mtw`) avoids a multi-GB download:

```bash
make getlibs CY_GETLIBS_SHARED_PATH=/Users/rlang/mtw CY_GETLIBS_SHARED_NAME=mtb_shared
make build  CY_GETLIBS_SHARED_PATH=/Users/rlang/mtw CY_GETLIBS_SHARED_NAME=mtb_shared -j8
```

### Configure Wi-Fi + broker

- **Wi-Fi credentials** — pass at build time (or wire to secure storage later):
  ```bash
  make build WIFI_SSID='myssid' WIFI_PASSWORD='mypass' -j8
  ```
- **NATS broker** — set `NATS_HOST`/`NATS_PORT` in `../firmware/src/device_main.c`. `NATS_HOST` may
  be a dotted-quad (LAN) or a DNS hostname (cloud) — both resolve.
- **TLS** — the build is secure-by-default and pins a CA placeholder in `platform_hal_pse84.c`. For a
  first plaintext bring-up against an open broker, add `NATS_DISABLE_TLS` to `DEFINES` in
  `proj_cm33_ns/Makefile`; for TLS, provision the broker's CA PEM (`DEVICE_TLS_ROOT_CA`).

## Backup the current firmware, then flash (board attached)

Backup-before-flash is enforced by `device-flash.sh` — it dumps the device's current image and
aborts if the readback is empty (e.g. board not detected).

```bash
scripts/device-flash.sh                 # backup -> program app_combined.hex
# or step by step:
scripts/device-backup.sh                # -> backups/<UTC-timestamp>/{app_ns.hex, secure.hex, manifest.txt}
make program                            # flash only
scripts/device-restore.sh backups/<ts>  # roll back to a backup (write_image erase + verify)
```

The backup reads the exact external-QSPI regions the new image overwrites — `0x60340000` (the
non-secure CM33/CM55 app) and `0x70100000` (the secure CM33/boot region) — via OpenOCD's
flashloader-backed `flash read_bank`, and saves each as an Intel HEX. Restore re-programs them with
`flash write_image erase` + `verify_image`, the same path `make program` uses, so a backup fully
and verifiably reverts the device. **Verified on hardware**: backup → restore round-trips with
`verify_image` passing on both regions.

> Notes from bring-up: a raw `dump_image` of the XIP window (`0x60000000`) reads **zeros** (the SMIF
> isn't XIP-mapped in a bare debug session) — you must read through the registered flash bank, which
> requires the `-s .../GeneratedSource` search path. And `flash write_bank` from offset 0 **aborts**
> on the protected pre-app/secure region — hence the `write_image erase` approach. Confirm
> `fw-loader --device-list` sees the KitProg3 before running.

## Layout

```
firmware-app/
  Makefile, common.mk, common_app.mk   # MTB application build (MTB_TYPE=APPLICATION)
  bsps/TARGET_APP_KIT_PSE84_AI/         # board support package (incl. QSPI flash config)
  configs/boot_with_extended_boot.json  # combine + MCUboot sign step
  proj_cm33_s/                          # secure core (unchanged from example)
  proj_cm33_ns/                         # NON-secure core — our app lives here
    source/main.c                       #   board bring-up + launches device_main() task
    Makefile                            #   pulls in ../../firmware/src + HAL_FLASH_STUB
  proj_cm55/                            # CM55 core (idle in this build)
  scripts/device-backup.sh|restore|flash
```
