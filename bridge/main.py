# ABOUTME: Bridge service — subscribes NATS (JetStream), persists Contract B/D to Timescale.
# ABOUTME: Also serves POST /label (label-ui -> NATS) and a /metrics endpoint for Prometheus.

import asyncio
import base64
import json
import os
import signal
import ssl
from datetime import datetime, timezone

import asyncpg
import nats
from aiohttp import web
from opentelemetry import propagate, trace

tracer = trace.get_tracer("bridge")

# --- config ---------------------------------------------------------------

THRESHOLD = float(os.environ.get("THRESHOLD", "0.60"))
NATS_URL = os.environ.get("NATS_URL", "nats://nats:4222")
# Per-identity nkey seed (CRA unique-credential auth). Unset on the open demo fabric.
NATS_NKEY_SEED = os.environ.get("NATS_NKEY_SEED")
# CA that signed the NATS server cert (CRA confidentiality in transit). Unset -> plaintext demo.
NATS_CA_FILE = os.environ.get("NATS_CA_FILE")
NATS_TLS_HOSTNAME = os.environ.get("NATS_TLS_HOSTNAME", "nats")
PG_DSN = os.environ.get("PG_DSN", "postgresql://postgres:postgres@timescale:5432/postgres")
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8000"))
STREAM_NAME = os.environ.get("STREAM_NAME", "EDGE")
# Queue a retrain once this many actionable (non-dismiss) labels accumulate.
RETRAIN_AFTER_LABELS = int(os.environ.get("RETRAIN_AFTER_LABELS", "1"))
# The device that traverses the track and runs capture; capture commands target its subject.
CAPTURE_DEVICE = os.environ.get("CAPTURE_DEVICE", "cnc-7")
# Per-frame camera topic (Contract: edge.camera.<line>.<container>, base64 JPEG per msg).
CAMERA_SUBJECT = os.environ.get("CAMERA_SUBJECT", "edge.camera.*.*")

VALID_LABELS = {"bearing wear", "imbalance", "dismiss"}

# Track annotation fault classes (operator marks an error on a track segment).
# Mirrors the features in idea.md: turn table, bumps, rubber-band slippage, bearings, servos.
TRACK_FAULTS = {
    "bearing wear", "imbalance", "turn table", "track bump",
    "rubber band slippage", "servo motor", "dismiss",
}


# --- pure logic (unit-tested in test_bridge.py) ---------------------------

def parse_ts(value) -> datetime:
    """Coerce an ISO-8601 string (or datetime) into an aware datetime for asyncpg."""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_inference(data: bytes) -> dict:
    """Validate a Contract B telemetry payload; raise ValueError if malformed."""
    msg = json.loads(data)
    if "ts" not in msg or "anomaly_score" not in msg:
        raise ValueError("inference payload missing ts/anomaly_score")
    msg["ts"] = parse_ts(msg["ts"])
    msg["anomaly_score"] = float(msg["anomaly_score"])
    return msg


def parse_label(data: bytes) -> dict:
    """Validate a Contract D label payload; raise ValueError if malformed."""
    msg = json.loads(data)
    for field in ("ts", "container_id", "label"):
        if field not in msg:
            raise ValueError(f"label payload missing {field}")
    msg["ts"] = parse_ts(msg["ts"])
    return msg


def should_open_alert(score: float, threshold: float, has_open_alert: bool) -> bool:
    """Open an alert only when over threshold and one isn't already open."""
    return score >= threshold and not has_open_alert


def label_subject(line: str, container: str) -> str:
    """Contract D subject for a label event."""
    return f"labels.{line}.{container}"


def deploy_subject(line: str) -> str:
    """Contract C subject for a model deploy event."""
    return f"models.{line}.deploy"


def capture_cmd_subject(line: str, container: str) -> str:
    """Contract E subject the device subscribes to for a directed-gather command."""
    return f"capture.{line}.{container}.cmd"


