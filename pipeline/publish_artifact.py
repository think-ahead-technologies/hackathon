# ABOUTME: Stream a signed model to devices as Contract C frames over NATS (gateway-side bridge).
# ABOUTME: Reads {manifest.json,manifest.sig,model_int8_vela.tflite} pulled from the OCI registry.

import asyncio
import os
import pathlib
import ssl

import nats

from deploy_frame import build_frames

NATS_URL = os.environ.get("NATS_URL", "nats://nats:4222")
# Per-identity nkey seed (CRA unique-credential auth). Unset on the open demo fabric.
NATS_NKEY_SEED = os.environ.get("NATS_NKEY_SEED")
# CA that signed the NATS server cert (CRA confidentiality in transit). Unset -> plaintext demo.
NATS_CA_FILE = os.environ.get("NATS_CA_FILE")
NATS_TLS_HOSTNAME = os.environ.get("NATS_TLS_HOSTNAME", "nats")
LINE = os.environ.get("LINE", "line1")
BUILD = pathlib.Path(os.environ.get("BUILD_DIR", "build"))
SUBJECT = f"models.{LINE}.artifact"


async def main() -> None:
    manifest = (BUILD / "manifest.json").read_bytes()
    sig = (BUILD / "manifest.sig").read_bytes()        # raw 64-byte ECDSA-P256 (der2raw.py output)
    model = (BUILD / "model_int8_vela.tflite").read_bytes()

    frames = build_frames(manifest, sig, model)

    auth = {}
    if NATS_NKEY_SEED:
        auth["nkeys_seed"] = NATS_NKEY_SEED
    if NATS_CA_FILE:
        auth["tls"] = ssl.create_default_context(cafile=NATS_CA_FILE)
        auth["tls_hostname"] = NATS_TLS_HOSTNAME
    nc = await nats.connect(NATS_URL, **auth)
    for frame in frames:
        await nc.publish(SUBJECT, frame)
    await nc.flush()
    await nc.drain()

    print(f"published {len(frames)} Contract C frames to {SUBJECT} "
          f"(manifest {len(manifest)} B, sig {len(sig)} B, model {len(model)} B)")


if __name__ == "__main__":
    asyncio.run(main())
