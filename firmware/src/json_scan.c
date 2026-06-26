// ABOUTME: Bounded JSON field scanning shared by the Contract A/E parsers (no NUL assumption).
// ABOUTME: Pure logic, host-tested via the manifest and capture suites that build on it.

#include <string.h>

#include "json_scan.h"

const char *json_mem_find(const char *hay, size_t hlen, const char *needle) {
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

int json_is_ws(char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r';
}

const char *json_value_after(const char *hay, size_t hlen, const char *quoted_key) {
    const char *k = json_mem_find(hay, hlen, quoted_key);
    if (!k) {
        return NULL;
    }
    const char *p = k + strlen(quoted_key);
    const char *end = hay + hlen;
    while (p < end && (json_is_ws(*p) || *p == ':')) {
        p++;
    }
    return (p < end) ? p : NULL;
}

bool json_parse_quoted(const char *p, const char *end, char *out, size_t cap) {
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