def build_capture_add(segment: str, label: str, request_id) -> dict:
    """Assemble a Contract E add command: watch `segment`, record while the device is on it."""
    return {"request_id": request_id, "label": label, "segment": segment}


def build_capture_stop() -> dict:
    """Assemble a Contract E stop command: the device clears its watch-set (stops listening)."""
    return {"stop": True}


def parse_capture(data: bytes) -> dict:
    """Validate a captured-window payload from the sink; raise ValueError if malformed."""
    msg = json.loads(data)
    if "features_b64" not in msg:
        raise ValueError("capture payload missing features_b64")
    return msg


def parse_deploy(data: bytes) -> dict:
    """Validate a Contract C deploy payload; raise ValueError if malformed."""
    msg = json.loads(data)
    if "model_version" not in msg:
        raise ValueError("deploy payload missing model_version")
    if "ts" in msg:
        msg["ts"] = parse_ts(msg["ts"])
    return msg


def should_queue_retrain(new_labels: int, threshold: int) -> bool:
    """Queue a retrain once enough actionable labels have accumulated."""
    return new_labels >= threshold


def next_model_version(deployed_count: int) -> str:
    """Auto-name the next retrained model when a deploy doesn't specify one."""
    return f"pdm-anomaly@retrained-{deployed_count + 1}"


def valid_track_fault(label) -> bool:
    """A track annotation's fault class must be one the model can retrain on."""
    return label in TRACK_FAULTS


# --- persistence ----------------------------------------------------------

async def handle_inference(pool: asyncpg.Pool, msg: dict) -> None:
    container = msg.get("container_id")
    async with pool.acquire() as con:
        await con.execute(
            "INSERT INTO scores (ts, container_id, model_version, anomaly_score, fault_class, location)"
            " VALUES ($1::timestamptz, $2, $3, $4, $5, $6)",
            msg["ts"], container, msg.get("model_version"),
            msg["anomaly_score"], msg.get("fault_class"), msg.get("location"),
        )
        has_open = await con.fetchval(
            "SELECT EXISTS (SELECT 1 FROM alerts WHERE container_id=$1 AND state='unlabeled')",
            container,
        )
        if should_open_alert(msg["anomaly_score"], THRESHOLD, has_open):
            await con.execute(
                "INSERT INTO alerts (ts, container_id, anomaly_score) VALUES ($1::timestamptz, $2, $3)",
                msg["ts"], container, msg["anomaly_score"],
            )
            print(f"[alert] opened for {container} @ {msg['anomaly_score']:.2f}", flush=True)


async def handle_label(pool: asyncpg.Pool, msg: dict) -> None:
    container = msg["container_id"]
    label = msg["label"]
    async with pool.acquire() as con:
        await con.execute(
            "INSERT INTO labels (ts, container_id, feature_window_ref, label)"
            " VALUES ($1::timestamptz, $2, $3, $4)",
            msg["ts"], container, msg.get("feature_window_ref"), label,
        )
        # Flip the open alert: a 'dismiss' label dismisses it, anything else labels it.
        new_state = "dismissed" if label == "dismiss" else "labeled"
        await con.execute(
            "UPDATE alerts SET state=$1, label=$2, labeled_at=now()"
            " WHERE id = (SELECT id FROM alerts WHERE container_id=$3 AND state='unlabeled'"
            "             ORDER BY ts DESC LIMIT 1)",
            new_state, label, container,
        )
        print(f"[label] {container} -> {label} ({new_state})", flush=True)

        # Close the loop: once enough actionable labels accumulate since the last
        # retrain was queued, queue another — the retrain ML would consume.
        if label != "dismiss":
            last_queued = await con.fetchval(
                "SELECT max(ts) FROM model_events WHERE event_type='retrain_queued'"
            )
            new_labels = await con.fetchval(
                "SELECT count(*) FROM labels WHERE label <> 'dismiss'"
                " AND ts > COALESCE($1, '-infinity'::timestamptz)",
                last_queued,
            )
            if should_queue_retrain(new_labels, RETRAIN_AFTER_LABELS):
                await con.execute(
                    "INSERT INTO model_events (line, event_type, detail)"
                    " VALUES ($1, 'retrain_queued', $2)",
                    msg.get("line", "line1"),
                    f"queued from {new_labels} label(s)",
                )
                print(f"[retrain] queued from {new_labels} label(s)", flush=True)


