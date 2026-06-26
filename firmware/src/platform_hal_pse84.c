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
#ifndef HAL_FLASH_STUB
#include "cy_smif.h"                // SMIF PDL low-level (Cy_SMIF_Init / SetMode / Enable)
#include "cy_smif_memslot.h"        // SMIF PDL memory-slot API (Cy_SMIF_Mem{Init,Read,Write,EraseSector})
#include "cycfg_qspi_memslot.h"     // QSPI Configurator block config (smifBlockConfig)
#endif
#include "psa/crypto.h"             // PSA Crypto (Mbed TLS / TF-M backed)
#include "cy_wcm.h"                 // Wi-Fi Connection Manager
#include "cy_wcm_error.h"
#include "cy_secure_sockets.h"      // Secure Sockets (over lwIP); TLS unless -DNATS_DISABLE_TLS
#include "mtb_hal.h"                // mtb_hal_sdio_t / mtb_hal_gpio_t used by WCM bring-up
#include "lwip/ip_addr.h"           // ip4addr_ntoa() for logging the assigned IP
#include "cyabs_rtos.h"             // cy_rtos_delay_milliseconds() for hal_sleep_ms

// PSA persistent key id of the model-signing public key, provisioned into the
// device's protected storage (RRAM-backed) at manufacturing. VERIFY: the id you
// provision with; the key is ECC P-256 public (verifies a detached ECDSA sig).
#define MODEL_SIGNING_KEY_ID ((psa_key_id_t)0x00000001u)

// The on-disk metadata layout (meta_blob_t: magic, seq, meta, crc) and the
// copy-selection logic live in meta_store.h/.c — pure, host-tested.

// -----------------------------------------------------------------------------
//  QSPI NOR flash (SMIF PDL memory-slot API)
//  hal_flash_init() brings up the SMIF controller and the NOR device from the
//  configurator-generated config (cycfg_qspi_memslot.h smifBlockConfig + the
//  CYBSP_SMIF_CORE_0_XSPI_FLASH_* controller config). The PSE84 (CAT1D) part ships
//  the new mtb-hal, not the legacy cyhal, so the cy_serial_flash_qspi middleware
//  (cyhal-only) cannot be used here — the PDL Cy_SMIF_Mem* layer it wrapped is.
//
//  HAL_FLASH_STUB: the connectivity-first firmware-app build defines this to get a
//  no-persistence backend — model slots aren't wired to QSPI yet. With the stub,
//  hal_flash_* fail benignly so an inbound Contract C deploy aborts cleanly instead
//  of corrupting anything, while the Wi-Fi/TLS/NATS publish path runs for real.
// -----------------------------------------------------------------------------

// Model-slot base offsets — shared by both backends (the stub uses them for its default metadata
// view, the real backend for the fresh-device default). The two slots must be at least
// MODEL_SLOT_BYTES (device_main.c) apart and must not overlap the metadata sectors below.
// VERIFY: against the QSPI Configurator / linker flash plan.
#define FLASH_SLOT_A_OFFSET (0x00000000u)
#define FLASH_SLOT_B_OFFSET (0x00100000u)  // 1 MB past slot A (matches MODEL_SLOT_BYTES)

#ifdef HAL_FLASH_STUB

bool hal_flash_init(void) {
    return true;   // no QSPI on the connectivity build — nothing to bring up
}
bool hal_flash_erase(uint32_t offset, uint32_t len) {
    (void)offset; (void)len;
    return false;  // no flash on the connectivity build -> deploy aborts gracefully
}
bool hal_flash_program(uint32_t offset, const uint8_t *data, uint32_t len) {
    (void)offset; (void)data; (void)len;
    return false;
}
const uint8_t *hal_flash_xip_map(uint32_t offset) {
    (void)offset;
    return NULL;
}
bool hal_meta_read(model_meta_t *out) {
    // Synthesize a minimal valid metadata view: slot A active, nothing else valid.
    // Lets model_loader_load_active(SLOT_A) and slot_inactive() behave on-target.
    memset(out, 0, sizeof(*out));
    out->active = SLOT_A;
    out->slot[SLOT_A].flash_offset = FLASH_SLOT_A_OFFSET;
    out->slot[SLOT_B].flash_offset = FLASH_SLOT_B_OFFSET;
    return true;
}
bool hal_meta_write(const model_meta_t *m) {
    (void)m;
    return true;  // accepted but not persisted
}

