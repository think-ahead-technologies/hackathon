// ABOUTME: PSE84 implementation of platform_hal.h — SMIF flash, PSA Crypto verify, RRAM key, Wi-Fi TCP.
// ABOUTME: GROUNDED IN THE DOCUMENTED MTB APIs BUT UNVERIFIED — not compiled/tested here; confirm on-target.

// =============================================================================
//  STATUS: researched scaffold, NOT a verified build.
//
//  The API *signatures* used below are cross-checked against Infineon's published
//  headers/examples + the Arm PSA Crypto API (serial-flash qspi read/write/erase/
//  enable_xip; cy_socket_create/connect/send/recv/setsockopt + cy_socket_sockaddr_t;
//  psa_hash_compute/psa_verify_hash/psa_sign_message via the openless key-id API).
//  What is still UNVERIFIED and must be confirmed on-target: the flash offsets, the
//  SMIF XIP base, the provisioned key ids, the crypto build flags (PSA_WANT_ALG_*),
//  the boot bring-up, and that the whole thing compiles/links against the E84 BSP.
//  Lines marked `VERIFY:` are the deployment-specific values. This file is excluded
//  from the host test build (it needs the BSP + TF-M + lwIP).
//
//  References (Infineon code examples that use these exact APIs):
//   - SMIF/QSPI:  github.com/Infineon/mtb-example-psoc6-qspi-xip
//                 infineon.github.io/mtb-pdl-cat1 (SMIF) + serial-flash middleware
//   - PSA Crypto: github.com/Infineon/mtb-example-psoc-edge-mbedtls-psa-crypto
//                 github.com/Infineon/mtb-example-psoc-edge-crypto-sha
//   - Wi-Fi/TCP:  github.com/Infineon/mtb-example-wifi-secure-tcp-client
//                 infineon.github.io/wifi-connection-manager
// =============================================================================

#include "platform_hal.h"

#include <stdio.h>
#include <string.h>

#include "meta_store.h"   // power-fail-atomic two-copy metadata logic (host-tested)

// ---- BSP / middleware headers (present only in the on-target MTB build) -----
#include "cybsp.h"
#include "cy_serial_flash_qspi.h"   // SMIF serial-flash middleware
#include "psa/crypto.h"             // PSA Crypto (Mbed TLS / TF-M backed)
#include "cy_wcm.h"                 // Wi-Fi Connection Manager
#include "cy_secure_sockets.h"      // Secure Sockets (over lwIP); TLS unless -DNATS_DISABLE_TLS

// =============================================================================
//  Flash layout — these offsets come from your flash plan / linker, NOT guesses.
//  VERIFY: set to the actual reserved regions in the QSPI Configurator / .ld.
// =============================================================================
// Two metadata copies for power-fail-atomic updates (see meta_store). VERIFY: two distinct
// reserved sectors in the QSPI Configurator / .ld.
#define META_FLASH_OFFSET_A (0x00100000u)
#define META_FLASH_OFFSET_B (0x00110000u)
// VERIFY: the memory-mapped base the SMIF exposes for XIP reads on the E84.
// On CAT1 parts this is the SMIF XIP region base (e.g. CY_XIP_BASE); confirm for E84.
#define SMIF_XIP_BASE       (0x60000000u)

// PSA persistent key id of the model-signing public key, provisioned into the
// device's protected storage (RRAM-backed) at manufacturing. VERIFY: the id you
// provision with; the key is ECC P-256 public (verifies a detached ECDSA sig).
#define MODEL_SIGNING_KEY_ID ((psa_key_id_t)0x00000001u)

// The on-disk metadata layout (meta_blob_t: magic, seq, meta, crc) and the
// copy-selection logic live in meta_store.h/.c — pure, host-tested.

// -----------------------------------------------------------------------------
//  QSPI NOR flash (SMIF serial-flash middleware)
//  init is expected to have run at boot: cy_serial_flash_qspi_init(...). The
//  config struct comes from the QSPI Configurator (cycfg_qspi_memslot.h).
// -----------------------------------------------------------------------------

bool hal_flash_erase(uint32_t offset, uint32_t len) {
    // VERIFY: erase granularity — must be sector-aligned; the middleware erases
    // whole sectors. Round/align offset+len to the device sector size.
    return cy_serial_flash_qspi_erase(offset, len) == CY_RSLT_SUCCESS;
}

bool hal_flash_program(uint32_t offset, const uint8_t *data, uint32_t len) {
    // VERIFY: write granularity (page program size, typically 256 B). The
    // middleware handles page splitting in recent versions; confirm for E84.
    return cy_serial_flash_qspi_write(offset, len, data) == CY_RSLT_SUCCESS;
}