async def handle_deploy(pool: asyncpg.Pool, msg: dict) -> None:
    """Record a Contract C deploy event (models.<line>.deploy) for the dashboard."""
    async with pool.acquire() as con:
        await con.execute(
            "INSERT INTO model_events (ts, line, event_type, model_version, detail)"
            " VALUES (COALESCE($1, now()), $2, 'deployed', $3, $4)",
            msg.get("ts"), msg.get("line", "line1"),
            msg["model_version"], msg.get("detail"),
        )
        print(f"[deploy] {msg['model_version']} deployed", flush=True)


async def handle_capture(pool: asyncpg.Pool, msg: dict) -> None:
    """Persist one gathered window from the capture sink into the training-data store."""
    async with pool.acquire() as con:
        await con.execute(
            "INSERT INTO captures (request_id, container_id, label, segment, seq, features_b64)"
            " VALUES ($1, $2, $3, $4, $5, $6)",
            msg.get("request_id"), msg.get("container_id"), msg.get("label"),
            msg.get("segment"), msg.get("seq"), msg["features_b64"],
        )


# --- camera frames (edge.camera.<line>.<container> -> live MJPEG) ----------

class FrameStore:
    """Holds the most recent JPEG frame per line and wakes streamers on each new one."""

    def __init__(self):
        self._frames: dict[str, dict] = {}   # line -> {"jpeg": bytes, "meta": dict}
        self._event = asyncio.Event()

    def put(self, line: str, jpeg: bytes, meta: dict) -> None:
        self._frames[line] = {"jpeg": jpeg, "meta": meta}
        # Wake every waiter, then reset so the next frame can signal again.
        self._event.set()
        self._event.clear()

    def latest(self, line: str):
        return self._frames.get(line)

    def lines(self):
        return sorted(self._frames.keys())

    async def wait(self) -> None:
        await self._event.wait()


def camera_line(subject: str) -> str:
    """edge.camera.<line>.<container> -> <line>."""
    parts = subject.split(".")
    return parts[2] if len(parts) >= 4 else "line1"


def decode_frame(data: bytes) -> tuple[bytes, dict]:
    """Parse a camera message, return (raw jpeg bytes, lightweight meta). Raises on bad input."""
    msg = json.loads(data)
    jpeg = base64.b64decode(msg["data"])
    meta = {k: msg.get(k) for k in ("frame_id", "width", "height", "t_us", "t_host_us")}
    return jpeg, meta


# --- HTTP (label-ui -> NATS, and Prometheus /metrics) ---------------------

async def post_label(request: web.Request) -> web.Response:
    """Accept a label from label-ui and publish Contract D to NATS."""
    body = await request.json()
    line = body.get("line", "line1")
    container = body.get("container_id")
    label = body.get("label")
    if container is None or label not in VALID_LABELS:
        return web.json_response({"error": "container_id and a valid label required"}, status=400)

    payload = {
        "ts": body.get("ts") or "now()",
        "container_id": container,
        "feature_window_ref": body.get("feature_window_ref"),
        "label": label,
    }
    # The bridge fills ts server-side if the page didn't; keep it ISO for the DB.
    if payload["ts"] == "now()":
        payload["ts"] = (await _now_iso(request.app["pool"]))
    nc = request.app["nc"]
    await nc.publish(label_subject(line, container), json.dumps(payload).encode())
    return web.json_response({"ok": True, "subject": label_subject(line, container)})


