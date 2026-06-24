// ABOUTME: Cross-language harness — feed deploy_frame.py's bytes through the real C device parser.
// ABOUTME: Proves the Python sender and the firmware agree on the Contract C wire format (no board).

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "deploy.h"
#include "manifest.h"
#include "model_contract.h"

#define CAP (2u * 1024u * 1024u)

static uint8_t g_frames[CAP + 65536];
static uint8_t g_model[CAP];
static uint8_t g_manifest[4096];
static uint8_t g_sig[64];

static long read_file(const char *path, uint8_t *buf, size_t cap) {
    FILE *f = fopen(path, "rb");
    if (!f) {
        return -1;
    }
    long n = (long)fread(buf, 1, cap, f);
    fclose(f);
    return n;
}

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s <frames-file> <out-model-file>\n", argv[0]);
        return 2;
    }
    long n = read_file(argv[1], g_frames, sizeof(g_frames));
    if (n < 0) {
        fprintf(stderr, "cannot read frames\n");
        return 2;
    }

    // Walk the concatenated frame stream exactly as the device would per NATS message: each frame
    // is self-describing (header + chunk_len payload), so frame length = DEPLOY_HDR_BYTES + chunk_len.
    deploy_rx_t rx;
    deploy_rx_reset(&rx);
    uint32_t manifest_len = 0;
    int have_sig = 0;
    long pos = 0;
    while (pos < n) {
        deploy_hdr_t h;
        if (!deploy_parse_header(g_frames + pos, (size_t)(n - pos), &h)) {
            fprintf(stderr, "FAIL: bad header at offset %ld\n", pos);
            return 1;
        }
        const uint8_t *payload = g_frames + pos + DEPLOY_HDR_BYTES;
        if (h.part == DEPLOY_PART_MANIFEST) {
            if ((size_t)h.offset + h.chunk_len > sizeof(g_manifest)) {
                fprintf(stderr, "FAIL: manifest exceeds buffer\n");
                return 1;
            }
            memcpy(g_manifest + h.offset, payload, h.chunk_len);
            if (h.flags & DEPLOY_FLAG_LAST) {
                manifest_len = h.offset + h.chunk_len;
            }
        } else if (h.part == DEPLOY_PART_SIG) {
            if (h.chunk_len != 64) {
                fprintf(stderr, "FAIL: sig length %u\n", h.chunk_len);
                return 1;
            }
            memcpy(g_sig, payload, 64);
            have_sig = 1;
        } else if (h.part == DEPLOY_PART_MODEL) {
            if (!deploy_rx_accept(&rx, &h, CAP)) {
                fprintf(stderr, "FAIL: chunk rejected at offset %u\n", h.offset);
                return 1;
            }
            memcpy(g_model + h.offset, payload, h.chunk_len);
        }
        pos += (long)DEPLOY_HDR_BYTES + (long)h.chunk_len;
    }

    if (!deploy_rx_complete(&rx)) {
        fprintf(stderr, "FAIL: model incomplete (%u/%u)\n", rx.received, rx.total);
        return 1;
    }
    if (manifest_len == 0 || !have_sig) {
        fprintf(stderr, "FAIL: missing manifest or sig\n");
        return 1;
    }

    model_contract_t c;
    uint8_t sha[32];
    if (!parse_manifest(g_manifest, manifest_len, &c, sha)) {
        fprintf(stderr, "FAIL: manifest parse\n");
        return 1;
    }

    FILE *out = fopen(argv[2], "wb");
    if (!out) {
        fprintf(stderr, "cannot write reassembled model\n");
        return 2;
    }
    fwrite(g_model, 1, rx.total, out);
    fclose(out);

    // Report what the C side parsed, for the Python driver to check.
    printf("model_bytes=%u\n", rx.total);
    printf("input_shape=%d,%d,%d,%d\n",
           c.input_shape[0], c.input_shape[1], c.input_shape[2], c.input_shape[3]);
    printf("arena_bytes=%u\n", c.arena_bytes);
    printf("dtype=%s\n", c.input_dtype);
    printf("sha256=");
    for (int i = 0; i < 32; i++) {
        printf("%02x", sha[i]);
    }
    printf("\n");
    return 0;
}