const uint8_t *hal_flash_xip_map(uint32_t offset) {
    // Put the SMIF in memory-mapped (XIP) mode so the flatbuffer is directly
    // addressable, then hand TFLM a pointer. (Alternative: copy into HYPERRAM.)
    if (cy_serial_flash_qspi_enable_xip(true) != CY_RSLT_SUCCESS) {
        return NULL;
    }
    // VERIFY: SMIF_XIP_BASE for the E84. The mapped address is base + flash offset.
    return (const uint8_t *)(SMIF_XIP_BASE + offset);
}

static bool read_both_copies(meta_blob_t *a, meta_blob_t *b) {
    return cy_serial_flash_qspi_read(META_FLASH_OFFSET_A, sizeof(*a), (uint8_t *)a)
               == CY_RSLT_SUCCESS &&
           cy_serial_flash_qspi_read(META_FLASH_OFFSET_B, sizeof(*b), (uint8_t *)b)
               == CY_RSLT_SUCCESS;
}

bool hal_meta_read(model_meta_t *out) {
    meta_blob_t a, b;
    if (!read_both_copies(&a, &b)) {
        return false;
    }
    // Highest valid sequence wins; -1 means neither copy is valid (uninitialised).
    return meta_select_newest(&a, &b, out) >= 0;
}

bool hal_meta_write(const model_meta_t *m) {
    // Power-fail-atomic: write the NON-authoritative copy with seq+1. A crash mid-write
    // leaves that copy CRC-invalid, so hal_meta_read still returns the previous copy. The
    // active_slot flip becomes live only once the new copy's CRC lands — an atomic point.
    meta_blob_t a, b;
    if (!read_both_copies(&a, &b)) {
        return false;
    }
    uint32_t next_seq = 0;
    int target = meta_select_write_target(&a, &b, &next_seq);
    uint32_t off = (target == 0) ? META_FLASH_OFFSET_A : META_FLASH_OFFSET_B;

    meta_blob_t blob;
    memset(&blob, 0, sizeof(blob));
    blob.seq = next_seq;
    blob.meta = *m;
    meta_blob_finalize(&blob);  // stamps magic + crc

    if (cy_serial_flash_qspi_erase(off, sizeof(blob)) != CY_RSLT_SUCCESS) {
        return false;
    }
    return cy_serial_flash_qspi_write(off, sizeof(blob), (const uint8_t *)&blob)
           == CY_RSLT_SUCCESS;
}

// -----------------------------------------------------------------------------
//  Secure enclave / RRAM root-of-trust (PSA Crypto, TF-M / Mbed TLS backed)
//  psa_crypto_init() is expected to have run at boot.
//
//  CONTRACT NOTE: the device verifies a *detached raw ECDSA-P256 signature* over
//  the model's MANIFEST (which binds the flatbuffer by sha256 and carries the
//  contract). cosign's OCI signature is a different format and lives in the
//  registry; the build pipeline emits the detached manifest sig via `cosign
//  sign-blob` + der2raw.py. The flatbuffer is bound transitively via manifest.sha256.
// -----------------------------------------------------------------------------

bool hal_sha256(const uint8_t *data, uint32_t len, uint8_t out[32]) {
    size_t olen = 0;
    psa_status_t s = psa_hash_compute(PSA_ALG_SHA_256, data, len, out, 32, &olen);
    return s == PSA_SUCCESS && olen == 32;
}

bool hal_verify_signature(const uint8_t *data, uint32_t len, const uint8_t sig[64]) {
    uint8_t hash[32];
    if (!hal_sha256(data, len, hash)) {
        return false;
    }
    // Openless PSA API: reference the provisioned persistent key by its id directly. psa_open_key/
    // psa_close_key were removed from PSA Crypto 1.0 (kept only in the deprecated crypto_compat.h).
    // ECDSA-P256 over the SHA-256 digest; sig is the 64-byte r||s.
    psa_status_t s = psa_verify_hash(MODEL_SIGNING_KEY_ID, PSA_ALG_ECDSA(PSA_ALG_SHA_256),
                                     hash, sizeof(hash), sig, 64);
    return s == PSA_SUCCESS;
}

// -----------------------------------------------------------------------------
//  Device identity for NATS nkey auth (Ed25519) — the CRA unique-credential seed.
//  SEPARATE from the model-signing key above: that VERIFIES P-256 over the manifest;
//  this SIGNS the server nonce with the device's own Ed25519 seed to authenticate the
//  connection. The seed is provisioned per device (`make provision` in the dashboard
//  pipeline mints nats/creds/<container>.nk) and imported as a non-exportable PSA
//  persistent key at manufacturing; the matching public nkey is authorised in the
//  NATS server config. The seed never leaves protected storage.
// -----------------------------------------------------------------------------

