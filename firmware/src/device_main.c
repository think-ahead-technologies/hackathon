// ABOUTME: Device firmware orchestration — publish Contract B, handle Contract C deploys with
// ABOUTME: verify + shadow + A/B promote/rollback. BUILT ON-TARGET ONLY (uses HAL + TFLM loader).

// This is the skeleton that stitches the tested pure logic (src/model_slot, model_contract,
// shadow, nats_proto) to the hardware via platform_hal.h. The numbered comments map to the
// "Update flow on-device" steps in model-pipeline.md Part 2. Transport details left as TODOs
// are deliberately not faked — wire them to the real BSP.

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "deploy.h"
#include "manifest.h"
#include "model_contract.h"
#include "model_loader.h"
#include "model_slot.h"
#include "nats_proto.h"
#include "platform_hal.h"
#include "shadow.h"

// ---- demo configuration -----------------------------------------------------
// NATS_HOST/NATS_PORT are build-overridable (e.g. `make build NATS_HOST=10.0.0.5`); the
// default is the edge-node demo broker. Host may be a dotted-quad (LAN) or a DNS name (cloud).
#ifndef NATS_HOST
#define NATS_HOST   "wallnats.ganter.dev"
#endif
#ifndef NATS_PORT
#define NATS_PORT   4222
#endif
// Connect retry: tolerate DNS/network not being ready immediately after Wi-Fi association.
#define NATS_CONNECT_RETRIES  15
#define NATS_CONNECT_RETRY_MS 2000
#define LINE        "line1"
#define CONTAINER   "cnc-7"
#define PUB_SUBJECT "edge." LINE "." CONTAINER          // through the Vector boundary
// Contract C artifact stream (chunked frames). Distinct from models.<line>.deploy, which carries
// the JSON deploy *event* for the dashboard — this subject carries the model *bytes* for devices.
#define DEPLOY_SUB  "models." LINE ".artifact"

// What this firmware build can accommodate (must match the baked-in arena + pre-processing).
static const firmware_contract_t FW = {
    .expected_input_shape = {1, 49, 40, 1},
    .expected_input_dtype = "int8",
    .arena_capacity = TENSOR_ARENA_BYTES,
};

static const shadow_policy_t SHADOW = {
    .min_windows = 20,
    .max_mean_abs_delta = 0.05f,
    .max_single_abs_delta = 0.30f,
};

// Shadow state lives across windows while a candidate is being trialled.
static bool          g_shadowing = false;
static slot_id_t     g_candidate_slot;
static shadow_stats_t g_shadow;

// ---- Contract C deploy session ---------------------------------------------
#define DEPLOY_MANIFEST_MAX 1024u
#define MODEL_SLOT_BYTES    (1024u * 1024u)  // VERIFY: reserved per-slot flash size (>= worst case)

// A deploy arrives as MANIFEST + SIG frames, then the MODEL flatbuffer streamed in chunks. We
// buffer the small manifest + sig, validate them, then stream model chunks straight to the
// inactive flash slot (the flatbuffer is too big to hold in RAM).
static struct {
    uint8_t     manifest[DEPLOY_MANIFEST_MAX];
    uint32_t    manifest_len;
    bool        manifest_done;
    uint8_t     sig[64];
    bool        sig_done;
    bool        prepared;    // sig + contract verified, slot erased, ready to stream MODEL
    slot_id_t   target;
    uint32_t    target_off;
    uint8_t     want_sha[32];
    deploy_rx_t rx;
} g_dep;

static void deploy_session_reset(void) {
    memset(&g_dep, 0, sizeof(g_dep));
    deploy_rx_reset(&g_dep.rx);
}

// Publish one Contract B inference result.
static void publish_score(int sock, float score) {
    char body[160];
    int blen = snprintf(body, sizeof(body),
                        "{\"ts\":\"\",\"container_id\":\"" CONTAINER "\","
                        "\"anomaly_score\":%.4f,\"data_classification\":\"inference\","
                        "\"bytes\":0}",
                        (double)score);
    char frame[256];
    int flen = nats_build_pub(frame, sizeof(frame), PUB_SUBJECT,
                              (const uint8_t *)body, (size_t)blen);
    if (flen > 0) hal_tcp_send(sock, (const uint8_t *)frame, (size_t)flen);
}

