// ABOUTME: NATS core wire-protocol framing — build CONNECT/PUB, classify and parse inbound lines.
// ABOUTME: Pure string logic (no sockets); the TCP transport lives behind platform_hal.h.

#ifndef NATS_PROTO_H
#define NATS_PROTO_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// The inbound protocol verbs a publishing+subscribing client must recognise.
typedef enum {
    NATS_LINE_INFO,     // server greeting (ignore contents to publish)
    NATS_LINE_PING,     // must be answered with PONG or the server drops us
    NATS_LINE_PONG,
    NATS_LINE_OK,       // +OK (only seen if verbose=true)
    NATS_LINE_ERR,      // -ERR ...
    NATS_LINE_MSG,      // MSG <subject> <sid> [reply] <#bytes>  (a delivered message)
    NATS_LINE_UNKNOWN,
} nats_line_kind_t;

// A parsed MSG header (the payload bytes follow on the next read).
typedef struct {
    char     subject[64];
    char     sid[16];
    uint32_t payload_len;
} nats_msg_t;

// Classify one inbound protocol line (the trailing CRLF may be present or stripped).
nats_line_kind_t nats_line_kind(const char *line);

// Write a CONNECT line with verbose=false (so the server won't +OK every PUB).
// For nkey auth, pass the device's public nkey and the base64 signature over the
// server nonce (see nats_parse_info_nonce + nats_b64_encode); pass NULL for both to
// connect anonymously (the open demo fabric). Returns bytes written (excluding the
// NUL), or -1 if it would not fit in `cap`.
int nats_build_connect(char *buf, size_t cap, const char *name,
                       const char *nkey, const char *sig);

// Extract the "nonce" value from an INFO line into `out` (NUL-terminated). The server
// includes a nonce only when auth is required, so a false return means "anonymous OK".
// Returns false if there is no nonce or it would not fit in `cap`.
bool nats_parse_info_nonce(const char *line, char *out, size_t cap);

// Standard base64 (RFC 4648, '=' padded) — the encoding NATS expects for the nkey
// signature. Writes a NUL-terminated string. Returns encoded length (excluding the
// NUL), or -1 if it would not fit in `cap`.
int nats_b64_encode(char *out, size_t cap, const uint8_t *data, size_t len);

// Write a complete PUB frame: "PUB <subject> <payload_len>\r\n<payload>\r\n".
// Binary-safe (payload may contain anything). Returns total bytes, or -1 if it would
// not fit in `cap`.
int nats_build_pub(char *buf, size_t cap, const char *subject,
                   const uint8_t *payload, size_t payload_len);

// Parse a MSG header line into subject/sid/payload_len. Handles both the 3-token
// (no reply) and 4-token (with reply-to) forms. Returns false on a malformed header.
bool nats_parse_msg_header(const char *line, nats_msg_t *out);

// What to do with an inbound MSG body, decided purely from its parsed header. The caller MUST
// consume exactly payload_len body bytes whatever the route — DRAIN is the explicit "consume and
// discard" path, so an unknown subject or an oversized body can never leave payload in the socket
// and desync the next read.
typedef enum {
    NATS_ROUTE_DEPLOY,   // Contract C: read the body into the deploy buffer, parse a frame
    NATS_ROUTE_CAPTURE,  // Contract E: read the body into the command buffer, parse a command
    NATS_ROUTE_DRAIN,    // unknown subject, or body too large for its buffer -> consume + discard
} nats_route_t;

// Route a parsed MSG: DEPLOY or CAPTURE when the subject matches AND the body fits that handler's
// buffer; otherwise DRAIN. `deploy_cap`/`capture_cap` are the handler buffer sizes the caller will
// recv the body into — a body larger than its buffer routes to DRAIN rather than being truncated.
nats_route_t nats_route_msg(const nats_msg_t *msg,
                            const char *deploy_sub, uint32_t deploy_cap,
                            const char *capture_sub, uint32_t capture_cap);

#endif  // NATS_PROTO_H
