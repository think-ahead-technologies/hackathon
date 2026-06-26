// ABOUTME: NATS core wire framing — pure string building/parsing, no sockets.
// ABOUTME: hal_tcp_* (platform_hal.h) moves these bytes; this file just shapes them.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "nats_proto.h"

nats_line_kind_t nats_line_kind(const char *line) {
    if (strncmp(line, "INFO", 4) == 0) return NATS_LINE_INFO;
    if (strncmp(line, "PING", 4) == 0) return NATS_LINE_PING;
    if (strncmp(line, "PONG", 4) == 0) return NATS_LINE_PONG;
    if (strncmp(line, "+OK", 3) == 0)  return NATS_LINE_OK;
    if (strncmp(line, "-ERR", 4) == 0) return NATS_LINE_ERR;
    if (strncmp(line, "MSG ", 4) == 0) return NATS_LINE_MSG;
    return NATS_LINE_UNKNOWN;
}

int nats_build_connect(char *buf, size_t cap, const char *name,
                       const char *nkey, const char *sig) {
    // The nkey/sig pair is emitted only when both are supplied; otherwise the field block is
    // empty and the line is byte-identical to an anonymous CONNECT (open demo fabric).
    char auth[256];
    auth[0] = '\0';
    if (nkey && sig) {
        int an = snprintf(auth, sizeof(auth), "\"nkey\":\"%s\",\"sig\":\"%s\",", nkey, sig);
        if (an < 0 || (size_t)an >= sizeof(auth)) return -1;
    }
    // verbose=false: the server will not +OK every PUB, which keeps the MCU's read path simple.
    int n = snprintf(buf, cap,
                     "CONNECT {\"verbose\":false,\"pedantic\":false,\"name\":\"%s\",%s"
                     "\"lang\":\"c\",\"version\":\"0.1\",\"protocol\":1}\r\n",
                     name, auth);
    if (n < 0 || (size_t)n >= cap) return -1;
    return n;
}

bool nats_parse_info_nonce(const char *line, char *out, size_t cap) {
    const char *key = strstr(line, "\"nonce\":\"");
    if (!key) return false;  // no nonce -> server did not request auth
    const char *p = key + strlen("\"nonce\":\"");
    size_t i = 0;
    while (p[i] != '\0' && p[i] != '"') {
        if (i + 1 >= cap) return false;  // value would not fit (leave room for the NUL)
        out[i] = p[i];
        i++;
    }
    if (p[i] != '"') return false;  // unterminated value
    out[i] = '\0';
    return true;
}

int nats_b64_encode(char *out, size_t cap, const uint8_t *data, size_t len) {
    static const char A[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    size_t olen = 4 * ((len + 2) / 3);   // padded output length
    if (olen + 1 > cap) return -1;       // +1 for the NUL terminator

    size_t o = 0, i = 0;
    for (; i + 3 <= len; i += 3) {
        uint32_t v = ((uint32_t)data[i] << 16) | ((uint32_t)data[i + 1] << 8) | data[i + 2];
        out[o++] = A[(v >> 18) & 0x3f];
        out[o++] = A[(v >> 12) & 0x3f];
        out[o++] = A[(v >> 6) & 0x3f];
        out[o++] = A[v & 0x3f];
    }
    size_t rem = len - i;
    if (rem == 1) {
        uint32_t v = (uint32_t)data[i] << 16;
        out[o++] = A[(v >> 18) & 0x3f];
        out[o++] = A[(v >> 12) & 0x3f];
        out[o++] = '=';
        out[o++] = '=';
    } else if (rem == 2) {
        uint32_t v = ((uint32_t)data[i] << 16) | ((uint32_t)data[i + 1] << 8);
        out[o++] = A[(v >> 18) & 0x3f];
        out[o++] = A[(v >> 12) & 0x3f];
        out[o++] = A[(v >> 6) & 0x3f];
        out[o++] = '=';
    }
    out[o] = '\0';
    return (int)o;
}

int nats_build_pub(char *buf, size_t cap, const char *subject,
                   const uint8_t *payload, size_t payload_len) {
    // %lu + cast, not %zu: the target's newlib-nano printf (--specs=nano.specs) does not
    // support the 'z' length modifier and would emit the literal "zu" as the byte count,
    // producing a malformed PUB the broker rejects. unsigned long covers any real payload.
    int hn = snprintf(buf, cap, "PUB %s %lu\r\n", subject, (unsigned long)payload_len);
    if (hn < 0 || (size_t)hn >= cap) return -1;
    size_t total = (size_t)hn + payload_len + 2;  // payload + trailing CRLF
    if (total > cap) return -1;
    memcpy(buf + hn, payload, payload_len);
    buf[hn + payload_len]     = '\r';
    buf[hn + payload_len + 1] = '\n';
    return (int)total;
}

int nats_build_pub_header(char *buf, size_t cap, const char *subject, size_t payload_len) {
    // %lu + cast, not %zu: newlib-nano printf lacks the 'z' modifier (see nats_build_pub).
    int hn = snprintf(buf, cap, "PUB %s %lu\r\n", subject, (unsigned long)payload_len);
    if (hn < 0 || (size_t)hn >= cap) return -1;
    return hn;
}

bool nats_parse_msg_header(const char *line, nats_msg_t *out) {
    // Work on a CRLF-stripped copy so we can tokenize in place.
    char tmp[160];
    size_t i = 0;
    while (line[i] && line[i] != '\r' && line[i] != '\n' && i < sizeof(tmp) - 1) {
        tmp[i] = line[i];
        i++;
    }
    tmp[i] = '\0';

    char *tok[8];
    int nt = 0;
    for (char *p = strtok(tmp, " "); p && nt < 8; p = strtok(NULL, " ")) {
        tok[nt++] = p;
    }

    // Forms: "MSG <subject> <sid> <#bytes>" (4 tokens) or with a reply subject (5 tokens).
    if ((nt != 4 && nt != 5) || strcmp(tok[0], "MSG") != 0) {
        return false;
    }

    const char *bytes = tok[nt - 1];
    char *end = NULL;
    unsigned long len = strtoul(bytes, &end, 10);
    if (end == bytes || *end != '\0') {
        return false;  // the byte-count token was not a clean integer
    }

    snprintf(out->subject, sizeof(out->subject), "%s", tok[1]);
    snprintf(out->sid, sizeof(out->sid), "%s", tok[2]);
    out->payload_len = (uint32_t)len;
    return true;
}

nats_route_t nats_route_msg(const nats_msg_t *msg,
                            const char *deploy_sub, uint32_t deploy_cap,
                            const char *capture_sub, uint32_t capture_cap) {
    // A subject we serve only routes to its handler if the body also fits that handler's buffer;
    // an oversized body (or any other subject) falls through to DRAIN so it is still consumed.
    if (strcmp(msg->subject, deploy_sub) == 0 && msg->payload_len <= deploy_cap) {
        return NATS_ROUTE_DEPLOY;
    }
    if (strcmp(msg->subject, capture_sub) == 0 && msg->payload_len <= capture_cap) {
        return NATS_ROUTE_CAPTURE;
    }
    return NATS_ROUTE_DRAIN;
}
