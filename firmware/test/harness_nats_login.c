// ABOUTME: CLI harness exposing the real nats_proto auth functions for the live-login cross test.
// ABOUTME: The Python driver calls these so our C code drives an actual NATS nkey handshake (no board).

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "nats_proto.h"

// Decode a hex string into bytes. Returns byte count, or -1 on malformed input.
static int hex_decode(const char *hex, uint8_t *out, size_t cap) {
    size_t n = strlen(hex);
    if (n % 2 != 0 || n / 2 > cap) return -1;
    for (size_t i = 0; i < n; i += 2) {
        unsigned v;
        if (sscanf(hex + i, "%2x", &v) != 1) return -1;
        out[i / 2] = (uint8_t)v;
    }
    return (int)(n / 2);
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <nonce|b64|connect> ...\n", argv[0]);
        return 2;
    }

    // nonce <info-line>  ->  print the parsed nonce (exit 1 if the line carries none)
    if (strcmp(argv[1], "nonce") == 0 && argc == 3) {
        char nonce[128];
        if (!nats_parse_info_nonce(argv[2], nonce, sizeof(nonce))) return 1;
        printf("%s\n", nonce);
        return 0;
    }

    // b64 <hexbytes>  ->  print the standard base64 of those bytes (our encoder)
    if (strcmp(argv[1], "b64") == 0 && argc == 3) {
        uint8_t raw[256];
        int n = hex_decode(argv[2], raw, sizeof(raw));
        if (n < 0) { fprintf(stderr, "bad hex\n"); return 2; }
        char out[512];
        if (nats_b64_encode(out, sizeof(out), raw, (size_t)n) < 0) return 1;
        printf("%s\n", out);
        return 0;
    }

    // connect <name> <nkey> <sigb64>  ->  write the exact CONNECT bytes (incl. CRLF) to stdout.
    // Empty nkey/sig => anonymous CONNECT.
    if (strcmp(argv[1], "connect") == 0 && argc == 5) {
        const char *nkey = argv[3][0] ? argv[3] : NULL;
        const char *sig = argv[4][0] ? argv[4] : NULL;
        char buf[1024];
        int cn = nats_build_connect(buf, sizeof(buf), argv[2], nkey, sig);
        if (cn < 0) return 1;
        fwrite(buf, 1, (size_t)cn, stdout);  // raw: the driver sends these bytes verbatim
        return 0;
    }

    fprintf(stderr, "bad args\n");
    return 2;
}
