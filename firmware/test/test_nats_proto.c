// ABOUTME: Tests for NATS wire framing — line classification, CONNECT/PUB building, MSG parsing.

#include <string.h>

#include "nats_proto.h"
#include "test_util.h"

void run_nats_proto_tests(void) {
    // ---- line classification ----
    CHECK(nats_line_kind("INFO {\"server_id\":\"x\"}\r\n") == NATS_LINE_INFO);
    CHECK(nats_line_kind("PING\r\n") == NATS_LINE_PING);
    CHECK(nats_line_kind("PONG\r\n") == NATS_LINE_PONG);
    CHECK(nats_line_kind("+OK\r\n") == NATS_LINE_OK);
    CHECK(nats_line_kind("-ERR 'unknown'\r\n") == NATS_LINE_ERR);
    CHECK(nats_line_kind("MSG models.line1.deploy 1 42\r\n") == NATS_LINE_MSG);
    CHECK(nats_line_kind("garbage") == NATS_LINE_UNKNOWN);

    // ---- CONNECT (anonymous: open demo fabric) ----
    char cbuf[256];
    int cn = nats_build_connect(cbuf, sizeof(cbuf), "cnc-7", NULL, NULL);
    CHECK(cn > 0);
    CHECK(strstr(cbuf, "CONNECT ") == cbuf);
    CHECK(strstr(cbuf, "\"verbose\":false") != NULL);  // no +OK per PUB
    CHECK(strstr(cbuf, "\"nkey\"") == NULL);            // no credential when none supplied
    CHECK(cbuf[cn - 2] == '\r' && cbuf[cn - 1] == '\n');
    CHECK(nats_build_connect(cbuf, 4, "cnc-7", NULL, NULL) == -1);  // too small -> refuse

    // ---- CONNECT (nkey auth) ----
    char abuf[256];
    int an = nats_build_connect(abuf, sizeof(abuf), "cnc-7", "UABC123", "c2lnbmF0dXJl");
    CHECK(an > 0);
    CHECK(strstr(abuf, "\"nkey\":\"UABC123\"") != NULL);
    CHECK(strstr(abuf, "\"sig\":\"c2lnbmF0dXJl\"") != NULL);
    CHECK(strstr(abuf, "\"verbose\":false") != NULL);   // auth doesn't drop the other options
    CHECK(abuf[an - 2] == '\r' && abuf[an - 1] == '\n');

    // ---- nonce extraction from INFO ----
    char nonce[64];
    CHECK(nats_parse_info_nonce(
        "INFO {\"server_id\":\"x\",\"nonce\":\"abc123XYZ\",\"max_payload\":1048576}\r\n",
        nonce, sizeof(nonce)) == true);
    CHECK_STR_EQ(nonce, "abc123XYZ");
    // No nonce -> server isn't asking for auth.
    CHECK(nats_parse_info_nonce("INFO {\"server_id\":\"x\",\"max_payload\":1048576}\r\n",
                                nonce, sizeof(nonce)) == false);
    // Value too long for the buffer -> refuse (don't overflow).
    char tiny[4];
    CHECK(nats_parse_info_nonce("INFO {\"nonce\":\"abcdefgh\"}\r\n", tiny, sizeof(tiny)) == false);

    // ---- base64 (standard, padded — matches the nats-py-proven server path) ----
    char b64[128];
    CHECK(nats_b64_encode(b64, sizeof(b64), (const uint8_t *)"hello", 5) == 8);
    CHECK_STR_EQ(b64, "aGVsbG8=");
    CHECK(nats_b64_encode(b64, sizeof(b64), (const uint8_t *)"M", 1) == 4);
    CHECK_STR_EQ(b64, "TQ==");
    CHECK(nats_b64_encode(b64, sizeof(b64), (const uint8_t *)"", 0) == 0);
    CHECK_STR_EQ(b64, "");
    // A 64-byte Ed25519 signature encodes to 88 base64 chars.
    uint8_t sig[64];
    memset(sig, 0xAB, sizeof(sig));
    CHECK(nats_b64_encode(b64, sizeof(b64), sig, sizeof(sig)) == 88);
    CHECK(nats_b64_encode(b64, 8, sig, sizeof(sig)) == -1);  // too small -> refuse

    // ---- PUB (binary-safe, exact framing) ----
    char pbuf[128];
    const char *payload = "hello";
    int pn = nats_build_pub(pbuf, sizeof(pbuf), "edge.line1.cnc-7",
                            (const uint8_t *)payload, 5);
    const char *expect = "PUB edge.line1.cnc-7 5\r\nhello\r\n";
    CHECK(pn == (int)strlen(expect));
    CHECK(memcmp(pbuf, expect, pn) == 0);
    CHECK(nats_build_pub(pbuf, 8, "edge.line1.cnc-7",
                         (const uint8_t *)payload, 5) == -1);  // too small -> refuse

    // ---- MSG header parsing (no reply subject) ----
    nats_msg_t m;
    CHECK(nats_parse_msg_header("MSG models.line1.deploy 1 42", &m) == true);
    CHECK_STR_EQ(m.subject, "models.line1.deploy");
    CHECK_STR_EQ(m.sid, "1");
    CHECK(m.payload_len == 42);

    // ---- MSG header parsing (with reply-to subject) ----
    nats_msg_t r;
    CHECK(nats_parse_msg_header("MSG models.line1.deploy 7 _INBOX.abc 1024", &r) == true);
    CHECK_STR_EQ(r.subject, "models.line1.deploy");
    CHECK_STR_EQ(r.sid, "7");
    CHECK(r.payload_len == 1024);

    // ---- malformed header -> rejected ----
    nats_msg_t bad;
    CHECK(nats_parse_msg_header("MSG onlysubject", &bad) == false);
}
