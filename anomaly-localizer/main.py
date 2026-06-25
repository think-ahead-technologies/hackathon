# ABOUTME: Anomaly-localizer — joins edge.anomaly (on-device inference score) with edge.position
# ABOUTME: (localizer floor-map fix) and publishes the located alert to
# ABOUTME: edge.localized-anomaly.<line>.<container> (localized-anomaly-data contract).
#
# Last-known-position join: keep the most recent position fix per <line>.<container> and stamp it onto
# each incoming anomaly. Streaming, fire-and-forget — no windowed time alignment. An anomaly is never
# dropped: if no fix has arrived yet, location fields go out null. Classified "inference" so it crosses
# the Vector boundary like the bare anomaly.

import asyncio
import json
import os
import signal
import ssl
from datetime import datetime, timezone

import nats

# --- config ---------------------------------------------------------------

NATS_URL = os.environ.get("NATS_URL", "nats://nats:4222")
# Per-identity nkey seed (CRA unique-credential auth). Unset on the open demo fabric.
NATS_NKEY_SEED = os.environ.get("NATS_NKEY_SEED")
# CA that signed the NATS server cert (CRA confidentiality in transit). Unset -> plaintext demo.
NATS_CA_FILE = os.environ.get("NATS_CA_FILE")
NATS_TLS_HOSTNAME = os.environ.get("NATS_TLS_HOSTNAME", "nats")

ANOMALY_SUBJECT = os.environ.get("ANOMALY_SUBJECT", "edge.anomaly.*.*")
POSITION_SUBJECT = os.environ.get("POSITION_SUBJECT", "edge.position.*.*")
# Output namespace: edge.localized-anomaly.<line>.<container>.
OUT_PREFIX = os.environ.get("OUT_PREFIX", "edge.localized-anomaly")


# --- pure logic (unit-tested in test_anomaly_localizer.py) ----------------

def parse_subject(subject):
    """edge.<kind>.<line>.<container> -> (line, container). <kind> may contain hyphens."""
    _, _, line, container = subject.split(".", 3)
    return line, container


def ts_to_unix_us(value):
    """ISO-8601 (with trailing Z or offset) -> unix microseconds. None if unparseable/absent."""
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000)


def localize(anomaly: dict, fix: dict | None) -> dict:
    """Stamp the latest position `fix` onto an `anomaly` payload -> localized-anomaly message.

    Pass every anomaly field through; add segment/x/y/pos_t_host_us/pos_age_ms from the fix (null when
    there is no fix yet). Force data_classification="inference" and recompute `bytes` as the wire size.
    """
    out = dict(anomaly)                                  # pass-through, including any traceparent/etc.

    if fix is not None:
        out["segment"] = fix.get("segment")
        out["x"] = fix.get("x")
        out["y"] = fix.get("y")
        out["pos_t_host_us"] = fix.get("t_host_us")
        anom_us = ts_to_unix_us(anomaly.get("ts"))
        pos_us = fix.get("t_host_us")
        out["pos_age_ms"] = (
            int((anom_us - pos_us) / 1000) if anom_us is not None and pos_us is not None else None
        )
    else:
        out["segment"] = out["x"] = out["y"] = None
        out["pos_t_host_us"] = out["pos_age_ms"] = None

    out["data_classification"] = "inference"            # gate field — must be set or Vector drops it
    # `bytes` is the wire size of this very message -> self-referential. Iterate to a fixpoint (the
    # digit count of `bytes` itself affects the length); 2 passes always converge for this payload.
    out["bytes"] = 0
    for _ in range(3):
        n = len(json.dumps(out).encode())
        if n == out["bytes"]:
            break
        out["bytes"] = n
    return out


# --- wiring ----------------------------------------------------------------

async def main():
    auth = {}
    if NATS_NKEY_SEED:
        auth["nkeys_seed"] = NATS_NKEY_SEED
    if NATS_CA_FILE:
        auth["tls"] = ssl.create_default_context(cafile=NATS_CA_FILE)
        auth["tls_hostname"] = NATS_TLS_HOSTNAME
    nc = await nats.connect(NATS_URL, reconnect_time_wait=2, max_reconnect_attempts=-1, **auth)

    last_fix = {}                                        # (line, container) -> latest position dict

    async def on_position(m):
        try:
            line, container = parse_subject(m.subject)
            last_fix[(line, container)] = json.loads(m.data)
        except Exception as exc:  # noqa: BLE001 — one bad fix must not kill the subscription
            print(f"[anomaly-localizer] position dropped: {exc}", flush=True)

    async def on_anomaly(m):
        try:
            line, container = parse_subject(m.subject)
            anomaly = json.loads(m.data)
            fix = last_fix.get((line, container))
            out = localize(anomaly, fix)
            await nc.publish(f"{OUT_PREFIX}.{line}.{container}", json.dumps(out).encode())
        except Exception as exc:  # noqa: BLE001
            print(f"[anomaly-localizer] anomaly dropped: {exc}", flush=True)

    await nc.subscribe(POSITION_SUBJECT, cb=on_position)
    await nc.subscribe(ANOMALY_SUBJECT, cb=on_anomaly)
    print(f"[anomaly-localizer] {ANOMALY_SUBJECT} (+ {POSITION_SUBJECT}) "
          f"-> {OUT_PREFIX}.<line>.<container>", flush=True)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await nc.drain()


if __name__ == "__main__":
    asyncio.run(main())
