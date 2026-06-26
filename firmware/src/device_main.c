// ABOUTME: Device firmware orchestration — publish Contract B, handle Contract C deploys with
// ABOUTME: verify + shadow + A/B promote/rollback. BUILT ON-TARGET ONLY (uses HAL + TFLM loader).

// This is the skeleton that stitches the tested pure logic (src/model_slot, model_contract,
// shadow, nats_proto) to the hardware via platform_hal.h. The numbered comments map to the
// "Update flow on-device" steps in model-pipeline.md Part 2. Transport details left as TODOs
// are deliberately not faked — wire them to the real BSP.

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "camera_publish.h"
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
// Contract: edge.camera.<line>.<container> — per-frame JPEG, published straight to NATS (binary body).
#define CAMERA_DATA_SUBJECT "edge.camera." LINE "." CONTAINER
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

// Last CM55 score sequence we published. CM55 bumps SHARED_SCORE->seq once per inference; we
// publish (and shadow/capture) only when it advances, so the Contract B cadence tracks the
// inference window — not how often the loop happens to spin or how chatty the broker is.
static uint32_t g_last_score_seq;

// ---- Contract C deploy session ---------------------------------------------
#define DEPLOY_MANIFEST_MAX 1024u
// VERIFY: reserved per-slot flash size (>= worst-case flatbuffer). MUST equal the slot spacing in
// platform_hal_pse84.c's flash layout (FLASH_SLOT_B_OFFSET - FLASH_SLOT_A_OFFSET) so a slot erase
// covers exactly one slot and never reaches the metadata sectors that follow them.
#define MODEL_SLOT_BYTES    (1024u * 1024u)

// A deploy arrives as MANIFEST + SIG frames, then the MODEL flatbuffer streamed in chunks. We
// buffer the small manifest + sig, validate them, then stream model chunks straight to the
// inactive flash slot (the flatbuffer is too big to hold in RAM).
static struct {
    uint8_t     manifest[DEPLOY_MANIFEST_MAX];
    uint32_t    manifest_len;
    bool        manifest_done;
    uint8_t     sig[64];
    bool        sig_done;
    bool           prepared; // sig + contract verified, slot erased, ready to stream MODEL
    slot_id_t      target;
    uint32_t       target_off;
    uint8_t        want_sha[32];
    score_params_t score;    // this model's scoring set, parsed from the manifest at prepare time
    deploy_rx_t    rx;
} g_dep;

static void deploy_session_reset(void) {
    memset(&g_dep, 0, sizeof(g_dep));
    deploy_rx_reset(&g_dep.rx);
}