#else  // real QSPI-backed flash + power-fail-atomic metadata

// =============================================================================
//  Flash layout — these offsets come from your flash plan / linker, NOT guesses.
//  VERIFY: set to the actual reserved regions in the QSPI Configurator / .ld.
// =============================================================================
// Two metadata copies for power-fail-atomic updates (see meta_store), one sector each, placed
// AFTER both 1 MB model slots (FLASH_SLOT_A/B_OFFSET) so a slot erase never reaches them.
// VERIFY: two distinct reserved sectors in the QSPI Configurator / .ld.
#define META_FLASH_OFFSET_A (0x00200000u)
#define META_FLASH_OFFSET_B (0x00210000u)
// VERIFY: the memory-mapped base the SMIF exposes for XIP reads on the E84.
// On CAT1 parts this is the SMIF XIP region base (e.g. CY_XIP_BASE); confirm for E84.
// Must match npu_infer.c's SMIF_XIP_BASE — both cores read the same flash.
#define SMIF_XIP_BASE       (0x60000000u)
// VERIFY: SMIF controller init timeout (us) for the E84 board's flash part.
#define SMIF_INIT_TIMEOUT_US (1000UL)

// The SMIF instance and configs that carry the model/metadata QSPI NOR flash come from the
// Device/QSPI Configurator (the S25HS512T part on SMIF0 slot 0): CYBSP_SMIF_CORE_0_XSPI_FLASH_HW
// is the SMIF base, CYBSP_SMIF_CORE_0_XSPI_FLASH_config the controller config, and smifBlockConfig
// the memory-slot block config (from cycfg_qspi_memslot.h).
#define FLASH_SMIF_HW  CYBSP_SMIF_CORE_0_XSPI_FLASH_HW
#define FLASH_SMIF_CFG (&CYBSP_SMIF_CORE_0_XSPI_FLASH_config)

static cy_stc_smif_context_t g_smif_ctx;

// The single configured NOR device. The Cy_SMIF_Mem* ops take the per-device config; the block
// config carries the array the Configurator generated (one device on this board).
static cy_stc_smif_mem_config_t *flash_mem(void) {
    return smifBlockConfig.memConfig[0];
}

// Command (MMIO) mode is required for Cy_SMIF_Mem{Read,Write,EraseSector}; XIP (memory-mapped) mode
// is required for the pointer hal_flash_xip_map hands back. XIP is the resting mode so those
// pointers stay valid between calls; each command-mode op brackets itself and restores XIP. This is
// the QSPI *data* flash — the NS image runs from RRAM, so toggling SMIF mode never pulls code out
// from under us. (The legacy serial-flash middleware did this mode dance internally.)
static void flash_cmd_mode(void) { Cy_SMIF_SetMode(FLASH_SMIF_HW, CY_SMIF_NORMAL); }
static void flash_xip_mode(void) { Cy_SMIF_SetMode(FLASH_SMIF_HW, CY_SMIF_MEMORY); }

bool hal_flash_init(void) {
    // Bring up the SMIF controller from the generated config, init the NOR device(s) from the QSPI
    // Configurator block config, then leave the block memory-mapped (XIP) so hal_flash_xip_map just
    // returns an address.
    // VERIFY on-target: that the SMIF controller isn't already brought up by cybsp_init() (a second
    // Cy_SMIF_Init would fault); the init timeout; and the slot-0 data/slave-select wiring.
    if (Cy_SMIF_Init(FLASH_SMIF_HW, FLASH_SMIF_CFG, SMIF_INIT_TIMEOUT_US, &g_smif_ctx)
            != CY_SMIF_SUCCESS) {
        return false;
    }
    Cy_SMIF_Enable(FLASH_SMIF_HW, &g_smif_ctx);
    if (Cy_SMIF_MemInit(FLASH_SMIF_HW, &smifBlockConfig, &g_smif_ctx) != CY_SMIF_SUCCESS) {
        return false;
    }
    flash_xip_mode();
    return true;
}