async def _now_iso(pool: asyncpg.Pool) -> str:
    async with pool.acquire() as con:
        return (await con.fetchval("SELECT now()")).isoformat()


async def post_deploy(request: web.Request) -> web.Response:
    """Publish a Contract C deploy event (the platform 'rolls out' a retrained model)."""
    body = await request.json() if request.can_read_body else {}
    line = body.get("line", "line1")
    pool = request.app["pool"]
    version = body.get("model_version")
    if not version:
        async with pool.acquire() as con:
            deployed = await con.fetchval(
                "SELECT count(*) FROM model_events WHERE event_type='deployed'"
            )
        version = next_model_version(deployed)
    payload = {
        "ts": await _now_iso(pool),
        "line": line,
        "model_version": version,
        "detail": body.get("detail", "rolled out via GitOps"),
    }
    await request.app["nc"].publish(deploy_subject(line), json.dumps(payload).encode())
    return web.json_response({"ok": True, "model_version": version, "subject": deploy_subject(line)})


async def post_control(request: web.Request) -> web.Response:
    """Demo control: publish a perturb/heal/auto command to the device (control.<line>)."""
    body = await request.json() if request.can_read_body else {}
    cmd = body.get("cmd", "auto")
    line = body.get("line", "line1")
    if cmd not in ("auto", "perturb", "heal"):
        return web.json_response({"error": "cmd must be auto|perturb|heal"}, status=400)
    await request.app["nc"].publish(f"control.{line}", json.dumps({"cmd": cmd}).encode())
    return web.json_response({"ok": True, "cmd": cmd})


async def post_capture(request: web.Request) -> web.Response:
    """Tell the device which track segments to record (Contract E).

    Segments are sent one at a time and accumulate into the device's watch-set; it records every
    window while driving on any watched segment. Use `label: "healthy"` to gather clean baseline
    data (so the training set isn't skewed toward failure data) or a fault class for a bad part.
    Send `{"stop": true}` to clear the watch-set and stop listening. Published on
    capture.<line>.<container>.cmd (container defaults to the track device).
    """
    body = await request.json() if request.can_read_body else {}
    line = body.get("line", "line1")
    container = body.get("container_id", CAPTURE_DEVICE)
    subject = capture_cmd_subject(line, container)
    nc = request.app["nc"]

    if body.get("stop"):
        await nc.publish(subject, json.dumps(build_capture_stop()).encode())
        return web.json_response({"ok": True, "subject": subject, "stop": True})

    segment = body.get("segment")
    if not segment:
        return web.json_response({"error": "segment required (or send stop:true)"}, status=400)
    label = body.get("label", "healthy")
    request_id = body.get("request_id") or f"cap-{segment}"
    await nc.publish(subject, json.dumps(build_capture_add(segment, label, request_id)).encode())
    return web.json_response({"ok": True, "subject": subject, "segment": segment})


async def get_model_events(request: web.Request) -> web.Response:
    """Recent model/control events for the label-ui and secondary dashboard."""
    pool = request.app["pool"]
    async with pool.acquire() as con:
        rows = await con.fetch(
            "SELECT ts, event_type, model_version, detail FROM model_events"
            " ORDER BY ts DESC LIMIT 20"
        )
    return web.json_response(
        [
            {
                "ts": r["ts"].isoformat() if r["ts"] else None,
                "event_type": r["event_type"],
                "model_version": r["model_version"],
                "detail": r["detail"],
            }
            for r in rows
        ]
    )


async def get_alerts(request: web.Request) -> web.Response:
    """Open alerts for the label-ui to render (the operator surface)."""
    pool = request.app["pool"]
    async with pool.acquire() as con:
        rows = await con.fetch(
            "SELECT id, ts, container_id, anomaly_score FROM alerts"
            " WHERE state='unlabeled' ORDER BY ts DESC"
        )
    return web.json_response(
        [
            {
                "id": r["id"],
                "ts": r["ts"].isoformat() if r["ts"] else None,
                "container_id": r["container_id"],
                "anomaly_score": r["anomaly_score"],
            }
            for r in rows
        ]
    )