// PSA persistent key id of the device Ed25519 nkey seed. VERIFY: the id you provision with
// (distinct from MODEL_SIGNING_KEY_ID); type ECC key-pair, family twisted-Edwards (Ed25519).
#define DEVICE_NKEY_KEY_ID ((psa_key_id_t)0x00000002u)

// The device's public nkey in NATS text form ("U..." base32+CRC). Provisioned alongside the
// seed. VERIFY: how it's stored — a PSA ITS blob or a per-device constant in protected storage.
// Encoding the 32-byte raw public key into "U..." on-device is also an option (base32 + crc16).
static const char DEVICE_NKEY_PUBLIC[] =
    "UAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";  // VERIFY: provisioned per device

bool hal_nkey_public(char *out, size_t cap) {
    size_t n = strlen(DEVICE_NKEY_PUBLIC);
    if (n + 1 > cap) return false;
    memcpy(out, DEVICE_NKEY_PUBLIC, n + 1);
    return true;
}

bool hal_nkey_sign(const uint8_t *nonce, size_t len, uint8_t sig[64]) {
    // Openless PSA API: reference the persistent seed by its id directly (no psa_open_key).
    // Pure Ed25519 signs the message (the nonce) directly — no caller-side pre-hash.
    // VERIFY: PSA_ALG_PURE_EDDSA + PSA_ECC_FAMILY_TWISTED_EDWARDS are enabled in the
    // TF-M / Mbed TLS crypto build for the E84 (PSA_WANT_ALG_PURE_EDDSA).
    size_t siglen = 0;
    psa_status_t s = psa_sign_message(DEVICE_NKEY_KEY_ID, PSA_ALG_PURE_EDDSA,
                                      nonce, len, sig, 64, &siglen);
    return s == PSA_SUCCESS && siglen == 64;
}

// -----------------------------------------------------------------------------
//  Network transport (AIROC CYW55513 Wi-Fi -> Secure Sockets over lwIP)
//  One long-lived NATS connection, so we keep a single static handle; the `int sock`
//  in the HAL signature is just a 0/-1 status. Secure-by-default: the connection is
//  server-auth TLS unless built with -DNATS_DISABLE_TLS (open demo / bring-up). The
//  client's identity is the nkey signature in CONNECT, not a client cert — so the
//  nats_proto wire bytes are identical with or without TLS; only the socket changes.
//  The CY_SOCKET_* TLS option ids below are cross-checked against Infineon's secure-sockets
//  API reference + the mtb-example-wifi-secure-tcp-client. VERIFY on-target: cy_wcm_connect_ap
//  struct, the boot-time TLS init (the example also calls cy_tls_load_global_root_ca_certificates
//  before connecting), and that this builds/handshakes against the E84 BSP.
// -----------------------------------------------------------------------------

#ifndef NATS_DISABLE_TLS
// PEM of the CA that signed the NATS server cert (the dashboard's `nats/tls/ca.pem`),
// provisioned into protected storage. VERIFY: where it lives (PSA ITS blob vs. a const
// in secure flash) and that it matches the deployment's broker cert.
static const char DEVICE_TLS_ROOT_CA[] =
    "-----BEGIN CERTIFICATE-----\n"
    "...provisioned per fleet (see `make provision`)...\n"
    "-----END CERTIFICATE-----\n";
// The name carried in the server cert's SAN; verified during the handshake so a wrong/MITM
// host is rejected. Matches the SANs provision.py mints (nats / localhost / 127.0.0.1).
#define NATS_TLS_SERVER_NAME "nats"
#endif

static cy_socket_t g_sock;
static bool        g_sock_open = false;

