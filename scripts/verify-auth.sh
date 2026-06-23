#!/usr/bin/env bash
# ABOUTME: Proves the provisioned NATS config over TLS: authorized pub works, cross-device/anonymous/
# ABOUTME: untrusted-TLS are denied. Boots a throwaway nats-server with the generated conf + chain — no stack.
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONF="$DIR/nats/nats-server.conf"
CA="$DIR/nats/tls/ca.pem"
NET="cra-verify-$$"
NATS="cra-verify-nats-$$"
NATS_IMG="nats:2.14.2-alpine"
BOX_IMG="natsio/nats-box:0.14.5"

if [ ! -f "$CONF" ] || [ ! -f "$CA" ]; then
  echo "missing $CONF or $CA — run 'make provision' first" >&2
  exit 1
fi

cleanup() { docker stop "$NATS" >/dev/null 2>&1 || true; docker network rm "$NET" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker network create "$NET" >/dev/null
# Alias the server as 'nats' so the TLS hostname matches the server cert's SAN.
docker run -d --rm --name "$NATS" --network "$NET" --network-alias nats \
  -v "$DIR/nats":/etc/nats:ro "$NATS_IMG" -c /etc/nats/nats-server.conf >/dev/null
sleep 2

CA_IN=/etc/nats/tls/ca.pem
SRV=nats://nats:4222
# The nats CLI exits non-zero on a denied publish, so capture output first and grep the
# string — piping into `grep` directly would let pipefail mask a matched-but-denied result.
box() { docker run --rm --network "$NET" -v "$DIR/nats":/etc/nats:ro "$BOX_IMG" "$@" 2>&1 || true; }
fail=0
check() {  # <label> <expected-substring> -- <cli args...>
  local label="$1" want="$2"; shift 3
  echo "== $label =="
  if box "$@" | grep -q "$want"; then echo "  PASS"; else echo "  FAIL (expected '$want')"; fail=1; fi
}

check "1. AUTHORIZED over TLS: cnc-7 publishes its own subject" "Published" \
  -- nats --server "$SRV" --tlsca "$CA_IN" --nkey /etc/nats/creds/cnc-7.nk pub edge.line1.cnc-7 ok
check "2. DENIED: cnc-7 cannot publish as press-3" "Permissions Violation" \
  -- nats --server "$SRV" --tlsca "$CA_IN" --nkey /etc/nats/creds/cnc-7.nk pub edge.line1.press-3 spoof
check "3. DENIED: anonymous client (TLS ok, no creds)" "Authorization Violation" \
  -- nats --server "$SRV" --tlsca "$CA_IN" pub edge.line1.cnc-7 anon
# 4. TLS enforced: without the CA the server cert is untrusted -> the handshake fails (no publish).
echo "== 4. TLS ENFORCED: untrusted cert is rejected =="
if box nats --server "$SRV" pub edge.line1.cnc-7 notls | grep -qiE "certificate|x509|tls|unknown authority"; then
  echo "  PASS"
else
  echo "  FAIL — an untrusted/plaintext connection was not rejected at the TLS layer"; fail=1
fi

[ "$fail" -eq 0 ] \
  && echo "SECURE FABRIC VERIFIED — TLS + unique-credential auth + least-privilege enforced." \
  || { echo "SECURE CHECK FAILED."; exit 1; }
