# ABOUTME: Convert a base64 DER ECDSA-P256 signature (cosign sign-blob) to raw r||s (64 bytes).
# ABOUTME: The device verifies with PSA psa_verify_hash, which wants P1363 raw, not ASN.1 DER.

import argparse
import base64
import pathlib

INT_LEN = 32  # P-256: each of r, s is 32 bytes


def _read_len(der: bytes, idx: int) -> tuple[int, int]:
    first = der[idx]
    idx += 1
    if first < 0x80:
        return first, idx
    n = first & 0x7F
    val = int.from_bytes(der[idx:idx + n], "big")
    return val, idx + n


def _read_int(der: bytes, idx: int) -> tuple[bytes, int]:
    if der[idx] != 0x02:
        raise ValueError("expected INTEGER")
    idx += 1
    length, idx = _read_len(der, idx)
    val = der[idx:idx + length].lstrip(b"\x00")  # drop the DER sign byte / leading zeros
    return val, idx + length


def _pad(b: bytes, n: int) -> bytes:
    if len(b) > n:
        raise ValueError("integer wider than the curve")
    return b"\x00" * (n - len(b)) + b


def der_to_raw(der: bytes, int_len: int = INT_LEN) -> bytes:
    """SEQUENCE { INTEGER r, INTEGER s } -> r||s, each left-padded to int_len bytes."""
    if not der or der[0] != 0x30:
        raise ValueError("not a DER SEQUENCE")
    _, idx = _read_len(der, 1)
    r, idx = _read_int(der, idx)
    s, idx = _read_int(der, idx)
    return _pad(r, int_len) + _pad(s, int_len)


def main() -> int:
    ap = argparse.ArgumentParser(description="Base64 DER ECDSA-P256 sig -> raw r||s.")
    ap.add_argument("--in", dest="inp", required=True, help="cosign sign-blob output (base64 DER)")
    ap.add_argument("--out", required=True, help="raw 64-byte signature for the device")
    args = ap.parse_args()

    der = base64.b64decode(pathlib.Path(args.inp).read_text().strip())
    raw = der_to_raw(der)
    pathlib.Path(args.out).write_bytes(raw)
    print(f"device sig: {args.out}  ({len(raw)} bytes raw r||s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
