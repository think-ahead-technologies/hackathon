# ABOUTME: Unit tests for the DER->raw ECDSA signature converter (cosign sign-blob -> device sig).

import pytest

from der2raw import der_to_raw


def make_der(r: bytes, s: bytes) -> bytes:
    """Encode SEQUENCE { INTEGER r, INTEGER s } the way an ECDSA DER signature is."""
    def enc_int(x: bytes) -> bytes:
        if x[0] & 0x80:          # high bit set -> prepend a 0x00 sign byte
            x = b"\x00" + x
        return b"\x02" + bytes([len(x)]) + x

    body = enc_int(r) + enc_int(s)
    return b"\x30" + bytes([len(body)]) + body


def test_round_trips_two_full_width_integers():
    r = bytes(range(1, 33))           # 32 bytes, no high bit
    s = bytes(range(33, 65))
    raw = der_to_raw(make_der(r, s))
    assert raw == r + s
    assert len(raw) == 64


def test_strips_der_sign_byte_on_high_bit_integer():
    r = b"\x80" + bytes(31)            # high bit set -> DER adds a 0x00, we must strip it
    s = bytes(range(33, 65))
    raw = der_to_raw(make_der(r, s))
    assert len(raw) == 64
    assert raw[:32] == r              # back to exactly 32 bytes
    assert raw[32:] == s


def test_left_pads_short_integers_to_32_bytes():
    r = b"\x2a"                        # a 1-byte integer
    s = b"\x01\x02"
    raw = der_to_raw(make_der(r, s))
    assert len(raw) == 64
    assert raw[:32] == bytes(31) + b"\x2a"
    assert raw[32:] == bytes(30) + b"\x01\x02"


def test_rejects_non_sequence():
    with pytest.raises(ValueError):
        der_to_raw(b"\x02\x01\x01")    # an INTEGER, not a SEQUENCE