bool hal_flash_erase(uint32_t offset, uint32_t len) {
    // Cy_SMIF_MemEraseSector needs command mode and a sector-aligned offset + sector-multiple
    // length; the caller (hal_meta_write) aligns to the device sector size.
    flash_cmd_mode();
    cy_en_smif_status_t s = Cy_SMIF_MemEraseSector(FLASH_SMIF_HW, flash_mem(), offset, len,
                                                   &g_smif_ctx);
    flash_xip_mode();
    return s == CY_SMIF_SUCCESS;
}

bool hal_flash_program(uint32_t offset, const uint8_t *data, uint32_t len) {
    // Cy_SMIF_MemWrite handles page-program splitting and write-enable / WIP polling internally.
    flash_cmd_mode();
    cy_en_smif_status_t s = Cy_SMIF_MemWrite(FLASH_SMIF_HW, flash_mem(), offset, data, len,
                                             &g_smif_ctx);
    flash_xip_mode();
    return s == CY_SMIF_SUCCESS;
}

const uint8_t *hal_flash_xip_map(uint32_t offset) {
    // XIP (memory-mapped) mode is the resting mode (hal_flash_init + the erase/program brackets
    // restore it), so the flatbuffer is directly addressable here — just return its address.
    // VERIFY: SMIF_XIP_BASE for the E84 matches the configured baseAddress for slot 0.
    return (const uint8_t *)(SMIF_XIP_BASE + offset);
}

static bool read_both_copies(meta_blob_t *a, meta_blob_t *b) {
    // Cy_SMIF_MemRead is a command-mode read; bracket back to XIP so xip_map pointers stay valid.
    flash_cmd_mode();
    cy_en_smif_status_t sa = Cy_SMIF_MemRead(FLASH_SMIF_HW, flash_mem(), META_FLASH_OFFSET_A,
                                             (uint8_t *)a, sizeof(*a), &g_smif_ctx);
    cy_en_smif_status_t sb = Cy_SMIF_MemRead(FLASH_SMIF_HW, flash_mem(), META_FLASH_OFFSET_B,
                                             (uint8_t *)b, sizeof(*b), &g_smif_ctx);
    flash_xip_mode();
    return sa == CY_SMIF_SUCCESS && sb == CY_SMIF_SUCCESS;
}

bool hal_meta_read(model_meta_t *out) {
    meta_blob_t a, b;
    if (!read_both_copies(&a, &b)) {
        return false;
    }
    // Highest valid sequence wins. -1 means neither copy is valid: uninitialised flash on a fresh /
    // pre-commission device. Synthesize an empty layout (slot A active, no flash model) so the
    // device still boots — CM55 runs its baked model and the first deploy initialises the metadata.
    if (meta_select_newest(&a, &b, out) < 0) {
        memset(out, 0, sizeof(*out));
        out->active = SLOT_A;
        out->slot[SLOT_A].flash_offset = FLASH_SLOT_A_OFFSET;
        out->slot[SLOT_B].flash_offset = FLASH_SLOT_B_OFFSET;
        // len = 0, valid = false -> model_loader treats both slots as "no flash model".
    }
    return true;
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

    // hal_flash_erase requires a sector-aligned offset and a sector-multiple length, so erase the
    // whole metadata sector (the blob fits within one). The device sector size comes from the
    // configurator block config. VERIFY: sizeof(meta_blob_t) does not exceed the device sector size
    // at this offset, and that META_FLASH_OFFSET_A/B are sector-aligned.
    size_t sector = flash_mem()->deviceCfg->eraseSize;
    if (sector == 0 || sizeof(blob) > sector || !hal_flash_erase(off, sector)) {
        return false;
    }
    return hal_flash_program(off, (const uint8_t *)&blob, sizeof(blob));
}

