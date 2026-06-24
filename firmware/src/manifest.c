// ABOUTME: Fixed-schema Contract A manifest parser — extracts the fields the firmware needs.
// ABOUTME: Bounded scanning over the JSON bytes (no NUL assumption); pure, host-tested.

#include <stdlib.h>
#include <string.h>

#include "manifest.h"

// Bounded substring search (no NUL assumption on the input).
static const char *mem_find(const char *hay, size_t hlen, const char *needle) {
    size_t nlen = strlen(needle);
    if (nlen == 0 || nlen > hlen) {
        return NULL;
    }
    for (size_t i = 0; i + nlen <= hlen; i++) {
        if (memcmp(hay + i, needle, nlen) == 0) {
            return hay + i;
        }
    }
    return NULL;
}

static int is_ws(char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r';
}

// Return the value position just after `"key" :` (whitespace skipped), within [hay, hay+hlen).
static const char *value_after(const char *hay, size_t hlen, const char *quoted_key) {
    const char *k = mem_find(hay, hlen, quoted_key);
    if (!k) {
        return NULL;
    }
    const char *p = k + strlen(quoted_key);
    const char *end = hay + hlen;
    while (p < end && (is_ws(*p) || *p == ':')) {
        p++;
    }
    return (p < end) ? p : NULL;
}

// Copy a numeric token (until a JSON delimiter) into a NUL-terminated scratch buffer.
// Returns the position just past the token so the caller can continue scanning.
static const char *copy_token(const char *p, const char *end, char *buf, size_t cap) {
    while (p < end && is_ws(*p)) {
        p++;
    }
    size_t i = 0;
    while (p < end && i + 1 < cap && *p != ',' && *p != ']' && *p != '}' && !is_ws(*p)) {
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
        while (p < end && (is_ws(*p) || *p == ',')) {
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

static bool parse_quoted(const char *p, const char *end, char *out, size_t cap) {
    while (p < end && *p != '"') {
        p++;
    }
    if (p >= end) {
        return false;
    }
    p++;  // past opening quote
    size_t i = 0;
    while (p < end && *p != '"' && i + 1 < cap) {
        out[i++] = *p++;
    }
    out[i] = '\0';
    return p < end && *p == '"';
}

bool parse_manifest(const uint8_t *manifest, size_t len, model_contract_t *out, uint8_t sha[32]) {
    const char *j = (const char *)manifest;
    const char *end = j + len;
    memset(out, 0, sizeof(*out));

    // Bound the input{} and output{} regions so the duplicated "shape"/"dtype" keys resolve
    // to the right object. The pipeline always emits input before output.
    const char *in = mem_find(j, len, "\"input\"");
    const char *outk = mem_find(j, len, "\"output\"");
    if (!in || !outk || outk <= in) {
        return false;
    }
    size_t in_len = (size_t)(outk - in);
    size_t out_len = (size_t)(end - outk);

    const char *p;
    char buf[24];

    // input.shape / dtype / scale / zero_point  (within the input region)
    if (!(p = value_after(in, in_len, "\"shape\"")) ||
        !parse_int_array(p, in + in_len, out->input_shape, 4)) {
        return false;
    }
    if (!(p = value_after(in, in_len, "\"dtype\"")) ||
        !parse_quoted(p, in + in_len, out->input_dtype, sizeof(out->input_dtype))) {
        return false;
    }
    if (!(p = value_after(in, in_len, "\"scale\""))) {
        return false;
    }
    copy_token(p, in + in_len, buf, sizeof(buf));
    out->input_scale = strtof(buf, NULL);
    if (!(p = value_after(in, in_len, "\"zero_point\""))) {
        return false;
    }
    copy_token(p, in + in_len, buf, sizeof(buf));
    out->input_zero_point = (int32_t)strtol(buf, NULL, 10);

    // output.shape  (within the output region)
    if (!(p = value_after(outk, out_len, "\"shape\"")) ||
        !parse_int_array(p, end, out->output_shape, 2)) {
        return false;
    }

    // arena_bytes + sha256  (top-level)
    if (!(p = value_after(j, len, "\"arena_bytes\""))) {
        return false;
    }
    copy_token(p, end, buf, sizeof(buf));
    out->arena_bytes = (uint32_t)strtoul(buf, NULL, 10);

    if (!(p = value_after(j, len, "\"sha256\"")) || !parse_hex32(p, end, sha)) {
        return false;
    }
    return true;
}