int hal_tcp_connect(const char *host, uint16_t port) {
    // Wi-Fi association is assumed done at boot via cy_wcm_init()+cy_wcm_connect_ap().
    // (Kept out of the per-connection path; do it once in device bring-up.)
#ifdef NATS_DISABLE_TLS
    const int proto = CY_SOCKET_IPPROTO_TCP;   // open demo only — plaintext
#else
    const int proto = CY_SOCKET_IPPROTO_TLS;   // secure-by-default — encrypted + server-verified
#endif
    if (cy_socket_create(CY_SOCKET_DOMAIN_AF_INET, CY_SOCKET_TYPE_STREAM,
                         proto, &g_sock) != CY_RSLT_SUCCESS) {
        return -1;
    }

#ifndef NATS_DISABLE_TLS
    // Pin the provisioned CA and require a successful verification: an unverifiable or
    // wrong-host server cert aborts the handshake — there is no plaintext fallback.
    // (Cert + hostname are passed as PEM/char buffers with strlen, per the Infineon
    // secure-sockets API; lengths are byte counts, not including a NUL.)
    // Alternative used by the shipping examples: load the root CA once at boot with
    // cy_tls_load_global_root_ca_certificates(DEVICE_TLS_ROOT_CA, strlen(DEVICE_TLS_ROOT_CA))
    // and skip the per-socket ROOTCA option below. Per-socket is used here to stay self-contained.
    cy_socket_tls_auth_mode_t mode = CY_SOCKET_TLS_VERIFY_REQUIRED;
    if (cy_socket_setsockopt(g_sock, CY_SOCKET_SOL_TLS,
                             CY_SOCKET_SO_TRUSTED_ROOTCA_CERTIFICATE,
                             DEVICE_TLS_ROOT_CA, strlen(DEVICE_TLS_ROOT_CA)) != CY_RSLT_SUCCESS ||
        cy_socket_setsockopt(g_sock, CY_SOCKET_SOL_TLS,
                             CY_SOCKET_SO_TLS_AUTH_MODE, &mode, sizeof(mode)) != CY_RSLT_SUCCESS ||
        cy_socket_setsockopt(g_sock, CY_SOCKET_SOL_TLS,
                             CY_SOCKET_SO_SERVER_NAME_INDICATION, NATS_TLS_SERVER_NAME,
                             strlen(NATS_TLS_SERVER_NAME)) != CY_RSLT_SUCCESS) {
        cy_socket_delete(g_sock);
        return -1;
    }
    // We deliberately do NOT set CY_SOCKET_SO_TLS_IDENTITY: this is server-auth TLS, and the
    // device authenticates with its nkey signature in CONNECT, not a TLS client cert.
#endif

    cy_socket_sockaddr_t addr = {0};
    addr.port = port;  // host byte order, per cy_socket_sockaddr_t
    addr.ip_address.version = CY_SOCKET_IP_VER_V4;
    // Minimal dotted-quad parse (no DNS for the demo). ip.v4 is documented as network byte
    // order: on the little-endian Cortex-M, packing a in the low byte gives bytes [a,b,c,d].
    unsigned a, b, c, d;
    if (sscanf(host, "%u.%u.%u.%u", &a, &b, &c, &d) != 4) {
        cy_socket_delete(g_sock);
        return -1;
    }
    addr.ip_address.ip.v4 = (uint32_t)(a | (b << 8) | (c << 16) | (d << 24));

    // For a TLS socket cy_socket_connect also runs the handshake, verifying the server
    // cert against the root CA pinned above before any NATS byte is exchanged.
    if (cy_socket_connect(g_sock, &addr, sizeof(addr)) != CY_RSLT_SUCCESS) {
        cy_socket_delete(g_sock);
        return -1;
    }
    g_sock_open = true;
    return 0;
}

int hal_tcp_send(int sock, const uint8_t *data, size_t len) {
    (void)sock;
    if (!g_sock_open) return -1;
    uint32_t sent = 0;
    if (cy_socket_send(g_sock, data, len, 0 /*flags*/, &sent) != CY_RSLT_SUCCESS) {
        return -1;
    }
    return (int)sent;
}

int hal_tcp_recv_exact(int sock, uint8_t *buf, size_t len) {
    (void)sock;
    if (!g_sock_open) return -1;
    size_t got = 0;
    while (got < len) {
        uint32_t n = 0;
        if (cy_socket_recv(g_sock, buf + got, len - got, 0, &n) != CY_RSLT_SUCCESS) {
            return -1;
        }
        if (n == 0) return -1;  // peer closed
        got += n;
    }
    return (int)got;
}

int hal_tcp_recv_line(int sock, char *buf, size_t cap) {
    (void)sock;
    if (!g_sock_open || cap == 0) return -1;
    // NATS lines are CRLF-terminated. Read one byte at a time until "\r\n".
    // (A buffered reader is faster; byte-at-a-time keeps the parsing obvious.)
    size_t i = 0;
    while (i < cap - 1) {
        uint8_t ch;
        uint32_t n = 0;
        if (cy_socket_recv(g_sock, &ch, 1, 0, &n) != CY_RSLT_SUCCESS) return -1;
        if (n == 0) return 0;  // timeout / closed
        if (ch == '\n' && i > 0 && buf[i - 1] == '\r') {
            buf[i - 1] = '\0';     // strip CRLF
            return (int)(i - 1);
        }
        buf[i++] = (char)ch;
    }
    buf[i] = '\0';
    return (int)i;  // line longer than cap; returned truncated
}