async def get_track(request: web.Request) -> web.Response:
    """Model's view of the track: latest score + fault_class per segment (location).

    The video localizer stamps each inference with a `location` (segment id); this
    rolls those up to one row per segment so the track view can paint a heatmap and
    show which segments the model has flagged.
    """
    pool = request.app["pool"]
    async with pool.acquire() as con:
        rows = await con.fetch(
            "SELECT DISTINCT ON (location) location, container_id, anomaly_score,"
            " fault_class, ts FROM scores WHERE location IS NOT NULL"
            " ORDER BY location, ts DESC"
        )
        open_alerts = await con.fetch(
            "SELECT container_id FROM alerts WHERE state='unlabeled'"
        )
    alerting = {r["container_id"] for r in open_alerts}
    return web.json_response({
        "threshold": THRESHOLD,
        "segments": [
            {
                "location": r["location"],
                "container_id": r["container_id"],
                "anomaly_score": r["anomaly_score"],
                "fault_class": r["fault_class"],
                "ts": r["ts"].isoformat() if r["ts"] else None,
                "alerting": r["container_id"] in alerting,
            }
            for r in rows
        ],
    })


async def get_annotations(request: web.Request) -> web.Response:
    """Operator annotations: most-recent label per segment (location = container_id)."""
    pool = request.app["pool"]
    async with pool.acquire() as con:
        rows = await con.fetch(
            "SELECT DISTINCT ON (container_id) container_id, label, feature_window_ref, ts"
            " FROM labels ORDER BY container_id, ts DESC"
        )
    return web.json_response([
        {
            "location": r["container_id"],
            "label": r["label"],
            "note": r["feature_window_ref"],
            "ts": r["ts"].isoformat() if r["ts"] else None,
        }
        for r in rows
    ])


async def post_annotate(request: web.Request) -> web.Response:
    """Operator marks an error on a track segment -> publish Contract D keyed by location."""
    body = await request.json()
    line = body.get("line", "line1")
    location = body.get("location")
    fault = body.get("fault")
    if not location or not valid_track_fault(fault):
        return web.json_response(
            {"error": "location and a valid fault required", "valid": sorted(TRACK_FAULTS)},
            status=400,
        )
    payload = {
        "ts": await _now_iso(request.app["pool"]),
        "container_id": location,           # the segment is the labeled asset
        "feature_window_ref": body.get("note") or f"track:{location}",
        "label": fault,
    }
    subject = label_subject(line, location)
    nc = request.app["nc"]
    await nc.publish(subject, json.dumps(payload).encode())

    # Close the data loop: marking a segment faulty also adds it to the track device's capture
    # watch-set (Contract E), so retraining gets the windows from that segment, not just the
    # label. The device records whenever it drives on it, until a stop command. 'dismiss' is no fault.
    capture_subject = None
    if fault != "dismiss":
        container = body.get("container_id", CAPTURE_DEVICE)
        capture_subject = capture_cmd_subject(line, container)
        cmd = build_capture_add(location, fault, f"cap-annotate-{location}")
        await nc.publish(capture_subject, json.dumps(cmd).encode())
    return web.json_response({"ok": True, "subject": subject, "capture_subject": capture_subject})