#endif  // HAL_FLASH_STUB

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
//  Wi-Fi bring-up (AIROC CYW55513 over SDIO + Wi-Fi Connection Manager)
//  Brings the radio up and associates to the AP ONCE at boot, before any socket.
//  Mirrors the documented flow in mtb-example-wifi-secure-tcp-client / the PSOC
//  Edge Wi-Fi examples: app_sdio_init() (SDIO transport to the radio) then
//  cy_wcm_init(STA) + cy_wcm_connect_ap(). VERIFY on-target: the CYBSP_WIFI_*
//  symbols come from the Device Configurator (present in the Wi-Fi-enabled BSP),
//  the SDIO frequency/block-size, and the deep-sleep callback wiring.
// -----------------------------------------------------------------------------

// VERIFY: provision per fleet. In production these come from secure storage (the
// dashboard's `make provision`), not a compiled-in constant — kept here as the
// seam, matching how the model-signing key / nkey seed are provisioned.
#ifndef WIFI_SSID
#define WIFI_SSID            "MY_WIFI_SSID"
#endif
#ifndef WIFI_PASSWORD
#define WIFI_PASSWORD        "MY_WIFI_PASSWORD"
#endif
#define WIFI_SECURITY_TYPE   CY_WCM_SECURITY_WPA2_AES_PSK
#define WIFI_MAX_RETRY       (3u)

#define APP_SDIO_INTERRUPT_PRIORITY        (7u)
#define APP_HOST_WAKE_INTERRUPT_PRIORITY   (2u)
#define APP_SDIO_FREQUENCY_HZ              (25000000u)
#define SDHC_SDIO_64BYTES_BLOCK            (64u)

static mtb_hal_sdio_t        g_sdio;
static cy_stc_sd_host_context_t g_sdhc_ctx;
static cy_wcm_config_t       g_wcm_config;

static void sdio_interrupt_handler(void) {
    mtb_hal_sdio_process_interrupt(&g_sdio);
}
static void host_wake_interrupt_handler(void) {
    mtb_hal_gpio_process_interrupt(&g_wcm_config.wifi_host_wake_pin);
}

// Wire the SDIO bus that carries 802.11 traffic between the host MCU and the radio.
// Returns false on any setup failure (caller aborts bring-up).
static bool app_sdio_init(void) {
    cy_stc_sysint_t sdio_intr_cfg = {
        .intrSrc = CYBSP_WIFI_SDIO_IRQ, .intrPriority = APP_SDIO_INTERRUPT_PRIORITY,
    };
    cy_stc_sysint_t host_wake_intr_cfg = {
        .intrSrc = CYBSP_WIFI_HOST_WAKE_IRQ, .intrPriority = APP_HOST_WAKE_INTERRUPT_PRIORITY,
    };

    if (Cy_SysInt_Init(&sdio_intr_cfg, sdio_interrupt_handler) != CY_SYSINT_SUCCESS) {
        return false;
    }
    NVIC_EnableIRQ(CYBSP_WIFI_SDIO_IRQ);

    if (mtb_hal_sdio_setup(&g_sdio, &CYBSP_WIFI_SDIO_sdio_hal_config, NULL, &g_sdhc_ctx)
            != CY_RSLT_SUCCESS) {
        return false;
    }

    Cy_SD_Host_Enable(CYBSP_WIFI_SDIO_HW);
    Cy_SD_Host_Init(CYBSP_WIFI_SDIO_HW, CYBSP_WIFI_SDIO_sdio_hal_config.host_config, &g_sdhc_ctx);
    Cy_SD_Host_SetHostBusWidth(CYBSP_WIFI_SDIO_HW, CY_SD_HOST_BUS_WIDTH_4_BIT);

    mtb_hal_sdio_cfg_t sdio_hal_cfg = {
        .frequencyhal_hz = APP_SDIO_FREQUENCY_HZ, .block_size = SDHC_SDIO_64BYTES_BLOCK,
    };
    mtb_hal_sdio_configure(&g_sdio, &sdio_hal_cfg);

    // WL_REG_ON powers the radio; HOST_WAKE lets it wake the host out of sleep.
    mtb_hal_gpio_setup(&g_wcm_config.wifi_wl_pin, CYBSP_WIFI_WL_REG_ON_PORT_NUM,
                       CYBSP_WIFI_WL_REG_ON_PIN);
    mtb_hal_gpio_setup(&g_wcm_config.wifi_host_wake_pin, CYBSP_WIFI_HOST_WAKE_PORT_NUM,
                       CYBSP_WIFI_HOST_WAKE_PIN);

    if (Cy_SysInt_Init(&host_wake_intr_cfg, host_wake_interrupt_handler) != CY_SYSINT_SUCCESS) {
        return false;
    }
    NVIC_EnableIRQ(CYBSP_WIFI_HOST_WAKE_IRQ);
    return true;
}