// Publish one Contract B inference result. Returns <0 on a transport error (the caller reconnects),
// 0 when there was nothing to send (a frame-build failure, never fatal to the connection).
static int publish_score(int sock, float score) {
    char body[160];
    int blen = snprintf(body, sizeof(body),
                        "{\"ts\":\"\",\"container_id\":\"" CONTAINER "\","
                        "\"anomaly_score\":%.4f,\"data_classification\":\"inference\","
                        "\"bytes\":0}",
                        (double)score);
    char frame[256];
    int flen = nats_build_pub(frame, sizeof(frame), PUB_SUBJECT,
                              (const uint8_t *)body, (size_t)blen);
    if (flen <= 0) return 0;
    return hal_tcp_send(sock, (const uint8_t *)frame, (size_t)flen);
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

    // Scoring params travel with the model (output quant + centroid + threshold). Refuse here, before
    // touching flash, if they're missing — a model we can't score correctly must not be staged.
    if (!parse_manifest_scoring(g_dep.manifest, g_dep.manifest_len, &g_dep.score)) {
        printf("[deploy] refused: missing scoring params\n");
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
    meta.slot[g_dep.target].score = g_dep.score;  // scoring set persists with the slot
    meta.slot[g_dep.target].valid = true;         // written + verified, but NOT yet active
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
            // uint64 add so a crafted offset near UINT32_MAX can't wrap a 32-bit sum past the
            // bound and slip a wild memcpy through — same guard the model path uses (deploy.c).
            if ((uint64_t)h->offset + h->chunk_len <= sizeof(g_dep.manifest)) {
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

// (8) After each shadowed window, decide whether to promote or roll back. The active + candidate
// scores come from CM55 over the shared mailbox (CM55 runs both models on the same window); the
// verdict stays here, on CM33, in the host-tested shadow logic.
static void evaluate_shadow(float active_score, float candidate_score) {
    shadow_observe(&g_shadow, active_score, candidate_score);
    switch (shadow_decide(&g_shadow, &SHADOW)) {
        case SHADOW_PENDING:
            return;
        case SHADOW_PROMOTE: {
            model_meta_t meta;
            // Flip persistent metadata FIRST (so a reboot loads the new active), then swap the live
            // model on CM55. model_loader_promote() is a pointer-swap there, not a flash reload, and
            // it clears CM55's candidate role as part of promoting.
            if (hal_meta_read(&meta) && meta_promote(&meta, g_candidate_slot) &&
                hal_meta_write(&meta) && model_loader_promote()) {
                printf("[shadow] promoted slot %d\n", g_candidate_slot);
            }
            break;
        }
        case SHADOW_ROLLBACK:
            // Leave active_slot untouched; just discard the candidate. (meta_rollback() is
            // for recovering an already-promoted model that later misbehaves.)
            model_loader_clear_candidate();
            printf("[shadow] rolled back; keeping incumbent\n");
            break;
    }
    g_shadowing = false;
}

// Establish one NATS session: connect (with retry while Wi-Fi/DNS settle), read INFO, authenticate
// (nkey if the server sent a nonce, else anonymous), CONNECT, and SUBscribe. Returns the socket on
// success, or -1 if the session could not be brought up — the caller decides whether to retry. Every
// failure path closes the socket so a single static handle is never leaked across reconnects.
static int nats_session_open(void) {
    int sock = -1;
    for (int attempt = 1; attempt <= NATS_CONNECT_RETRIES; attempt++) {
        sock = hal_tcp_connect(NATS_HOST, NATS_PORT);
        if (sock >= 0) break;
        printf("[net] connect attempt %d/%d failed; retrying in %d ms\n",
               attempt, NATS_CONNECT_RETRIES, NATS_CONNECT_RETRY_MS);
        hal_sleep_ms(NATS_CONNECT_RETRY_MS);
    }
    if (sock < 0) return -1;

    // INFO is the server's first line and arrives immediately; wait out recv timeouts (0) for it,
    // but bail on a real error so we don't authenticate against an empty line.
    char line[256];
    int n;
    do {
        n = hal_tcp_recv_line(sock, line, sizeof(line));
    } while (n == 0);
    if (n < 0) {
        hal_tcp_close(sock);
        return -1;
    }

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
            hal_tcp_close(sock);
            return -1;  // cannot authenticate -> do not join the fabric
        }
        cn = nats_build_connect(connect, sizeof(connect), CONTAINER, nkey, sigb64);
    } else {
        cn = nats_build_connect(connect, sizeof(connect), CONTAINER, NULL, NULL);
    }
    if (cn < 0 || hal_tcp_send(sock, (const uint8_t *)connect, (size_t)cn) < 0) {
        hal_tcp_close(sock);
        return -1;
    }

    // Subscribe to Contract C deploys (sid 1) and Contract E capture commands (sid 2).
    char sub[128];
    int sn = snprintf(sub, sizeof(sub), "SUB %s 1\r\nSUB %s 2\r\n", DEPLOY_SUB, CAPTURE_SUB);
    if (hal_tcp_send(sock, (const uint8_t *)sub, (size_t)sn) < 0) {
        hal_tcp_close(sock);
        return -1;
    }
    return sock;
}

// Discard `n` payload bytes from the socket — an unhandled or oversized MSG body — so the stream
// stays framed and the next recv_line lands on a real protocol line, not payload. Returns false on
// a transport error (the caller reconnects). recv_exact rides out the socket timeout internally.
static bool nats_drain(int sock, uint32_t n) {
    uint8_t scratch[256];
    while (n > 0) {
        uint32_t chunk = (n < sizeof(scratch)) ? n : (uint32_t)sizeof(scratch);
        if (hal_tcp_recv_exact(sock, scratch, chunk) != (int)chunk) {
            return false;
        }
        n -= chunk;
    }
    return true;
}

// Run one connected NATS session until the transport drops. Returns when the connection is lost
// (recv/send error or peer close) so the caller can reconnect; the socket is still open on return.
static void nats_session_run(int sock) {
    char line[256];
    for (;;) {
        // Drain one protocol line (keepalive + inbound deploys/commands). 0 = recv timeout (quiet
        // socket) -> fall through to publishing; <0 = transport gone -> end the session.
        int n = hal_tcp_recv_line(sock, line, sizeof(line));
        if (n < 0) {
            printf("[net] connection lost; reconnecting\n");
            return;
        }
        if (n > 0) {
            switch (nats_line_kind(line)) {
                case NATS_LINE_PING:
                    if (hal_tcp_send(sock, (const uint8_t *)"PONG\r\n", 6) < 0) return;
                    break;
                case NATS_LINE_MSG: {
                    // One NATS message: "MSG <subj> <sid> <#bytes>\r\n", then exactly payload_len
                    // body bytes (its trailing CRLF reads back as an empty line, ignored). The body
                    // MUST be consumed in full whatever we do with it — leaving any byte in the
                    // socket would make the next recv_line read payload as a protocol line.
                    static uint8_t frame[DEPLOY_HDR_BYTES + 4096];
                    static uint8_t cmdbuf[512];
                    nats_msg_t msg;
                    if (!nats_parse_msg_header(line, &msg)) {
                        // No payload_len -> we can't realign the stream. Reconnect rather than
                        // risk interpreting the body as protocol.
                        printf("[net] unparseable MSG header; reconnecting\n");
                        return;
                    }
                    switch (nats_route_msg(&msg, DEPLOY_SUB, sizeof(frame),
                                           CAPTURE_SUB, sizeof(cmdbuf))) {
                        case NATS_ROUTE_DEPLOY: {
                            // The MSG payload is one Contract C frame (header + chunk). Read it,
                            // then parse + route. One NATS message carries header + one chunk.
                            if (hal_tcp_recv_exact(sock, frame, msg.payload_len) !=
                                (int)msg.payload_len) {
                                return;  // body didn't arrive -> connection lost
                            }
                            deploy_hdr_t h;
                            if (deploy_parse_header(frame, msg.payload_len, &h)) {
                                deploy_on_frame(&h, frame + DEPLOY_HDR_BYTES);
                            }
                            break;
                        }
                        case NATS_ROUTE_CAPTURE: {
                            // One Contract E command (small JSON): add a segment to the watch-set,
                            // or stop listening (clears it). Commands accumulate over time.
                            if (hal_tcp_recv_exact(sock, cmdbuf, msg.payload_len) !=
                                (int)msg.payload_len) {
                                return;
                            }
                            capture_cmd_t cmd;
                            if (capture_parse_cmd(cmdbuf, msg.payload_len, &cmd)) {
                                capture_apply(&g_capture, &cmd);
                                if (cmd.stop) {
                                    printf("[capture] stop listening\n");
                                } else {
                                    printf("[capture] watching segment=%s label=%s (%u total)\n",
                                           cmd.segment, cmd.label, g_capture.count);
                                }
                            }
                            break;
                        }
                        case NATS_ROUTE_DRAIN:
                            // Unknown subject, or a body too large for our buffers: drain it so the
                            // stream stays framed instead of desyncing.
                            printf("[net] draining %u-byte MSG on %s\n",
                                   (unsigned)msg.payload_len, msg.subject);
                            if (!nats_drain(sock, msg.payload_len)) return;
                            break;
                    }
                    break;
                }
                default:
                    break;
            }
        }

        // Camera frames publish independently of the inference cadence (their own rate, not the
        // CM55 score seq): poll the camera HAL and PUB any new JPEG to edge.camera. Dormant until
        // the capture pipeline is ported (the hal_camera stub returns no frame). A transport error
        // here ends the session so the caller reconnects, same as a failed score publish.
        if (camera_publish_step(sock, CAMERA_DATA_SUBJECT) < 0) return;

        // Inference runs on CM55 + the Ethos-U55 NPU; the dwell-smoothed anomaly score (and, while
        // shadowing a candidate, the candidate's score on the same window) arrive over the shared
        // SOCMEM mailbox. Publish only on a fresh score (seq advances once per CM55 window) so the
        // Contract B cadence is the inference rate, not the loop spin rate. Until CM55 has produced
        // its first score (magic unset), there is nothing to publish.
        if (SHARED_SCORE->magic != SHARED_SCORE_MAGIC || SHARED_SCORE->seq == g_last_score_seq) {
            continue;
        }
        g_last_score_seq = SHARED_SCORE->seq;

        float candidate_score = 0.0f;
        bool  have_candidate = false;
        float score = model_loader_infer(NULL, 0, &candidate_score, &have_candidate);
        if (publish_score(sock, score) < 0) return;

        // While listening, record this window whenever the device is on a watched segment.
        // The feature window is computed on CM55 and mirrored into the shared mailbox.
        if (capture_listening(&g_capture)) {
            char seg[32];
            if (!hal_track_segment(seg, sizeof(seg))) seg[0] = '\0';
            const capture_entry_t *e = capture_match(&g_capture, seg);
            if (e != NULL) {
                publish_capture_window(sock, (const int8_t *)SHARED_SCORE->features,
                                       FEAT_OUT_LEN, e, g_capture.seq);
                capture_advance(&g_capture);
            }
        }
        // Shadow verdict: while a candidate is being trialled, CM55 scores it on every window and
        // mirrors that score here. CM33 owns the decision — feed both scores to the host-tested
        // shadow logic, which promotes (metadata flip + MC_PROMOTE) or rolls back once it has seen
        // enough windows. The data plane is on CM55; the verdict plane stays on CM33.
        if (g_shadowing && have_candidate) {
            evaluate_shadow(score, candidate_score);
        }
    }
}

// Entry point for the device orchestration loop. On a bare-metal host this is the
// program; under ModusToolbox the board main() (proj_cm33_ns) brings up the BSP and
// runs this as a FreeRTOS task (see firmware-app/). It only returns on a fatal init
// failure — the steady state is the infinite (re)connect / publish / deploy loop below.
int device_main(void) {
    if (!hal_net_init()) return 1;                    // bring Wi-Fi up before any socket
    if (!model_loader_load_active(SLOT_A)) return 1;  // baked-in / last-good model
    // Camera capture is supplementary to inference on this unit: a bring-up failure must not strand
    // the wear-detection path, so log and continue (camera_publish_step then just finds no frames).
    if (!hal_camera_init()) printf("[cam] camera bring-up failed; continuing without camera\n");

    // Session loop: a dropped broker connection is a transient, not a fatal — re-establish it
    // forever rather than stranding the device until a power-cycle.
    for (;;) {
        int sock = nats_session_open();
        if (sock < 0) {
            printf("[net] session setup failed; retrying in %d ms\n", NATS_CONNECT_RETRY_MS);
            hal_sleep_ms(NATS_CONNECT_RETRY_MS);
            continue;
        }
        deploy_session_reset();   // a new connection invalidates any half-streamed deploy
        nats_session_run(sock);
        hal_tcp_close(sock);
    }
}
