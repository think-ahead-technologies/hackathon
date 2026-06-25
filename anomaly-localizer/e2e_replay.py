# ABOUTME: End-to-end replay for the anomaly-localizer — runs the real service in-process against a
# ABOUTME: live NATS, feeds edge.position + edge.anomaly, and asserts the edge.localized-anomaly join.
#
# Only needs a NATS server (not a separately-running anomaly-localizer container): it imports main.py
# and runs main.main() as a task. Bring NATS up first, then:
#   docker compose up -d nats         # or any nats://host:4222
#   python e2e_replay.py              # exits 0 on PASS, 1 on FAIL, 2 if no NATS reachable
# (Standalone like localizer/e2e_replay.py — not a pytest test; unit tests live in test_*.py.)

import asyncio
import json
import os

import nats

import main

LINE = "line1"
CONTAINER = "box1"
NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")

# Two position fixes (different segments). t_host_us = host µs of each anomaly's ts (so age = 0).
FIX_LEFT = {"t_us": 1, "t_host_us": main.ts_to_unix_us("2026-06-15T10:00:00Z"),
            "segment": "line1.left", "x": 0.0, "y": 1.83}
FIX_TOP = {"t_us": 2, "t_host_us": main.ts_to_unix_us("2026-06-15T10:00:05Z"),
           "segment": "line1.top", "x": 0.6, "y": 4.20}


def _anomaly(ts, score):
    return {"ts": ts, "container_id": CONTAINER, "model_version": "pdm-anomaly@test",
            "anomaly_score": score, "fault_class": None, "location": "spindle",
            "data_classification": "inference", "bytes": 47}


async def run(nats_url=NATS_URL):
    """Drive the scenario; return the list of edge.localized-anomaly messages received, in order."""
    # 1) start the real service in-process
    os.environ["NATS_URL"] = nats_url
    svc = asyncio.create_task(main.main())

    # 2) test client: collect the located output
    nc = await nats.connect(nats_url)
    out = []

    async def on_out(m):
        out.append(json.loads(m.data))

    await nc.subscribe(f"edge.localized-anomaly.{LINE}.{CONTAINER}", cb=on_out)
    await nc.flush()
    await asyncio.sleep(0.3)                         # let the service's subscriptions register

    async def pub(kind, msg):
        await nc.publish(f"edge.{kind}.{LINE}.{CONTAINER}", json.dumps(msg).encode())
        await nc.flush()
        await asyncio.sleep(0.15)                    # let the service callback run

    # 3) scenario
    await pub("anomaly", _anomaly("2026-06-14T09:00:00Z", 0.40))   # [0] anomaly BEFORE any fix
    await pub("position", FIX_LEFT)                                # fix arrives (no output)
    await pub("anomaly", _anomaly("2026-06-15T10:00:00Z", 0.83))   # [1] located at left
    await pub("position", FIX_TOP)                                 # newer fix
    await pub("anomaly", _anomaly("2026-06-15T10:00:05Z", 0.91))   # [2] located at top (last wins)

    await nc.flush()
    await asyncio.sleep(0.5)
    await nc.drain()
    svc.cancel()
    try:
        await svc
    except asyncio.CancelledError:
        pass
    return out


def check(out):
    """Assert the join; raise AssertionError on any mismatch. Returns out for convenience."""
    assert len(out) == 3, f"expected 3 located anomalies, got {len(out)}"

    # [0] anomaly before any fix -> still emitted, location null, never dropped
    a0 = out[0]
    assert a0["anomaly_score"] == 0.40
    assert a0["segment"] is None and a0["x"] is None and a0["y"] is None
    assert a0["pos_t_host_us"] is None and a0["pos_age_ms"] is None
    assert a0["data_classification"] == "inference"

    # [1] located at the left fix, age 0 (anomaly ts == fix host time)
    a1 = out[1]
    assert a1["segment"] == "line1.left"
    assert a1["x"] == 0.0 and a1["y"] == 1.83
    assert a1["pos_t_host_us"] == FIX_LEFT["t_host_us"]
    assert a1["pos_age_ms"] == 0
    assert a1["location"] == "spindle"               # machine component pass-through, untouched
    assert a1["bytes"] == len(json.dumps(a1).encode())

    # [2] last-known-position wins -> the newer top fix, not left
    a2 = out[2]
    assert a2["segment"] == "line1.top"
    assert a2["x"] == 0.6 and a2["y"] == 4.20
    assert a2["anomaly_score"] == 0.91
    return out


async def _nats_up(nats_url=NATS_URL):
    try:
        nc = await asyncio.wait_for(
            nats.connect(nats_url, connect_timeout=1, allow_reconnect=False,
                         max_reconnect_attempts=0),
            timeout=3,
        )
        await nc.drain()
        return True
    except Exception:  # noqa: BLE001 — no server / timeout -> caller skips
        return False


async def _amain():
    if not await _nats_up():
        print(f"E2E SKIP — no NATS at {NATS_URL}. Start one: docker compose up -d nats", flush=True)
        raise SystemExit(2)
    out = await run()
    print(f"located anomalies received: {len(out)}", flush=True)
    for i, m in enumerate(out):
        print(f"  [{i}] score={m['anomaly_score']} segment={m['segment']} "
              f"x={m['x']} y={m['y']} age_ms={m['pos_age_ms']} class={m['data_classification']}")
    try:
        check(out)
    except AssertionError as exc:
        print(f"\nE2E FAIL — {exc}", flush=True)
        raise SystemExit(1)
    print("\nE2E PASS", flush=True)
    raise SystemExit(0)


if __name__ == "__main__":
    asyncio.run(_amain())
