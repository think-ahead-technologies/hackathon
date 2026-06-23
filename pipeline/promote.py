# ABOUTME: GitOps reconciler — converges the running line to the desired model version.
# ABOUTME: Reads desired state, compares to observed (deployed events), verifies signature, deploys.

import json
import os
import pathlib
import subprocess
import sys
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
BRIDGE = os.environ.get("BRIDGE_URL", "http://localhost:8000")
LINE = os.environ.get("LINE", "line1")
COSIGN_IMAGE = os.environ.get("COSIGN_IMAGE", "gcr.io/projectsigstore/cosign:v2.5.3")
REGISTRY = os.environ.get("REGISTRY", "zot:5000")
NET = os.environ.get("NET", "dashboard_default")


def ref_for(version: str) -> str:
    """OCI ref in the sovereign registry for a model version (model_id@tag)."""
    model_id, _, tag = version.partition("@")
    return f"{REGISTRY}/models/{model_id}:{tag or 'latest'}"


def _get(path: str):
    with urllib.request.urlopen(f"{BRIDGE}{path}", timeout=5) as r:
        return json.load(r)


def _post(path: str, body: dict):
    req = urllib.request.Request(
        f"{BRIDGE}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def observed_version() -> str | None:
    """The currently-deployed version per our system of record (model_events)."""
    for e in _get("/model_events"):
        if e["event_type"] == "deployed":
            return e["model_version"]
    return None


def verify_signature(version: str) -> None:
    """Refuse to deploy a registry artifact whose signature doesn't verify (root-of-trust analog)."""
    if not (HERE / "cosign.pub").exists():
        raise SystemExit("missing cosign.pub — run `make keygen` first")
    ref = ref_for(version)
    # Offline verification of the signed artifact in the sovereign registry (no public Rekor).
    subprocess.run(
        ["docker", "run", "--rm", "--network", NET, "-v", f"{HERE}:/work", COSIGN_IMAGE,
         "verify", "--allow-insecure-registry", "--insecure-ignore-tlog=true",
         "--key", "/work/cosign.pub", ref],
        check=True,
    )


def main() -> int:
    desired = json.load(open(HERE / "desired-state.json"))["lines"][LINE]
    observed = observed_version()
    print(f"desired:  {desired}")
    print(f"observed: {observed or '(none deployed)'}")

    if desired == observed:
        print("in sync — nothing to reconcile.")
        return 0

    print("drift detected — verifying registry signature before deploy…")
    verify_signature(desired)
    print("signature OK — publishing Contract C (models.%s.deploy)…" % LINE)
    _post("/deploy", {"line": LINE, "model_version": desired})
    print(f"reconciled: {observed or '(none)'} -> {desired}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
