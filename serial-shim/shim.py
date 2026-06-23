# ABOUTME: Reads Contract B inference lines off the board's USB-UART and publishes them to NATS.
# ABOUTME: The device→fabric transport for the real-device path; fakegen's stand-in once a board is attached.

import asyncio
import json
import os
import ssl

import nats
import serial
from opentelemetry import propagate, trace
from opentelemetry.trace import SpanKind

NATS_URL = os.environ.get("NATS_URL", "nats://nats:4222")
# Per-identity nkey seed (CRA unique-credential auth). Unset on the open demo fabric.
NATS_NKEY_SEED = os.environ.get("NATS_NKEY_SEED")
# CA that signed the NATS server cert (CRA confidentiality in transit). Unset -> plaintext demo.
NATS_CA_FILE = os.environ.get("NATS_CA_FILE")
NATS_TLS_HOSTNAME = os.environ.get("NATS_TLS_HOSTNAME", "nats")
LINE = os.environ.get("LINE", "line1")
# The KitProg3 USB-UART bridge enumerates as a serial port: /dev/tty.usbmodem* (macOS)
# or /dev/ttyACM* (Linux). 115200 8N1 is the board's console default — match the firmware.
SERIAL_PORT = os.environ.get("SERIAL_PORT", "/dev/ttyACM0")
SERIAL_BAUD = int(os.environ.get("SERIAL_BAUD", "115200"))

tracer = trace.get_tracer("serial-shim")


def setup_telemetry(service_name: str) -> bool:
    """Wire OTLP trace export to the collector if configured."""
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        print("[otel] no endpoint set; telemetry disabled", flush=True)
        return False
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": service_name})
    tp = TracerProvider(resource=resource)
    tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tp)
    print(f"[otel] tracing -> {os.environ['OTEL_EXPORTER_OTLP_ENDPOINT']}", flush=True)
    return True


def parse_line(raw: bytes) -> dict | None:
    """Parse one serial line into a Contract B inference message, or None to skip it.

    Returns None for anything that is not a usable inference object — blank lines,
    boot banners, baud-mismatch gibberish, non-objects, or messages missing the
    fields the bridge requires. Skipping (not raising) keeps the shim resilient to
    the noise every real serial stream carries.
    """
    try:
        text = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if not text:
        return None
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    if "container_id" not in obj or "anomaly_score" not in obj:
        return None
    try:
        obj["anomaly_score"] = float(obj["anomaly_score"])
    except (TypeError, ValueError):
        return None
    return obj


def edge_subject(line: str, container_id: str) -> str:
    """Device data-plane subject the Vector boundary gateway consumes (edge.>)."""
    return f"edge.{line}.{container_id}"


def to_edge_payload(msg: dict, wire_bytes: int) -> dict:
    """Shape a parsed message for the boundary gateway: tag its class, record its wire size."""
    out = dict(msg)
    out["data_classification"] = "inference"  # the only class Vector lets cross the boundary
    out["bytes"] = wire_bytes                   # real egress size, for the boundary audit metric
    return out


async def publish_message(nc, subject: str, msg: dict) -> None:
    """Publish one inference message, carrying trace context in the body across the Vector hop.

    Continues the device's trace if the firmware emitted `traceparent`; otherwise opens
    a fresh CLIENT span so the device→bridge edge still appears in the service graph.
    """
    parent = propagate.extract({k: v for k in ("traceparent", "tracestate") if (v := msg.get(k))})
    with tracer.start_as_current_span("fabric.publish", context=parent, kind=SpanKind.CLIENT) as span:
        span.set_attribute("data.classification", "inference")
        span.set_attribute("container_id", str(msg.get("container_id", "")))
        carrier: dict[str, str] = {}
        propagate.inject(carrier)
        msg["traceparent"] = carrier.get("traceparent")
        msg["tracestate"] = carrier.get("tracestate")
        await nc.publish(subject, json.dumps(msg).encode())


async def main() -> None:
    setup_telemetry("serial-shim")
    auth = {}
    if NATS_NKEY_SEED:
        auth["nkeys_seed"] = NATS_NKEY_SEED
    if NATS_CA_FILE:
        auth["tls"] = ssl.create_default_context(cafile=NATS_CA_FILE)
        auth["tls_hostname"] = NATS_TLS_HOSTNAME
    nc = await nats.connect(NATS_URL, reconnect_time_wait=2, max_reconnect_attempts=-1, **auth)
    port = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1)
    print(f"[serial-shim] {SERIAL_PORT}@{SERIAL_BAUD} -> edge.{LINE}.* (Contract B over NATS)", flush=True)

    loop = asyncio.get_event_loop()
    while True:
        # readline() blocks; run it off the event loop so reconnects/shutdown stay responsive.
        raw = await loop.run_in_executor(None, port.readline)
        msg = parse_line(raw)
        if msg is None:
            continue  # idle read, boot banner, or junk — skip and keep reading
        payload = to_edge_payload(msg, len(raw.strip()))
        await publish_message(nc, edge_subject(LINE, msg["container_id"]), payload)


if __name__ == "__main__":
    asyncio.run(main())
