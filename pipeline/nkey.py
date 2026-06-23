# ABOUTME: NATS nkey (Ed25519) codec — encodes/decodes the seed + public key wire format.
# ABOUTME: Real crypto, no service: each board gets a unique seed, so there is no shared default credential.

import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# NATS nkey prefix bytes (entity type, pre-shifted by 3 as in the nats-io/nkeys spec).
PREFIX_SEED = 18 << 3   # encodes as 'S'
PREFIX_USER = 20 << 3   # encodes as 'U'


def _crc16_table() -> list[int]:
    """CRC-16/XMODEM table (poly 0x1021, init 0) — the checksum nkeys append, little-endian."""
    table = []
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
            crc &= 0xFFFF
        table.append(crc)
    return table


_CRC16_TABLE = _crc16_table()


def crc16(data: bytes) -> int:
    """CRC-16/XMODEM over data."""
    crc = 0
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ _CRC16_TABLE[((crc >> 8) ^ b) & 0xFF]
    return crc


def _b32encode(raw: bytes) -> str:
    """RFC4648 base32, uppercase, no padding — the nkey text encoding."""
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _b32decode(text: str) -> bytes:
    pad = (-len(text)) % 8
    return base64.b32decode(text + "=" * pad)


def encode_public(prefix: int, raw_key: bytes) -> str:
    """Encode a 32-byte raw public key as an nkey public string (e.g. 'U...')."""
    if len(raw_key) != 32:
        raise ValueError(f"public key must be 32 bytes, got {len(raw_key)}")
    body = bytes([prefix]) + raw_key
    return _b32encode(body + crc16(body).to_bytes(2, "little"))


def encode_seed(public_prefix: int, raw_seed: bytes) -> str:
    """Encode a 32-byte Ed25519 seed as an nkey seed string (e.g. user seed 'SU...')."""
    if len(raw_seed) != 32:
        raise ValueError(f"seed must be 32 bytes, got {len(raw_seed)}")
    # Two prefix bytes pack the seed marker and the entity type across a base32 boundary,
    # so a user seed prints as the human-readable 'SU' pair.
    b1 = PREFIX_SEED | (public_prefix >> 5)
    b2 = (public_prefix & 0b00011111) << 3
    body = bytes([b1, b2]) + raw_seed
    return _b32encode(body + crc16(body).to_bytes(2, "little"))


def decode(text: str) -> tuple[int, bytes]:
    """Decode an nkey string, verifying its CRC. Returns (prefix_byte, payload_bytes)."""
    raw = _b32decode(text)
    body, checksum = raw[:-2], int.from_bytes(raw[-2:], "little")
    if crc16(body) != checksum:
        raise ValueError("nkey checksum mismatch — corrupt or truncated credential")
    return body[0], body[1:]


def create_user() -> tuple[str, str]:
    """Generate a fresh, unique Ed25519 user identity. Returns (seed, public_nkey)."""
    private = Ed25519PrivateKey.generate()
    raw_seed = private.private_bytes_raw()
    raw_public = private.public_key().public_bytes_raw()
    return encode_seed(PREFIX_USER, raw_seed), encode_public(PREFIX_USER, raw_public)


def public_from_seed(seed: str) -> str:
    """Derive the public nkey from a user seed (used to verify a creds file matches the config)."""
    prefix, payload = decode(seed)
    if prefix != (PREFIX_SEED | (PREFIX_USER >> 5)):
        raise ValueError("not a user seed")
    # A seed carries two prefix bytes; decode() strips the first, drop the second to get the 32-byte seed.
    raw_seed = payload[1:]
    public = Ed25519PrivateKey.from_private_bytes(raw_seed).public_key()
    return encode_public(PREFIX_USER, public.public_bytes_raw())