// (3a + 4) Once the manifest and sig are in: authenticate the MANIFEST against the hardware root
// of trust, parse the contract it carries, validate it against our fixed pre-processing, and
// erase the inactive slot ready to receive the model. Verifying the manifest (not the raw
// flatbuffer) means the signature also covers the contract, not just opaque bytes.
static bool deploy_prepare(void) {
    if (!hal_verify_signature(g_dep.manifest, g_dep.manifest_len, g_dep.sig)) return false;

    model_contract_t contract;
    if (!parse_manifest(g_dep.manifest, g_dep.manifest_len, &contract, g_dep.want_sha)) return false;

    contract_result_t cr = contract_validate(&contract, &FW);
    if (cr != CONTRACT_OK) {
        printf("[deploy] refused: %s\n", contract_result_str(cr));  // the refusal is the safety beat
        return false;
    }

    model_meta_t meta;
    if (!hal_meta_read(&meta)) return false;
    g_dep.target = slot_inactive(meta.active);          // always the INACTIVE slot
    g_dep.target_off = meta.slot[g_dep.target].flash_offset;
    return hal_flash_erase(g_dep.target_off, MODEL_SLOT_BYTES);  // (2) prepare the slot
}

// (3b + 7) The whole model has streamed in. Bind the written bytes to the trusted manifest by
// digest, record the slot valid (atomic metadata write), load it as candidate, start shadowing.
static void deploy_finalize(void) {
    const uint8_t *p = hal_flash_xip_map(g_dep.target_off);
    uint8_t got[32];
    if (p == NULL || !hal_sha256(p, g_dep.rx.total, got) ||
        memcmp(got, g_dep.want_sha, 32) != 0) {
        deploy_session_reset();  // bytes don't match the signed manifest — discard
        return;
    }

    model_meta_t meta;
    if (!hal_meta_read(&meta)) {
        deploy_session_reset();
        return;
    }
    meta.slot[g_dep.target].len = g_dep.rx.total;
    memcpy(meta.slot[g_dep.target].sha256, got, 32);
    memcpy(meta.slot[g_dep.target].sig, g_dep.sig, 64);
    meta.slot[g_dep.target].valid = true;  // written + verified, but NOT yet active
    if (hal_meta_write(&meta) && model_loader_load_candidate(g_dep.target)) {
        g_candidate_slot = g_dep.target;
        g_shadowing = true;
        shadow_reset(&g_shadow);
        printf("[deploy] candidate in slot %d, shadowing\n", g_dep.target);
    }
    deploy_session_reset();
}

// Stream one MODEL chunk to the inactive slot. Manifest + sig must have arrived and validated
// first; the reassembler enforces contiguous, in-range writes. Any violation aborts the deploy.
static void deploy_on_model_chunk(const deploy_hdr_t *h, const uint8_t *payload) {
    if (!g_dep.prepared) {
        if (!(g_dep.manifest_done && g_dep.sig_done)) return;  // wait for manifest + sig
        if (!deploy_prepare()) {
            deploy_session_reset();
            return;
        }
        g_dep.prepared = true;
    }
    if (!deploy_rx_accept(&g_dep.rx, h, MODEL_SLOT_BYTES) ||
        !hal_flash_program(g_dep.target_off + h->offset, payload, h->chunk_len)) {
        deploy_session_reset();
        return;
    }
    if (deploy_rx_complete(&g_dep.rx)) {
        deploy_finalize();
    }
}

// Route one parsed Contract C frame to the right part accumulator.
static void deploy_on_frame(const deploy_hdr_t *h, const uint8_t *payload) {
    switch (h->part) {
        case DEPLOY_PART_MANIFEST:
            if ((size_t)h->offset + h->chunk_len <= sizeof(g_dep.manifest)) {
                memcpy(g_dep.manifest + h->offset, payload, h->chunk_len);
                if (h->flags & DEPLOY_FLAG_LAST) {
                    g_dep.manifest_len = h->offset + h->chunk_len;
                    g_dep.manifest_done = true;
                }
            }
            break;
        case DEPLOY_PART_SIG:
            if (h->chunk_len == 64) {
                memcpy(g_dep.sig, payload, 64);
                g_dep.sig_done = true;
            }
            break;
        case DEPLOY_PART_MODEL:
            deploy_on_model_chunk(h, payload);
            break;
        default:
            break;
    }
}

// (8) After each shadowed window, decide whether to promote or roll back.
static void evaluate_shadow(float active_score, float candidate_score) {
    shadow_observe(&g_shadow, active_score, candidate_score);
    switch (shadow_decide(&g_shadow, &SHADOW)) {
        case SHADOW_PENDING:
            return;
        case SHADOW_PROMOTE: {
            model_meta_t meta;
            if (hal_meta_read(&meta) && meta_promote(&meta, g_candidate_slot) &&
                hal_meta_write(&meta) && model_loader_load_active(g_candidate_slot)) {
                printf("[shadow] promoted slot %d\n", g_candidate_slot);
            }
            break;
        }
        case SHADOW_ROLLBACK:
            // Leave active_slot untouched; just discard the candidate. (meta_rollback() is
            // for recovering an already-promoted model that later misbehaves.)
            printf("[shadow] rolled back; keeping incumbent\n");
            break;
    }
    model_loader_clear_candidate();
    g_shadowing = false;
}

