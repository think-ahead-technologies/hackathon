# ABOUTME: Dev-only publisher standing in for the device's full data plane.
# ABOUTME: Emits raw/features/inference to edge.* so the Vector boundary gateway can enforce.

import asyncio
import datetime
import json
import math
import os
import ssl

import nats
from opentelemetry import propagate, trace
from opentelemetry.trace import SpanKind

NATS_URL = os.environ.get("NATS_URL", "nats://nats:4222")
# Per-identity nkey seed (CRA unique-credential auth). Unset on the open demo fabric.
NATS_NKEY_SEED = os.environ.get("NATS_NKEY_SEED")
# CA that signed the NATS server cert (CRA confidentiality in transit). Unset -> plaintext demo.
NATS_CA_FILE = os.environ.get("NATS_CA_FILE")
NATS_TLS_HOSTNAME = os.environ.get("NATS_TLS_HOSTNAME", "nats")
LINE = os.environ.get("LINE", "line1")
PERIOD_S = float(os.environ.get("PERIOD_S", "1.0"))
# Seconds of flat-healthy before the ramp begins, then climb past 0.60.
FLAT_S = float(os.environ.get("FLAT_S", "20"))
RAMP_S = float(os.environ.get("RAMP_S", "25"))
MODEL_VERSION = os.environ.get("MODEL_VERSION", "pdm-anomaly@2026.06.15-a3f1")

# Simulated on-device hop durations (seconds). On a real board these are measured;
# here they are synthetic like the scores — the pipeline carrying them is real.
SENSOR_S = 0.002
INFERENCE_S = 0.008

# Representative payload sizes for the device's data classes. raw (a sensor window)
# and features dwarf the inference result — that contrast is the minimization story.
RAW_BYTES = 4096
FEATURE_BYTES = 512
INFERENCE_BYTES = 50

tracer = trace.get_tracer("fakegen")

# Demo control: "auto" follows the flat-then-rising curve; "perturb" forces cnc-7
# anomalous on cue; "heal" returns it to healthy. Driven via control.<line>.
STATE = {"mode": "auto"}


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


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def score_at(elapsed: float) -> float:
    """Flat ~0.2 healthy, then a smooth ramp through 0.60 up toward ~0.9."""
    if elapsed < FLAT_S:
        return 0.20 + 0.03 * math.sin(elapsed)
    ramp = min((elapsed - FLAT_S) / RAMP_S, 1.0)
    return 0.20 + 0.72 * ramp


def cnc_score(elapsed: float) -> float:
    """cnc-7's score, honoring the demo control mode."""
    mode = STATE["mode"]
    if mode == "perturb":
        return 0.85 + 0.03 * math.sin(elapsed)   # firmly over threshold, on cue
    if mode == "heal":
        return 0.20 + 0.02 * math.sin(elapsed)   # calm, on cue
    return score_at(elapsed)                      # auto: flat-then-rising


async def publish_edge(nc, container: str, classification: str, body: dict) -> None:
    """Publish one classified message to the device data plane the gateway sees."""
    body["data_classification"] = classification
    await nc.publish(f"edge.{LINE}.{container}", json.dumps(body).encode())


async def publish_one(nc, container: str, base: float, elapsed: float) -> None:
    # raw + features are the bulk of the device's data — they must stay on-device.
    await publish_edge(nc, container, "raw", {"ts": now_iso(), "container_id": container, "bytes": RAW_BYTES})
    await publish_edge(nc, container, "features", {"ts": now_iso(), "container_id": container, "bytes": FEATURE_BYTES})

    # inference is the only class allowed to egress (Contract B). It carries the
    # trace context in the BODY (not NATS headers) so it survives the Vector hop.
    with tracer.start_as_current_span("edge.pipeline") as root:
        root.set_attribute("container_id", container)
        with tracer.start_as_current_span("sensor"):
            await asyncio.sleep(SENSOR_S)
        with tracer.start_as_current_span("inference"):
            await asyncio.sleep(INFERENCE_S)
        # CLIENT span: pairs with the bridge's SERVER span to form the service-graph edge.
        with tracer.start_as_current_span("fabric.publish", kind=SpanKind.CLIENT) as pub:
            pub.set_attribute("data.classification", "inference")
            carrier: dict[str, str] = {}
            propagate.inject(carrier)
            await publish_edge(nc, container, "inference", {
                "ts": now_iso(),
                "container_id": container,
                "model_version": MODEL_VERSION,
                "anomaly_score": round(base + 0.02 * math.sin(elapsed * 1.7), 4),
                "fault_class": None,
                "location": "spindle" if container == "cnc-7" else "ram",
                "bytes": INFERENCE_BYTES,
                "traceparent": carrier.get("traceparent"),
                "tracestate": carrier.get("tracestate"),
            })


# Conveyor track segments (match label-ui/track.html topology). The video localizer
# would stamp these as `location`; here fakegen plays both device and model so the
# track view's heatmap and model fault annotations are live in the demo.
TRACK_SEGMENTS = [f"seg-{i}" for i in range(1, 18)]
HOT_SEGMENT = "seg-4"          # one segment the "model" flags as a bearing fault


def track_score(seg: str, elapsed: float) -> float:
    """Healthy baseline across the loop, with one segment riding over threshold."""
    if seg == HOT_SEGMENT:
        return round(0.78 + 0.03 * math.sin(elapsed), 4)
    idx = TRACK_SEGMENTS.index(seg)
    return round(0.18 + 0.12 * abs(math.sin(elapsed / 7.0 + idx)), 4)


async def publish_track(nc, elapsed: float) -> None:
    """Emit one Contract B inference per track segment (container_id == location)."""
    for seg in TRACK_SEGMENTS:
        score = track_score(seg, elapsed)
        await publish_edge(nc, seg, "inference", {
            "ts": now_iso(),
            "container_id": seg,
            "model_version": MODEL_VERSION,
            "anomaly_score": score,
            "fault_class": "bearing wear" if seg == HOT_SEGMENT else None,
            "location": seg,
            "bytes": INFERENCE_BYTES,
        })


async def on_control(msg) -> None:
    try:
        cmd = json.loads(msg.data).get("cmd", "auto")
    except Exception:  # noqa: BLE001
        cmd = "auto"
    if cmd in ("auto", "perturb", "heal"):
        STATE["mode"] = cmd
        print(f"[control] mode -> {cmd}", flush=True)


async def main() -> None:
    setup_telemetry("fakegen")
    auth = {}
    if NATS_NKEY_SEED:
        auth["nkeys_seed"] = NATS_NKEY_SEED
    if NATS_CA_FILE:
        auth["tls"] = ssl.create_default_context(cafile=NATS_CA_FILE)
        auth["tls_hostname"] = NATS_TLS_HOSTNAME
    nc = await nats.connect(NATS_URL, reconnect_time_wait=2, max_reconnect_attempts=-1, **auth)
    await nc.subscribe(f"control.{LINE}", cb=on_control)
    print(f"[fakegen] publishing edge.{LINE}.* (raw|features|inference) every {PERIOD_S}s", flush=True)

    elapsed = 0.0
    while True:
        # cnc-7 follows the demo control mode; press-3 is a steady healthy control.
        await publish_one(nc, "cnc-7", cnc_score(elapsed), elapsed)
        await publish_one(nc, "press-3", 0.20, elapsed)
        await publish_track(nc, elapsed)
        elapsed += PERIOD_S
        await asyncio.sleep(PERIOD_S)


if __name__ == "__main__":
    asyncio.run(main())
