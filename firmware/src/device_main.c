// ABOUTME: Device firmware orchestration — publish Contract B, handle Contract C deploys with
// ABOUTME: verify + shadow + A/B promote/rollback. BUILT ON-TARGET ONLY (uses HAL + TFLM loader).

// This is the skeleton that stitches the tested pure logic (src/model_slot, model_contract,
// shadow, nats_proto) to the hardware via platform_hal.h. The numbered comments map to the
// "Update flow on-device" steps in model-pipeline.md Part 2. Transport details left as TODOs
// are deliberately not faked — wire them to the real BSP.

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "capture.h"
#include "deploy.h"
#include "manifest.h"
#include "model_contract.h"
#include "model_loader.h"
#include "model_slot.h"
#include "nats_proto.h"
#include "platform_hal.h"
#include "shared_score.h"
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
// Contract E directed-gather: the platform commands a capture on .cmd; the device streams the
// gathered feature windows back on .data. The .data sink is deliberately OFF edge.> — the Vector
// minimization boundary blocks raw/features there; operator-gated training capture is the
// sanctioned exception and rides its own subject (see model-pipeline.md / bridge POST /capture).
#define CAPTURE_SUB          "capture." LINE "." CONTAINER ".cmd"
#define CAPTURE_DATA_SUBJECT "capture." LINE "." CONTAINER ".data"

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

// Contract E capture watch-set (segments the platform asked us to record). Pure, host-tested.
static capture_set_t g_capture;

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

// Publish one gathered feature window to the capture sink, tagged with the command metadata so
// the collector can bin it (clean baseline vs. failure segment) for retraining. The int8 features
// are base64-encoded into the JSON envelope. Buffers are static — the b64 of a full window plus
// the envelope is a few KB, too large for the stack on this loop.
static void publish_capture_window(int sock, const int8_t *feat, size_t n,
                                   const capture_entry_t *e, uint32_t seq) {
    static char b64[((49 * 40 + 2) / 3) * 4 + 4];
    if (nats_b64_encode(b64, sizeof(b64), (const uint8_t *)feat, n) < 0) return;

    static char body[sizeof(b64) + 256];
    int blen = snprintf(body, sizeof(body),
                        "{\"request_id\":\"%s\",\"label\":\"%s\",\"segment\":\"%s\","
                        "\"seq\":%u,\"container_id\":\"" CONTAINER "\","
                        "\"data_classification\":\"capture\",\"features_b64\":\"%s\"}",
                        e->request_id, e->label, e->segment, seq, b64);
    if (blen < 0 || (size_t)blen >= sizeof(body)) return;

    static char frame[sizeof(body) + 128];
    int flen = nats_build_pub(frame, sizeof(frame), CAPTURE_DATA_SUBJECT,
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

    // Subscribe to Contract C deploys (sid 1) and Contract E capture commands (sid 2).
    char sub[128];
    int sn = snprintf(sub, sizeof(sub), "SUB %s 1\r\nSUB %s 2\r\n", DEPLOY_SUB, CAPTURE_SUB);
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
                    if (!nats_parse_msg_header(line, &msg)) break;
                    if (strcmp(msg.subject, DEPLOY_SUB) == 0) {
                        // The MSG payload is one Contract C frame (header + chunk). Read it, then
                        // parse + route. One NATS message carries at most a header + one chunk.
                        static uint8_t frame[DEPLOY_HDR_BYTES + 4096];
                        deploy_hdr_t h;
                        if (msg.payload_len <= sizeof(frame) &&
                            hal_tcp_recv_exact(sock, frame, msg.payload_len) == (int)msg.payload_len &&
                            deploy_parse_header(frame, msg.payload_len, &h)) {
                            deploy_on_frame(&h, frame + DEPLOY_HDR_BYTES);
                        }
                    } else if (strcmp(msg.subject, CAPTURE_SUB) == 0) {
                        // The payload is one Contract E command (small JSON): add a segment to the
                        // watch-set, or stop listening (clears it). Commands accumulate over time.
                        static uint8_t cmdbuf[512];
                        capture_cmd_t cmd;
                        if (msg.payload_len <= sizeof(cmdbuf) &&
                            hal_tcp_recv_exact(sock, cmdbuf, msg.payload_len) == (int)msg.payload_len &&
                            capture_parse_cmd(cmdbuf, msg.payload_len, &cmd)) {
                            capture_apply(&g_capture, &cmd);
                            if (cmd.stop) {
                                printf("[capture] stop listening\n");
                            } else {
                                printf("[capture] watching segment=%s label=%s (%u total)\n",
                                       cmd.segment, cmd.label, g_capture.count);
                            }
                        }
                    }
                    break;
                }
                default:
                    break;
            }
        }

        // Inference runs on CM55 + the Ethos-U55 NPU; the dwell-smoothed anomaly score arrives
        // over the shared SOCMEM mailbox. CM33-NS just forwards it to NATS. Until CM55 has
        // produced its first score (magic unset), publish 0.
        float score = (SHARED_SCORE->magic == SHARED_SCORE_MAGIC) ? SHARED_SCORE->score : 0.0f;
        publish_score(sock, score);

        // While listening, record this window whenever the device is on a watched segment.
        // The feature window is computed on CM55 and mirrored into the shared mailbox; publish
        // that (only meaningful once CM55 is live, i.e. magic is set).
        if (capture_listening(&g_capture) && SHARED_SCORE->magic == SHARED_SCORE_MAGIC) {
            char seg[32];
            if (!hal_track_segment(seg, sizeof(seg))) seg[0] = '\0';
            const capture_entry_t *e = capture_match(&g_capture, seg);
            if (e != NULL) {
                publish_capture_window(sock, (const int8_t *)SHARED_SCORE->features,
                                       FEAT_OUT_LEN, e, g_capture.seq);
                capture_advance(&g_capture);
            }
        }
        // Note: on-device shadow promote/rollback (evaluate_shadow) ran when CM33 itself
        // executed both active + candidate models. In the NPU architecture inference lives on
        // CM55, so shadowing belongs there; CM33 no longer evaluates it here.
    }
}