async def metrics(request: web.Request) -> web.Response:
    """Stretch: a minimal Prometheus exposition derived from recent rows."""
    pool = request.app["pool"]
    async with pool.acquire() as con:
        rows = await con.fetch(
            "SELECT DISTINCT ON (container_id) container_id, anomaly_score"
            " FROM scores ORDER BY container_id, ts DESC"
        )
        open_alerts = await con.fetchval("SELECT count(*) FROM alerts WHERE state='unlabeled'")
    lines = [
        "# HELP anomaly_score Latest anomaly score per container.",
        "# TYPE anomaly_score gauge",
    ]
    for r in rows:
        lines.append(f'anomaly_score{{container_id="{r["container_id"]}"}} {r["anomaly_score"]}')
    lines += [
        "# HELP active_alerts Currently open (unlabeled) alerts.",
        "# TYPE active_alerts gauge",
        f"active_alerts {open_alerts}",
    ]
    return web.Response(text="\n".join(lines) + "\n", content_type="text/plain")


async def get_camera_snapshot(request: web.Request) -> web.Response:
    """Latest single JPEG frame for a line (cheap poll / fallback for the <img> stream)."""
    line = request.query.get("line", "line1")
    frame = request.app["frames"].latest(line)
    if not frame:
        return web.json_response({"error": f"no frames yet for {line}"}, status=404)
    return web.Response(
        body=frame["jpeg"],
        content_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


async def get_camera_stream(request: web.Request) -> web.StreamResponse:
    """Live MJPEG (multipart/x-mixed-replace) — point an <img src> straight at this."""
    line = request.query.get("line", "line1")
    store = request.app["frames"]
    boundary = "frame"
    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": f"multipart/x-mixed-replace; boundary={boundary}",
            "Cache-Control": "no-store",
        },
    )
    await resp.prepare(request)
    last_id = None
    try:
        while True:
            frame = store.latest(line)
            if frame and frame["meta"].get("frame_id") != last_id:
                last_id = frame["meta"].get("frame_id")
                jpeg = frame["jpeg"]
                await resp.write(
                    f"--{boundary}\r\nContent-Type: image/jpeg\r\n"
                    f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                    + jpeg
                    + b"\r\n"
                )
            await store.wait()
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    return resp


async def get_camera_lines(request: web.Request) -> web.Response:
    """Lines that currently have a live frame (so the UI can pick one)."""
    return web.json_response({"lines": request.app["frames"].lines()})


# --- wiring ---------------------------------------------------------------

@web.middleware
async def cors_middleware(request: web.Request, handler):
    """Allow the static label-ui (port 8080) to call the bridge (port 8000)."""
    if request.method == "OPTIONS":
        resp = web.Response()
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp



def setup_telemetry(service_name: str) -> None:
    """Wire OTLP trace export to the collector if an endpoint is configured."""
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        print("[otel] no endpoint set; tracing disabled", flush=True)
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    print(f"[otel] tracing -> {os.environ['OTEL_EXPORTER_OTLP_ENDPOINT']}", flush=True)


async def connect_pg() -> asyncpg.Pool:
    last = None
    for _ in range(30):
        try:
            return await asyncpg.create_pool(PG_DSN, min_size=1, max_size=5)
        except Exception as exc:  # noqa: BLE001 — retry until Timescale is up
            last = exc
            print(f"[pg] waiting: {exc}", flush=True)
            await asyncio.sleep(2)
    raise RuntimeError(f"could not connect to Postgres: {last}")


async def ensure_stream(js) -> None:
    """JetStream so inference buffers and replays if the bridge restarts mid-demo."""
    try:
        await js.add_stream(name=STREAM_NAME, subjects=["inference.>", "labels.>", "models.>"])
    except Exception as exc:  # noqa: BLE001 — stream may already exist
        print(f"[js] stream: {exc}", flush=True)


