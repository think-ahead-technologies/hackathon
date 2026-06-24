// ABOUTME: PLACEHOLDER hardware-abstraction seam — flash, secure-enclave, and TCP transport.
// ABOUTME: Every symbol here MUST be confirmed against the E84 BSP / arch ref / app note AN235935.

#ifndef PLATFORM_HAL_H
#define PLATFORM_HAL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "model_slot.h"

// =============================================================================
// WARNING — none of these signatures are verified against the real E84 BSP.
// They are the boundary the vendor-neutral logic depends on, named after the
// operations model-pipeline.md Part 2 requires. Map each to the actual Infineon
// SMIF / TF-M / mbedTLS / connectivity call before relying on it. Treat the names
// as a spec for what the firmware needs, not as an API that exists today.
// =============================================================================

// ---- QSPI NOR flash (SMIF) — the model slots live here ----------------------
// Erase then program a region of external flash (the INACTIVE slot during an update).
bool hal_flash_erase(uint32_t offset, uint32_t len);
bool hal_flash_program(uint32_t offset, const uint8_t *data, uint32_t len);
// Map a flash region into the address space for execute/read-in-place (SMIF XIP),
// returning a pointer TFLM's GetModel() can consume. NULL on failure.
const uint8_t *hal_flash_xip_map(uint32_t offset);

// ---- Atomic metadata region (active_slot + per-slot ver/len/sha/sig) --------
bool hal_meta_read(model_meta_t *out);
bool hal_meta_write(const model_meta_t *m);  // single atomic write flips active_slot

// ---- Secure enclave / RRAM root-of-trust ------------------------------------
// Verify `sig` over `len` bytes at `data` against the device's hardware-stored key.
// This is the gate that makes "every model is signature-verified before it can run" real.
bool hal_verify_signature(const uint8_t *data, uint32_t len, const uint8_t sig[64]);
bool hal_sha256(const uint8_t *data, uint32_t len, uint8_t out[32]);

// ---- Device identity for NATS nkey auth (Ed25519) ---------------------------
// DISTINCT primitive from hal_verify_signature: that VERIFIES an ECDSA-P256 sig over the
// model manifest (root-of-trust for *what runs*); these SIGN with the device's OWN Ed25519
// nkey seed to authenticate the *connection* — the CRA unique-credential identity. The seed
// is a non-exportable PSA persistent key in protected storage and never leaves the enclave.
// Copy the device's public nkey (NATS "U..." text) into `out`. false if unavailable / too small.
bool hal_nkey_public(char *out, size_t cap);
// Ed25519-sign the server `nonce` (len bytes) with the provisioned device seed. Writes the
// 64-byte raw signature; the caller base64-encodes it for CONNECT. false on failure.
bool hal_nkey_sign(const uint8_t *nonce, size_t len, uint8_t sig[64]);

// ---- Network bring-up (call ONCE at boot, before any hal_tcp_* call) --------
// Power up the AIROC Wi-Fi radio over SDIO, start the Wi-Fi Connection Manager as
// a station, and associate to the provisioned access point so the device has an IP
// and a route to the cloud. Retries the association internally; returns true once
// an address is assigned, false if there is no link after all retries. Every
// hal_tcp_connect() assumes this has already succeeded.
bool hal_net_init(void);

// ---- Network transport (AIROC CYW55513 Wi-Fi -> TCP) ------------------------
// Open a TCP connection to the NATS broker. `host` may be a dotted-quad IPv4
// (LAN edge node) or a DNS hostname (a cloud broker) — it is resolved either way.
// Returns >=0 handle, or -1.
int  hal_tcp_connect(const char *host, uint16_t port);
// Send all bytes. Returns bytes sent, or -1.
int  hal_tcp_send(int sock, const uint8_t *data, size_t len);
// Read one CRLF-terminated protocol line into `buf` (NUL-terminated, CRLF stripped).
// Returns line length, 0 on timeout, -1 on error.
int  hal_tcp_recv_line(int sock, char *buf, size_t cap);
// Read exactly `len` payload bytes (the body following a MSG header). Returns len or -1.
int  hal_tcp_recv_exact(int sock, uint8_t *buf, size_t len);

#endif  // PLATFORM_HAL_H
