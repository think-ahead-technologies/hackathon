# ABOUTME: Tests for Contract C framing — must match the device's deploy.c wire format exactly.

import struct

import pytest

from deploy_frame import (
    FLAG_LAST,
    HDR,
    MAGIC,
    PART_MANIFEST,
    PART_MODEL,
    PART_SIG,
    build_frames,
)


def parse(frame: bytes) -> dict:
    magic, part, flags, total, off, clen = struct.unpack(HDR, frame[:20])
    return {
        "magic": magic, "part": part, "flags": flags, "total": total,
        "off": off, "clen": clen, "payload": frame[20:20 + clen],
    }


def test_header_layout_is_20_bytes_little_endian():
    assert struct.calcsize(HDR) == 20  # matches DEPLOY_HDR_BYTES in deploy.h


def test_parts_are_ordered_manifest_then_sig_then_model():
    manifest = b'{"sha256":"ab"}'
    sig = bytes(range(64))
    model = bytes(i % 256 for i in range(10_000))  # 3 chunks at 4096
    frames = [parse(f) for f in build_frames(manifest, sig, model, chunk_size=4096)]

    parts = [f["part"] for f in frames]
    assert parts == [PART_MANIFEST, PART_SIG, PART_MODEL, PART_MODEL, PART_MODEL]
    assert all(f["magic"] == MAGIC for f in frames)


def test_manifest_and_sig_single_frames():
    manifest = b'{"x":1}'
    sig = bytes(range(64))
    frames = [parse(f) for f in build_frames(manifest, sig, b"M" * 100)]
    man = next(f for f in frames if f["part"] == PART_MANIFEST)
    assert man["payload"] == manifest
    assert man["total"] == len(manifest)
    assert man["flags"] & FLAG_LAST
    s = next(f for f in frames if f["part"] == PART_SIG)
    assert s["payload"] == sig and s["clen"] == 64


def test_model_chunks_are_contiguous_and_reassemble():
    model = bytes(i % 256 for i in range(10_000))
    frames = [parse(f) for f in build_frames(b"m", bytes(64), model, chunk_size=4096)]
    chunks = [f for f in frames if f["part"] == PART_MODEL]

    # Contiguous offsets starting at 0 — exactly what deploy_rx_accept enforces.
    expected_off = 0
    for c in chunks:
        assert c["off"] == expected_off
        assert c["total"] == len(model)
        assert c["clen"] <= 4096
        expected_off += c["clen"]
    assert b"".join(c["payload"] for c in chunks) == model
    # FLAG_LAST set on the final chunk only.
    assert chunks[-1]["flags"] & FLAG_LAST
    assert not any(c["flags"] & FLAG_LAST for c in chunks[:-1])


def test_exact_multiple_of_chunk_size_sets_last_on_final_full_chunk():
    model = bytes(8192)  # exactly two 4096 chunks
    chunks = [parse(f) for f in build_frames(b"m", bytes(64), model, chunk_size=4096)
              if parse(f)["part"] == PART_MODEL]
    assert len(chunks) == 2
    assert chunks[-1]["flags"] & FLAG_LAST
    assert chunks[-1]["off"] == 4096 and chunks[-1]["clen"] == 4096


def test_rejects_bad_sig_length_and_empty_parts():
    with pytest.raises(ValueError):
        build_frames(b"m", bytes(32), b"model")   # sig must be 64 bytes
    with pytest.raises(ValueError):
        build_frames(b"", bytes(64), b"model")     # empty manifest
    with pytest.raises(ValueError):
        build_frames(b"m", bytes(64), b"")          # empty model
