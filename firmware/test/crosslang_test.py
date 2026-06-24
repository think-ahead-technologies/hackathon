#!/usr/bin/env python3
# ABOUTME: Cross-language test — Python deploy_frame.py frames must parse through the C device code.
# ABOUTME: Closes the sender/device wire-format gap without a board: build frames, run the C harness, compare.

import os
import subprocess
import sys
import tempfile

# Import the Platform-side framer (pure stdlib; no deps).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "pipeline"))
from deploy_frame import build_frames  # noqa: E402

# A realistic Contract A manifest (matches the pipeline schema) with a known sha256.
MANIFEST = (
    b'{"model_id":"pdm-anomaly","target":"pse84/ethos-u55-128",'
    b'"input":{"shape":[1,49,40,1],"dtype":"int8","scale":0.018,"zero_point":-12},'
    b'"output":{"shape":[1,2],"dtype":"int8","scale":0.004,"zero_point":0},'
    b'"arena_bytes":524288,"version":"pdm-anomaly@cross",'
    b'"sha256":"8d2314a285349d73cbbbf7f79da8e07d15cdfbefc1463ed6f18e0e5da863052f"}'
)
SIG = bytes(range(64))
MODEL = bytes(i % 256 for i in range(9000))  # spans 3 chunks at the 4096 chunk size
SHA_HEX = "8d2314a285349d73cbbbf7f79da8e07d15cdfbefc1463ed6f18e0e5da863052f"


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: crosslang_test.py <harness-binary>")
        return 2
    harness = sys.argv[1]

    frames = build_frames(MANIFEST, SIG, MODEL)
    with tempfile.TemporaryDirectory() as d:
        frames_file = os.path.join(d, "frames.bin")
        out_model = os.path.join(d, "model.out")
        with open(frames_file, "wb") as f:
            f.write(b"".join(frames))

        res = subprocess.run([harness, frames_file, out_model], capture_output=True, text=True)
        if res.returncode != 0:
            print("FAIL: C harness rejected the frames:\n" + res.stderr)
            return 1

        fields = dict(line.split("=", 1) for line in res.stdout.strip().splitlines())

        # 1) The model reassembled byte-for-byte across the language boundary.
        with open(out_model, "rb") as f:
            got = f.read()
        if got != MODEL:
            print(f"FAIL: model mismatch ({len(got)} B vs {len(MODEL)} B)")
            return 1

        # 2) The C parser read the manifest the Python side sent.
        checks = {
            "model_bytes": str(len(MODEL)),
            "input_shape": "1,49,40,1",
            "arena_bytes": "524288",
            "dtype": "int8",
            "sha256": SHA_HEX,
        }
        for key, want in checks.items():
            if fields.get(key) != want:
                print(f"FAIL: {key}={fields.get(key)!r}, expected {want!r}")
                return 1

    print(f"cross-language OK: {len(frames)} frames; {len(MODEL)} B model reassembled byte-identical; "
          f"manifest parsed (shape 1,49,40,1, arena 524288, sha {SHA_HEX[:8]}…)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
