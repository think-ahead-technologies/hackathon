# ABOUTME: Chunk-and-frame a packaged model into Contract C frames for on-device reassembly.
# ABOUTME: Wire format mirrors the firmware's deploy.h/deploy.c exactly — keep the two in lockstep.

import struct

# Must match firmware/include/deploy.h.
MAGIC = 0x43444331  # 'C''D''C''1'
PART_MANIFEST = 1
PART_SIG = 2
PART_MODEL = 3
FLAG_LAST = 0x0001
HDR = "<IHHIII"  # magic, part, flags, total_len, offset, chunk_len (little-endian, 20 bytes)

# The device reads a frame into a (DEPLOY_HDR_BYTES + 4096) buffer, so a chunk payload must
# not exceed 4096 bytes.
DEFAULT_CHUNK = 4096


def _frames_for_part(part: int, data: bytes, chunk_size: int) -> list[bytes]:
    """Split one part into [header || payload] frames; FLAG_LAST marks the final chunk."""
    frames = []
    total = len(data)
    off = 0
    while off < total:
        clen = min(chunk_size, total - off)
        flags = FLAG_LAST if off + clen >= total else 0
        header = struct.pack(HDR, MAGIC, part, flags, total, off, clen)
        frames.append(header + data[off:off + clen])
        off += clen
    return frames


def build_frames(manifest: bytes, sig: bytes, model: bytes,
                 chunk_size: int = DEFAULT_CHUNK) -> list[bytes]:
    """The full Contract C frame sequence: manifest, then sig, then the chunked model.

    Order matters: the device validates the manifest signature before it will accept any
    model chunk, so manifest + sig must arrive first.
    """
    if not manifest:
        raise ValueError("manifest must be non-empty")
    if len(sig) != 64:
        raise ValueError("sig must be 64 bytes (raw ECDSA-P256 r||s)")
    if not model:
        raise ValueError("model must be non-empty")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    return (
        _frames_for_part(PART_MANIFEST, manifest, chunk_size)
        + _frames_for_part(PART_SIG, sig, chunk_size)
        + _frames_for_part(PART_MODEL, model, chunk_size)
    )