void hal_sleep_ms(uint32_t ms) {
    cy_rtos_delay_milliseconds(ms);
}

bool hal_net_init(void) {
    if (!app_sdio_init()) {
        return false;
    }

    g_wcm_config.interface = CY_WCM_INTERFACE_TYPE_STA;
    g_wcm_config.wifi_interface_instance = &g_sdio;
    if (cy_wcm_init(&g_wcm_config) != CY_RSLT_SUCCESS) {
        return false;
    }

    cy_wcm_connect_params_t connect_param = {0};
    memcpy(connect_param.ap_credentials.SSID, WIFI_SSID, sizeof(WIFI_SSID));
    memcpy(connect_param.ap_credentials.password, WIFI_PASSWORD, sizeof(WIFI_PASSWORD));
    connect_param.ap_credentials.security = WIFI_SECURITY_TYPE;

    cy_wcm_ip_address_t ip_addr;
    bool associated = false;
    printf("[wifi] joining SSID '%s'...\n", WIFI_SSID);
    for (uint32_t attempt = 0; attempt < WIFI_MAX_RETRY; attempt++) {
        if (cy_wcm_connect_ap(&connect_param, &ip_addr) == CY_RSLT_SUCCESS) {
            associated = true;   // associated + DHCP lease in hand
            if (ip_addr.version == CY_WCM_IP_VER_V4) {
                printf("[wifi] joined '%s', IP %s\n", WIFI_SSID,
                       ip4addr_ntoa((const ip4_addr_t *)&ip_addr.ip.v4));
            } else {
                printf("[wifi] joined '%s' (IPv6)\n", WIFI_SSID);
            }
            break;
        }
        printf("[wifi] attempt %lu failed, retrying...\n", (unsigned long)(attempt + 1));
    }
    if (!associated) {
        printf("[wifi] FAILED to join '%s' after %u attempts\n", WIFI_SSID, WIFI_MAX_RETRY);
        return false;      // no link after all retries — caller must not open sockets
    }

    // Bring up the secure-sockets layer once the interface is up. The raw cy_socket_*
    // API (used by hal_tcp_*) requires this; the example reached it via the http-client
    // wrapper, which called it internally.
    return cy_socket_init() == CY_RSLT_SUCCESS;
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

// Receive timeout (ms) on the NATS socket. The steady-state loop interleaves blocking line-reads
// with publishing CM55 scores on a single task, so recv MUST return periodically even on a quiet
// socket — otherwise the publish path stalls until the next server PING. recv_line/recv_exact treat
// this timeout as "no data yet" (not an error), so it bounds latency without breaking either read.
#define NATS_RECV_TIMEOUT_MS 200u

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

    // Bound recv() so the single-task loop can't block past the next score window on a quiet
    // socket. VERIFY: CY_SOCKET_SO_RCVTIMEO takes a uint32_t milliseconds on the E84 secure-sockets.
    uint32_t rx_timeout_ms = NATS_RECV_TIMEOUT_MS;
    if (cy_socket_setsockopt(g_sock, CY_SOCKET_SOL_SOCKET, CY_SOCKET_SO_RCVTIMEO,
                             &rx_timeout_ms, sizeof(rx_timeout_ms)) != CY_RSLT_SUCCESS) {
        cy_socket_delete(g_sock);
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
    // Accept either a dotted-quad (LAN edge node) or a DNS hostname (cloud broker).
    // Try the literal parse first; if it isn't four octets, resolve via the stack's
    // DNS. ip.v4 is documented as network byte order: on the little-endian Cortex-M,
    // packing `a` in the low byte gives bytes [a,b,c,d].
    unsigned a, b, c, d;
    printf("[net] connecting to %s:%u\n", host, (unsigned)port);
    if (sscanf(host, "%u.%u.%u.%u", &a, &b, &c, &d) == 4) {
        addr.ip_address.ip.v4 = (uint32_t)(a | (b << 8) | (c << 16) | (d << 24));
    } else {
        cy_rslt_t dr = cy_socket_gethostbyname(host, CY_SOCKET_IP_VER_V4, &addr.ip_address);
        if (dr != CY_RSLT_SUCCESS) {
            printf("[net] DNS resolve of '%s' FAILED rc=0x%08lx\n", host, (unsigned long)dr);
            cy_socket_delete(g_sock);
            return -1;  // unresolvable host
        }
    }
    {
        uint32_t v4 = addr.ip_address.ip.v4;  // network byte order: bytes [a,b,c,d]
        printf("[net] resolved %s -> %u.%u.%u.%u\n", host,
               (unsigned)(v4 & 0xff), (unsigned)((v4 >> 8) & 0xff),
               (unsigned)((v4 >> 16) & 0xff), (unsigned)((v4 >> 24) & 0xff));
    }

    // For a TLS socket cy_socket_connect also runs the handshake, verifying the server
    // cert against the root CA pinned above before any NATS byte is exchanged.
    cy_rslt_t cr = cy_socket_connect(g_sock, &addr, sizeof(addr));
    if (cr != CY_RSLT_SUCCESS) {
        printf("[net] connect to %s:%u FAILED rc=0x%08lx\n", host, (unsigned)port, (unsigned long)cr);
        cy_socket_delete(g_sock);
        return -1;
    }
    printf("[net] connected to %s:%u\n", host, (unsigned)port);
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
        // VERIFY: CY_RSLT_MODULE_SECURE_SOCKETS_TIMEOUT is the rc cy_socket_recv returns when the
        // SO_RCVTIMEO window elapses with no data. A MSG payload is already committed by its header,
        // so a timeout mid-body just means "more is coming" — keep waiting rather than abort.
        cy_rslt_t rc = cy_socket_recv(g_sock, buf + got, len - got, 0, &n);
        if (rc == CY_RSLT_MODULE_SECURE_SOCKETS_TIMEOUT) {
            continue;
        }
        if (rc != CY_RSLT_SUCCESS) return -1;
        if (n == 0) return -1;  // peer closed
        got += n;
    }
    return (int)got;
}

