// ABOUTME: Fixed-schema Contract A manifest parser — extracts the fields the firmware needs.
// ABOUTME: Bounded scanning over the JSON bytes (no NUL assumption); pure, host-tested.

#include <stdlib.h>
#include <string.h>

#include "json_scan.h"
#include "manifest.h"

// Copy a numeric token (until a JSON delimiter) into a NUL-terminated scratch buffer.
// Returns the position just past the token so the caller can continue scanning.
static const char *copy_token(const char *p, const char *end, char *buf, size_t cap) {
    while (p < end && json_is_ws(*p)) {
        p++;
    }
    size_t i = 0;
    while (p < end && i + 1 < cap && *p != ',' && *p != ']' && *p != '}' && !json_is_ws(*p)) {
        buf[i++] = *p++;
    }
    buf[i] = '\0';
    return p;
}

static bool parse_int_array(const char *p, const char *end, int32_t *out, int n) {
    while (p < end && *p != '[') {
        p++;
    }
    if (p >= end) {
        return false;
    }
    p++;  // past '['
    for (int i = 0; i < n; i++) {
        char buf[16];
        p = copy_token(p, end, buf, sizeof(buf));
        if (buf[0] == '\0') {
            return false;
        }
        out[i] = (int32_t)strtol(buf, NULL, 10);
        while (p < end && (json_is_ws(*p) || *p == ',')) {
            p++;  // to the next element
        }
    }
    return true;
}

// Parse the first `n` numbers of a JSON float array into `out`. Buffer is generous: a full-
// precision double prints to ~20 chars (e.g. -4.9178876876831055).
static bool parse_float_array(const char *p, const char *end, float *out, int n) {
    while (p < end && *p != '[') {
        p++;
    }
    if (p >= end) {
        return false;
    }
    p++;  // past '['
    for (int i = 0; i < n; i++) {
        char buf[32];
        p = copy_token(p, end, buf, sizeof(buf));
        if (buf[0] == '\0') {
            return false;
        }
        out[i] = strtof(buf, NULL);
        while (p < end && (json_is_ws(*p) || *p == ',')) {
            p++;  // to the next element
        }
    }
    return true;
}

static int hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static bool parse_hex32(const char *p, const char *end, uint8_t out[32]) {
    while (p < end && *p != '"') {
        p++;
    }
    if (p >= end) {
        return false;
    }
    p++;  // past opening quote
    for (int i = 0; i < 32; i++) {
        if (p + 1 >= end) {
            return false;
        }
        int hi = hexval(p[0]);
        int lo = hexval(p[1]);
        if (hi < 0 || lo < 0) {
            return false;
        }
        out[i] = (uint8_t)((hi << 4) | lo);
        p += 2;
    }
    return true;
}

bool parse_manifest(const uint8_t *manifest, size_t len, model_contract_t *out, uint8_t sha[32]) {
    const char *j = (const char *)manifest;
    const char *end = j + len;
    memset(out, 0, sizeof(*out));

    // Bound the input{} and output{} regions so the duplicated "shape"/"dtype" keys resolve
    // to the right object. The pipeline always emits input before output.
    const char *in = json_mem_find(j, len, "\"input\"");
    const char *outk = json_mem_find(j, len, "\"output\"");
    if (!in || !outk || outk <= in) {
        return false;
    }
    size_t in_len = (size_t)(outk - in);
    size_t out_len = (size_t)(end - outk);

    const char *p;
    char buf[32];

    // input.shape / dtype / scale / zero_point  (within the input region)
    if (!(p = json_value_after(in, in_len, "\"shape\"")) ||
        !parse_int_array(p, in + in_len, out->input_shape, 4)) {
        return false;
    }
    if (!(p = json_value_after(in, in_len, "\"dtype\"")) ||
        !json_parse_quoted(p, in + in_len, out->input_dtype, sizeof(out->input_dtype))) {
        return false;
    }
    if (!(p = json_value_after(in, in_len, "\"scale\""))) {
        return false;
    }
    copy_token(p, in + in_len, buf, sizeof(buf));
    out->input_scale = strtof(buf, NULL);
    if (!(p = json_value_after(in, in_len, "\"zero_point\""))) {
        return false;
    }
    copy_token(p, in + in_len, buf, sizeof(buf));
    out->input_zero_point = (int32_t)strtol(buf, NULL, 10);

    // output.shape  (within the output region)
    if (!(p = json_value_after(outk, out_len, "\"shape\"")) ||
        !parse_int_array(p, end, out->output_shape, 2)) {
        return false;
    }

    // arena_bytes + sha256  (top-level)
    if (!(p = json_value_after(j, len, "\"arena_bytes\""))) {
        return false;
    }
    copy_token(p, end, buf, sizeof(buf));
    out->arena_bytes = (uint32_t)strtoul(buf, NULL, 10);

    if (!(p = json_value_after(j, len, "\"sha256\"")) || !parse_hex32(p, end, sha)) {
        return false;
    }
    return true;
}

bool parse_manifest_scoring(const uint8_t *manifest, size_t len, score_params_t *out) {
    const char *j = (const char *)manifest;
    const char *end = j + len;
    memset(out, 0, sizeof(*out));

    const char *p;
    char buf[32];

    // Output quant lives in the output{} object. Bound the search to start at "output" so it can't
    // pick up input.scale or feature_config.scale_eps; output.scale is the first "scale" after it
    // (the pipeline emits output before feature_config / embedding, same fixed order parse_manifest
    // relies on for input-before-output).
    const char *outk = json_mem_find(j, len, "\"output\"");
    if (!outk) {
        return false;
    }
    size_t out_len = (size_t)(end - outk);
    if (!(p = json_value_after(outk, out_len, "\"scale\""))) {
        return false;
    }
    copy_token(p, end, buf, sizeof(buf));
    out->out_scale = strtof(buf, NULL);
    if (!(p = json_value_after(outk, out_len, "\"zero_point\""))) {
        return false;
    }
    copy_token(p, end, buf, sizeof(buf));
    out->out_zero_point = (int32_t)strtol(buf, NULL, 10);

    // Centroid + threshold live in the embedding{} object (the per-unit healthy baseline).
    const char *emb = json_mem_find(j, len, "\"embedding\"");
    if (!emb) {
        return false;
    }
    size_t emb_len = (size_t)(end - emb);
    if (!(p = json_value_after(emb, emb_len, "\"centroid\"")) ||
        !parse_float_array(p, end, out->centroid, SCORE_EMBED_DIM)) {
        return false;
    }
    if (!(p = json_value_after(emb, emb_len, "\"threshold\""))) {
        return false;
    }
    copy_token(p, end, buf, sizeof(buf));
    out->threshold = strtof(buf, NULL);
    return true;
}