async def main() -> None:
    setup_telemetry("bridge")
    pool = await connect_pg()
    auth = {}
    if NATS_NKEY_SEED:
        auth["nkeys_seed"] = NATS_NKEY_SEED
    if NATS_CA_FILE:
        auth["tls"] = ssl.create_default_context(cafile=NATS_CA_FILE)
        auth["tls_hostname"] = NATS_TLS_HOSTNAME
    nc = await nats.connect(NATS_URL, reconnect_time_wait=2, max_reconnect_attempts=-1, **auth)
    js = nc.jetstream()
    await ensure_stream(js)
    frames = FrameStore()

    async def on_inference(m):
        try:
            msg = parse_inference(m.data)
        except Exception as exc:  # noqa: BLE001 — never kill the subscription on one bad msg
            print(f"[inference] dropped: {exc}", flush=True)
            await m.ack()
            return
        # Trace context rides in the BODY (Vector can't forward NATS headers), so
        # extract it from the payload to continue the edge trace at cloud.ingest.
        carrier = {k: msg[k] for k in ("traceparent", "tracestate") if msg.get(k)}
        ctx = propagate.extract(carrier)
        # SERVER span: closes the service-graph edge from the device's fabric.publish.
        with tracer.start_as_current_span(
            "cloud.ingest", context=ctx, kind=trace.SpanKind.SERVER
        ):
            try:
                await handle_inference(pool, msg)
            except Exception as exc:  # noqa: BLE001
                print(f"[inference] persist failed: {exc}", flush=True)
        await m.ack()

    async def on_label(m):
        try:
            await handle_label(pool, parse_label(m.data))
        except Exception as exc:  # noqa: BLE001
            print(f"[label] dropped: {exc}", flush=True)
        await m.ack()

    async def on_deploy(m):
        try:
            await handle_deploy(pool, parse_deploy(m.data))
        except Exception as exc:  # noqa: BLE001
            print(f"[deploy] dropped: {exc}", flush=True)
        await m.ack()

    async def on_capture(m):
        try:
            await handle_capture(pool, parse_capture(m.data))
        except Exception as exc:  # noqa: BLE001
            print(f"[capture] dropped: {exc}", flush=True)

    async def on_camera(m):
        try:
            jpeg, meta = decode_frame(m.data)
        except Exception as exc:  # noqa: BLE001 — never kill the sub on one bad frame
            print(f"[camera] dropped: {exc}", flush=True)
            return
        frames.put(camera_line(m.subject), jpeg, meta)

    await js.subscribe("inference.>", durable="bridge-inference", cb=on_inference)
    await js.subscribe("labels.>", durable="bridge-labels", cb=on_label)
    await js.subscribe("models.>", durable="bridge-models", cb=on_deploy)
    # Capture sink: core NATS (not JetStream) — high-volume training windows outside the EDGE stream.
    await nc.subscribe("capture.*.*.data", cb=on_capture)
    # Camera frames: core NATS, high-volume JPEG stream — kept in memory, not persisted.
    await nc.subscribe(CAMERA_SUBJECT, cb=on_camera)
    print(f"[bridge] subscribed; THRESHOLD={THRESHOLD}; camera={CAMERA_SUBJECT}", flush=True)

    app = web.Application(middlewares=[cors_middleware])
    app["pool"] = pool
    app["nc"] = nc
    app["frames"] = frames
    app.router.add_post("/label", post_label)
    app.router.add_post("/deploy", post_deploy)
    app.router.add_post("/control", post_control)
    app.router.add_post("/capture", post_capture)
    app.router.add_post("/annotate", post_annotate)
    app.router.add_get("/alerts", get_alerts)
    app.router.add_get("/track", get_track)
    app.router.add_get("/annotations", get_annotations)
    app.router.add_get("/model_events", get_model_events)
    app.router.add_get("/camera/stream", get_camera_stream)
    app.router.add_get("/camera/snapshot", get_camera_snapshot)
    app.router.add_get("/camera/lines", get_camera_lines)
    app.router.add_get("/metrics", metrics)
    app.router.add_get("/healthz", lambda r: web.json_response({"ok": True}))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", HTTP_PORT).start()
    print(f"[bridge] http on :{HTTP_PORT}", flush=True)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    await runner.cleanup()
    await nc.drain()
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