void hal_tcp_close(int sock) {
    (void)sock;
    if (g_sock_open) {
        cy_socket_disconnect(g_sock, 0);
        cy_socket_delete(g_sock);
        g_sock_open = false;
    }
}

// On-device track localization. STUB: the figure-8 lap segmentation (wear_detector/localize.py)
// is not ported on-target yet, so position is always "unknown" and Contract E capture falls back
// to its time-bounded / safety-cap behaviour. When the localizer lands, fill `out` with the
// current segment id (and return true) so segment-gated capture records exactly one pass.
bool hal_track_segment(char *out, size_t cap) {
    if (cap > 0) out[0] = '\0';
    return false;  // VERIFY: wire to the on-target localizer when available
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
        cy_rslt_t rc = cy_socket_recv(g_sock, &ch, 1, 0, &n);
        if (rc == CY_RSLT_MODULE_SECURE_SOCKETS_TIMEOUT) {
            // No byte within the recv window. At a line boundary (i == 0) that's just a quiet
            // socket — return 0 so the caller can publish a score and poll again. Mid-line, keep
            // waiting: returning here would drop the bytes already consumed and desync the stream.
            if (i == 0) return 0;
            continue;
        }
        if (rc != CY_RSLT_SUCCESS) return -1;  // genuine transport error -> caller reconnects
        if (n == 0) return -1;                 // peer closed -> caller reconnects
        if (ch == '\n' && i > 0 && buf[i - 1] == '\r') {
            buf[i - 1] = '\0';     // strip CRLF
            return (int)(i - 1);
        }
        buf[i++] = (char)ch;
    }
    buf[i] = '\0';
    return (int)i;  // line longer than cap; returned truncated
}
