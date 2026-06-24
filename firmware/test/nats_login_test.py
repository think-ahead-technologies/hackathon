#!/usr/bin/env python3
# ABOUTME: Live cross-test — our C nats_proto functions drive a real NATS nkey login (no board).
# ABOUTME: Boots a throwaway nats-server from the dashboard's provisioning, then asserts the server's verdict.

import os
import socket
import subprocess
import sys
import tempfile
import time

# Reuse the dashboard's real provisioning + nkey codec (also validates them across the boundary).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "dashboard", "pipeline"))
import nkey  # noqa: E402
import provision  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

PORT = 14222
NATS_IMAGE = "nats:2.14.2-alpine"
CONTAINER = f"nats-login-test-{os.getpid()}"
FLEET = {"devices": [{"id": "cnc-7", "line": "line1"}, {"id": "press-3", "line": "line1"}],
         "services": []}


def sign_nonce(seed: str, nonce: bytes) -> bytes:
    """Software stand-in for hal_nkey_sign: Ed25519-sign the nonce with the device seed."""
    _, payload = nkey.decode(seed)
    raw_seed = payload[1:]  # seed carries two prefix bytes; the 32-byte key seed follows
    return Ed25519PrivateKey.from_private_bytes(raw_seed).sign(nonce)


def harness(binary: str, *args: str) -> bytes:
    res = subprocess.run([binary, *args], capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(f"harness {args!r} failed: {res.stderr.decode().strip()}")
    return res.stdout


def recv_some(sock: socket.socket, timeout: float = 2.0) -> bytes:
    sock.settimeout(timeout)
    try:
        return sock.recv(4096)
    except socket.timeout:
        return b""


def read_info(sock: socket.socket) -> bytes:
    """Read the server's INFO greeting line (up to CRLF)."""
    buf = b""
    sock.settimeout(2.0)
    while b"\r\n" not in buf:
        chunk = sock.recv(1)
        if not chunk:
            break
        buf += chunk
    return buf


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: nats_login_test.py <harness-binary>")
        return 2
    h = sys.argv[1]

    # 1) Provision a one-line fleet and render the real server auth config.
    identities = provision.build_identities(FLEET)
    cnc = next(i for i in identities if i["name"] == "cnc-7")
    conf = provision.render_server_config(identities)

    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "nats-server.conf"), "w") as f:
            f.write(conf)

        subprocess.run(["docker", "run", "-d", "--rm", "--name", CONTAINER,
                        "-p", f"{PORT}:4222", "-v", f"{d}:/etc/nats:ro", NATS_IMAGE,
                        "-c", "/etc/nats/nats-server.conf"], check=True, capture_output=True)
        try:
            time.sleep(2)

            # 2) Authorized login: parse nonce (C), sign it, base64 it (C), build CONNECT (C), send.
            s = socket.create_connection(("127.0.0.1", PORT), timeout=2.0)
            info = read_info(s)
            assert info.startswith(b"INFO "), f"expected INFO, got {info!r}"

            nonce = harness(h, "nonce", info.decode().strip()).strip()
            assert nonce, "C parser found no nonce in INFO (auth not requested?)"
            sig = sign_nonce(cnc["seed"], nonce)
            sigb64 = harness(h, "b64", sig.hex()).strip().decode()
            connect = harness(h, "connect", "cnc-7", cnc["public"], sigb64)
            s.sendall(connect)
            s.sendall(b"PING\r\n")
            resp = recv_some(s)
            assert b"PONG" in resp and b"-ERR" not in resp, \
                f"authorized login rejected: {resp!r}"

            # 3) Least-privilege: own subject OK, another device's subject denied.
            s.sendall(b"PUB edge.line1.cnc-7 2\r\nhi\r\n")
            s.sendall(b"PING\r\n")
            resp = recv_some(s)
            assert b"PONG" in resp and b"-ERR" not in resp, \
                f"publish to own subject was rejected: {resp!r}"

            s.sendall(b"PUB edge.line1.press-3 2\r\nhi\r\n")
            resp = recv_some(s)
            assert b"Permissions Violation" in resp, \
                f"expected a permissions violation publishing as press-3, got {resp!r}"
            s.close()

            # 4) Anonymous CONNECT (no creds) must be rejected.
            a = socket.create_connection(("127.0.0.1", PORT), timeout=2.0)
            read_info(a)
            a.sendall(harness(h, "connect", "cnc-7", "", ""))  # empty nkey/sig => anonymous
            a.sendall(b"PING\r\n")
            resp = recv_some(a)
            assert b"Authorization Violation" in resp, \
                f"anonymous client should be rejected, got {resp!r}"
            a.close()
        finally:
            subprocess.run(["docker", "stop", CONTAINER], capture_output=True)

    print("nats login OK: our C CONNECT authenticated against a real nats-server; "
          "own-subject publish accepted, cross-device denied, anonymous denied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
