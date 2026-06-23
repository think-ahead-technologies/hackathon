# ABOUTME: Packages a gated model into a signed artifact — manifest (model contract) + sha256.
# ABOUTME: The Makefile then cosign-signs the artifact; together they are the provenance record.

import argparse
import hashlib
import json
import pathlib

# Representative size for the placeholder standing in for ML's compiled Vela .tflite.
PLACEHOLDER_KIB = 312


def ensure_artifact(path: pathlib.Path) -> bytes:
    """Use ML's real Vela .tflite if present; otherwise write a deterministic placeholder."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        chunk = b"PLACEHOLDER-VELA-TFLITE\n"          # 24 bytes
        path.write_bytes(chunk * ((PLACEHOLDER_KIB * 1024) // len(chunk)))
    return path.read_bytes()


def build_manifest(meta: dict, version: str, data: bytes) -> dict:
    """The model contract the firmware validates on load (Contract A + provenance)."""
    manifest = {k: v for k, v in meta.items() if not k.startswith("_")}
    manifest["version"] = version
    manifest["sha256"] = hashlib.sha256(data).hexdigest()
    manifest["flatbuffer_bytes"] = len(data)
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description="Package + manifest a gated model artifact.")
    ap.add_argument("--meta", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", required=True, help="output dir for artifact + manifest")
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    artifact = out / "model_int8_vela.tflite"
    data = ensure_artifact(artifact)
    manifest = build_manifest(json.load(open(args.meta)), args.version, data)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    print(f"artifact:  {artifact}  ({len(data) / 1024:.0f} KiB)")
    print(f"sha256:    {manifest['sha256']}")
    print(f"manifest:  {out / 'manifest.json'}  (version {args.version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