// Entry point for the device orchestration loop. On a bare-metal host this is the
// program; under ModusToolbox the board main() (proj_cm33_ns) brings up the BSP and
// runs this as a FreeRTOS task (see firmware-app/). It only returns on a fatal init
// failure — the steady state is the infinite publish/deploy loop below.
int device_main(void) {
    if (!hal_net_init()) return 1;                    // bring Wi-Fi up before any socket
    if (!model_loader_load_active(SLOT_A)) return 1;  // baked-in / last-good model

    // Retry the broker connect: right after Wi-Fi associates, DNS / the network may not be
    // ready for a beat, and a single miss would otherwise strand the device until reset.
    int sock = -1;
    for (int attempt = 1; attempt <= NATS_CONNECT_RETRIES; attempt++) {
        sock = hal_tcp_connect(NATS_HOST, NATS_PORT);
        if (sock >= 0) break;
        printf("[net] connect attempt %d/%d failed; retrying in %d ms\n",
               attempt, NATS_CONNECT_RETRIES, NATS_CONNECT_RETRY_MS);
        hal_sleep_ms(NATS_CONNECT_RETRY_MS);
    }
    if (sock < 0) return 1;

    char line[256];
    hal_tcp_recv_line(sock, line, sizeof(line));      // INFO (carries a nonce when auth is on)

    // If the server sent a nonce it wants nkey auth: present this device's public nkey and a
    // signature over the nonce. No nonce -> connect anonymously (open demo fabric).
    char connect[512];
    int cn;
    char nonce[128];
    if (nats_parse_info_nonce(line, nonce, sizeof(nonce))) {
        char nkey[64];
        uint8_t sig[64];
        char sigb64[128];
        if (!hal_nkey_public(nkey, sizeof(nkey)) ||
            !hal_nkey_sign((const uint8_t *)nonce, strlen(nonce), sig) ||
            nats_b64_encode(sigb64, sizeof(sigb64), sig, sizeof(sig)) < 0) {
            return 1;  // cannot authenticate -> do not join the fabric
        }
        cn = nats_build_connect(connect, sizeof(connect), CONTAINER, nkey, sigb64);
    } else {
        cn = nats_build_connect(connect, sizeof(connect), CONTAINER, NULL, NULL);
    }
    if (cn < 0) return 1;
    hal_tcp_send(sock, (const uint8_t *)connect, (size_t)cn);

    // Subscribe to Contract C deploys for this line.
    char sub[64];
    int sn = snprintf(sub, sizeof(sub), "SUB %s 1\r\n", DEPLOY_SUB);
    hal_tcp_send(sock, (const uint8_t *)sub, (size_t)sn);

    for (;;) {
        // Drain any pending protocol lines (keepalive + inbound deploys).
        int n = hal_tcp_recv_line(sock, line, sizeof(line));
        if (n > 0) {
            switch (nats_line_kind(line)) {
                case NATS_LINE_PING:
                    hal_tcp_send(sock, (const uint8_t *)"PONG\r\n", 6);
                    break;
                case NATS_LINE_MSG: {
                    nats_msg_t msg;
                    if (nats_parse_msg_header(line, &msg) &&
                        strcmp(msg.subject, DEPLOY_SUB) == 0) {
                        // The MSG payload is one Contract C frame (header + chunk). Read it, then
                        // parse + route. One NATS message carries at most a header + one chunk.
                        static uint8_t frame[DEPLOY_HDR_BYTES + 4096];
                        deploy_hdr_t h;
                        if (msg.payload_len <= sizeof(frame) &&
                            hal_tcp_recv_exact(sock, frame, msg.payload_len) == (int)msg.payload_len &&
                            deploy_parse_header(frame, msg.payload_len, &h)) {
                            deploy_on_frame(&h, frame + DEPLOY_HDR_BYTES);
                        }
                    }
                    break;
                }
                default:
                    break;
            }
        }

        // One sensor window -> features -> inference -> publish.
        int8_t features[49 * 40];   // TODO(bsp): fill from the FFT/RMS feature extractor
        memset(features, 0, sizeof(features));
        float candidate_score = 0.0f;
        bool have_candidate = false;
        float score = model_loader_infer(features, sizeof(features),
                                         &candidate_score, &have_candidate);
        publish_score(sock, score);

        if (g_shadowing && have_candidate) {
            evaluate_shadow(score, candidate_score);
        }
    }
}
