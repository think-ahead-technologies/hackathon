"""Host bridge server: PSOC Edge sensor stream -> binary log + browser WebSocket.

Sits between the device and bmi270_web_streaming.html. Two device links:

  Serial (USB):   PSOC Edge ──UART/KitProg3──> imu_server.py
  TCP (Wi-Fi):    PSOC Edge SoftAP ──TCP:5000──> imu_server.py --tcp 192.168.10.1

Either way:        imu_server.py ──WebSocket──> browser frontend
                        │
                        └──> logs/imu_YYYYmmdd_HHMMSS_mmm.bin  (IMULOG01)

The server is a transparent byte pipe — device bytes are broadcast unmodified
to every connected WebSocket client (binary messages), and client text
messages ("S\\n", "Q\\n", "CFG,...\\n") are forwarded unmodified to the device —
so the frontend's existing frame parser works unchanged. In parallel the
server runs its own frame parser to CRC-validate frames and write them,
host-timestamped, to an IMULOG01 file (see imu_log.py).

Log lifecycle: a new file is opened when an "S" (start) command passes
through, and closed on "Q" (stop), when the last browser tab disconnects
(the server then also sends "Q" to the device so it doesn't stream into the
void), or when the device link drops. If frames arrive while no log is open —
e.g. the device was already streaming when the server started — a file is
opened automatically. The TCP link auto-reconnects with backoff.

Usage:
    pip install -r requirements.txt
    python imu_server.py                          # auto-detect KitProg3 port
    python imu_server.py --serial-port COM5 --baud 115200
    python imu_server.py --tcp 192.168.10.1       # Wi-Fi SoftAP (port 5000)
    python imu_server.py --tcp 192.168.10.1:5000 --http-port 8765
Then open http://localhost:8765 in Chrome/Edge.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Set

import serial
import serial.tools.list_ports
from aiohttp import WSMsgType, web

import imu_log

LOGGER = logging.getLogger("imu_server")

HERE = Path(__file__).resolve().parent
FRONTEND_HTML = HERE.parent / "proj_cm55" / "web_streaming" / "bmi270_web_streaming.html"

MAGIC0, MAGIC1 = 0xAB, 0xCD
FRAME_TYPE_IMU = 0x10
FRAME_TYPE_STATUS = 0x20
FRAME_TYPE_CAMERA = 0x30  # u32 frame_id, u16 w, u16 h, JPEG bitstream
FRAME_TYPE_AUDIO = 0x40   # u32 seq, u16 sample_rate, u8 channels, u8 bits, PCM
MAX_PAYLOAD = 65535  # camera frames carry up to ~64K of JPEG

WS_QUEUE_MAX = 256          # per-client chunk queue; slow clients drop oldest data
TCP_DEFAULT_PORT = 5000     # firmware tcp_stream.c / wifi_config.h
TCP_RECONNECT_MAX_S = 10.0


def _make_crc16_ibm_table() -> list:
    table = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
        table.append(crc)
    return table


_CRC16_IBM_TABLE = _make_crc16_ibm_table()


def crc16_ibm(data: bytes) -> int:
    """CRC-16/IBM, poly 0xA001, init 0xFFFF — matches stream_proto.c.

    Table-driven (one lookup per byte instead of eight shifts). This is the hot
    path: it runs on every frame, including the 10-30 KB camera JPEGs and the
    audio chunks, on the single asyncio thread that must also drain the socket.
    The bit-by-bit version made the host a marginally-slow consumer, so the OS
    receive buffer slowly filled until the board's TCP window closed and the
    firmware dropped the link (the ~90 s recording split). ~8x faster here gives
    the host comfortable headroom over the board's output rate.
    """
    crc = 0xFFFF
    table = _CRC16_IBM_TABLE
    for byte in data:
        crc = (crc >> 8) ^ table[(crc ^ byte) & 0xFF]
    return crc


class FrameParser:
    """Incremental parser for 0xAB 0xCD | type | len(LE16) | payload | crc(LE16).

    Tolerates interleaved ASCII status lines (all bytes < 0x80, so they can
    never contain the magic). feed() returns a list of (type, payload) tuples
    for every CRC-valid frame found.
    """

    def __init__(self):
        self._buf = bytearray()
        self.crc_errors = 0

    def feed(self, data: bytes):
        self._buf.extend(data)
        frames = []
        buf = self._buf
        i = 0
        while True:
            while i < len(buf) - 1 and not (buf[i] == MAGIC0 and buf[i + 1] == MAGIC1):
                i += 1
            if i >= len(buf) - 1:
                break
            if len(buf) - i < 5:
                break  # header incomplete
            ftype = buf[i + 2]
            length = buf[i + 3] | (buf[i + 4] << 8)
            if length > MAX_PAYLOAD:
                i += 2  # false magic inside data; resync
                continue
            frame_len = 5 + length + 2
            if len(buf) - i < frame_len:
                break  # frame incomplete
            payload = bytes(buf[i + 5 : i + 5 + length])
            crc_got = buf[i + 5 + length] | (buf[i + 6 + length] << 8)
            if crc16_ibm(payload) != crc_got:
                self.crc_errors += 1
                i += 2
                continue
            frames.append((ftype, payload))
            i += frame_len
        del buf[: max(i, 0)]
        if len(buf) > 65536:  # garbage guard
            del buf[:-4096]
        return frames


# ---- device links ---------------------------------------------------------------


class SerialLink:
    """USB serial device link (KitProg3 UART). Dies permanently on port loss."""

    def __init__(self, port_name: str, baud: int):
        self.port_name = port_name
        self.baud = baud
        self.name = f"{port_name} @ {baud} baud"
        self.alive = True
        self._lock = threading.Lock()
        try:
            # serial_for_url handles plain COM ports and test URLs (e.g. loop://)
            self.ser = serial.serial_for_url(port_name, baudrate=baud, timeout=0.05)
        except serial.SerialException as exc:
            raise SystemExit(
                f"Cannot open {port_name}: {exc}\n"
                "Close other programs using the port (terminal, another server "
                "instance, or a browser tab connected via WebSerial).")

    def start(self, loop: asyncio.AbstractEventLoop, on_rx, on_up):
        on_up()
        self._thread = threading.Thread(
            target=self._read_loop, args=(loop, on_rx), daemon=True, name="serial-rx")
        self._stop = threading.Event()
        self._thread.start()

    def _read_loop(self, loop, on_rx):
        while not self._stop.is_set():
            try:
                data = self.ser.read(self.ser.in_waiting or 1)
            except serial.SerialException as exc:
                LOGGER.error("Serial read failed (device unplugged?): %s", exc)
                self.alive = False
                loop.call_soon_threadsafe(on_rx, None)
                return
            if data:
                loop.call_soon_threadsafe(on_rx, bytes(data))

    def write(self, data: bytes):
        with self._lock:
            self.ser.write(data)

    async def stop(self):
        self._stop.set()
        try:
            self.ser.close()
        except Exception:
            pass


class TcpLink:
    """Wi-Fi TCP device link (firmware SoftAP). Reconnects with backoff."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.baud = 0  # not meaningful for TCP; recorded as 0 in log headers
        self.name = f"tcp://{host}:{port}"
        self.alive = False
        self._writer: Optional[asyncio.StreamWriter] = None
        self._task: Optional[asyncio.Task] = None

    def start(self, loop: asyncio.AbstractEventLoop, on_rx, on_up):
        self._task = loop.create_task(self._run(on_rx, on_up))

    async def _run(self, on_rx, on_up):
        backoff = 1.0
        while True:
            try:
                reader, writer = await asyncio.open_connection(self.host, self.port)
            except OSError as exc:
                LOGGER.warning("TCP connect to %s:%d failed (%s) — retrying in %.0fs",
                               self.host, self.port, exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, TCP_RECONNECT_MAX_S)
                continue
            sock = writer.get_extra_info("socket")
            if sock is not None:
                import socket as _socket
                sock.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1)
                # Enlarge the OS receive buffer (default ~64 KB) so a brief
                # host-side stall — a GC pause, a disk flush, an AV scan — is
                # absorbed here instead of closing the board's TCP window and
                # forcing a disconnect. 4 MB rides out ~10 s of backlog at our
                # ~3 Mbit/s, far longer than any transient hiccup.
                try:
                    sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_RCVBUF,
                                    4 * 1024 * 1024)
                except OSError:
                    pass
            self._writer = writer
            self.alive = True
            backoff = 1.0
            LOGGER.info("TCP link up: %s", self.name)
            on_up()
            try:
                while True:
                    # Large reads drain the kernel buffer in fewer syscalls,
                    # keeping the event loop ahead of the board's output.
                    data = await reader.read(65536)
                    if not data:
                        break
                    on_rx(data)
            except (ConnectionError, OSError) as exc:
                LOGGER.warning("TCP link error: %s", exc)
            self.alive = False
            self._writer = None
            LOGGER.warning("TCP link down: %s — reconnecting", self.name)
            on_rx(None)
            try:
                writer.close()
            except Exception:
                pass
            await asyncio.sleep(1.0)

    def write(self, data: bytes):
        if self._writer is not None:
            self._writer.write(data)

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                pass


# ---- bridge ---------------------------------------------------------------------


class Bridge:
    """Device link <-> WebSocket pipe with frame tap and IMULOG01 logging."""

    def __init__(self, link, log_dir: Path):
        self.link = link
        self.log_dir = log_dir
        self.parser = FrameParser()
        self.clients: Set["WsClient"] = set()
        self.log: Optional[imu_log.LogWriter] = None
        self.last_cfg: Optional[dict] = None
        self._samples = 0
        self._last_flush = 0.0
        self._last_stop_us = 0  # guards auto-open against in-flight frames after Q
        self._was_up = False

    # ---- device -> clients + log -------------------------------------------

    def on_device_rx(self, data: Optional[bytes]):
        if data is None:  # link dropped (serial: permanent; TCP: reconnecting)
            self.close_log("device link lost")
            self.notify_clients(
                "Device link lost"
                + ("" if self.link.alive else " — waiting for reconnect"), "err")
            return
        for client in self.clients:
            client.enqueue(data)
        now_us = time.time_ns() // 1000
        for ftype, payload in self.parser.feed(data):
            if ftype == FRAME_TYPE_IMU:
                if self.log is None:
                    # Frames that were already on the wire when "Q" went out
                    # must not reopen a log; anything later means the device
                    # really is streaming unprompted (e.g. server restarted).
                    if now_us - self._last_stop_us < 2_000_000:
                        continue
                    self.open_log(reason="stream detected")
                self.log.write(imu_log.REC_IMU, now_us, payload)
                self._samples += 1
            elif ftype == FRAME_TYPE_STATUS and self.log is not None:
                self.log.write(imu_log.REC_STATUS, now_us, payload)
            elif ftype == FRAME_TYPE_CAMERA and self.log is not None:
                self.log.write(imu_log.REC_CAMERA, now_us, payload)
            elif ftype == FRAME_TYPE_AUDIO and self.log is not None:
                self.log.write(imu_log.REC_AUDIO, now_us, payload)
        # bound data loss on crash to ~1 s without per-record flush cost
        if self.log is not None and time.monotonic() - self._last_flush > 1.0:
            self.log.flush()
            self._last_flush = time.monotonic()

    def on_link_up(self):
        if self._was_up:
            self.notify_clients(f"Device link restored ({self.link.name})", "ok")
        self._was_up = True

    # ---- clients -> device ---------------------------------------------------

    def on_client_cmd(self, text: str):
        if not self.link.alive:
            self.notify_clients("Command dropped — device link is down", "err")
            return
        self.link.write(text.encode("ascii", "replace"))
        now_us = time.time_ns() // 1000
        for line in text.split("\n"):
            cmd = line.strip()
            if not cmd:
                continue
            cfg = imu_log.parse_cfg_command(cmd)
            if cfg:
                self.last_cfg = cfg
            if cmd.upper() == "S":
                self.open_log(reason="start command")
            if self.log is not None:
                self.log.write(imu_log.REC_CMD, now_us, cmd.encode("ascii", "replace"))
            if cmd.upper() == "Q":
                self._last_stop_us = now_us
                self.close_log("stop command")

    # ---- log lifecycle -------------------------------------------------------

    def open_log(self, reason: str):
        if self.log is not None:
            return
        now_us = time.time_ns() // 1000
        # ms suffix + uniquify: rapid stop/start must never reuse a filename
        stem = "imu_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        path = self.log_dir / (stem + ".bin")
        n = 1
        while path.exists():
            path = self.log_dir / f"{stem}_{n}.bin"
            n += 1
        self.log = imu_log.LogWriter(path, self.link.baud, now_us)
        self._samples = 0
        meta = {
            "link": self.link.name,
            "baud": self.link.baud,
            "cfg": self.last_cfg,
            "opened_because": reason,
        }
        self.log.write(imu_log.REC_META, now_us, json.dumps(meta).encode("utf-8"))
        LOGGER.info("Log opened: %s (%s)", self.log.path, reason)
        self.notify_clients(f"Logging to {self.log.path.name}", "ok")

    def close_log(self, reason: str):
        if self.log is None:
            return
        path, samples = self.log.path, self._samples
        self.log.close()
        self.log = None
        LOGGER.info("Log closed: %s — %d samples (%s)", path, samples, reason)
        self.notify_clients(f"Log closed: {path.name} · {samples} samples", "ok")

    # ---- client management -----------------------------------------------------

    def add_client(self, client: "WsClient"):
        self.clients.add(client)
        LOGGER.info("Browser connected (%d client%s)", len(self.clients),
                    "s" if len(self.clients) != 1 else "")

    def remove_client(self, client: "WsClient"):
        self.clients.discard(client)
        LOGGER.info("Browser disconnected (%d left)", len(self.clients))
        if not self.clients:
            # nobody is watching: stop the device and finish the log file
            if self.link.alive and self.log is not None:
                self.link.write(b"Q\n")
                self._last_stop_us = time.time_ns() // 1000
            self.close_log("last client disconnected")

    def notify_clients(self, msg: str, level: str = "info"):
        text = json.dumps({"type": "log", "level": level, "msg": msg})
        for client in self.clients:
            client.enqueue_text(text)


class WsClient:
    """One WebSocket client with a bounded send queue (drop-oldest on overflow)."""

    def __init__(self, ws: web.WebSocketResponse):
        self.ws = ws
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=WS_QUEUE_MAX)

    def enqueue(self, data: bytes):
        self._put(("bin", data))

    def enqueue_text(self, text: str):
        self._put(("txt", text))

    def _put(self, item):
        if self.queue.full():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self.queue.put_nowait(item)

    async def sender(self):
        while True:
            kind, data = await self.queue.get()
            if kind == "bin":
                await self.ws.send_bytes(data)
            else:
                await self.ws.send_str(data)


# ---- aiohttp handlers ----------------------------------------------------------


async def handle_index(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(FRONTEND_HTML)


async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    bridge: Bridge = request.app["bridge"]
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)

    client = WsClient(ws)
    bridge.add_client(client)
    bridge.notify_clients(f"Server bridge on {bridge.link.name}", "info")
    sender = asyncio.ensure_future(client.sender())
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                bridge.on_client_cmd(msg.data)
            elif msg.type == WSMsgType.ERROR:
                LOGGER.warning("WebSocket error: %s", ws.exception())
    finally:
        sender.cancel()
        bridge.remove_client(client)
    return ws


def discover_board(port: int, timeout: float = 0.3) -> str:
    """Scan the laptop's current /24 for a host with `port` (the board's TCP
    server) open, and return its IP. Used in STA mode where the board takes a
    DHCP address from the hotspot instead of a fixed one — so you don't have to
    look the address up by hand each session.

    Method: find this machine's IP on the active (internet-bearing) interface,
    then probe every host .1-.254 on that /24 concurrently. The board is the
    only device on the hotspot listening on this port, so the first responder is
    it.
    """
    import socket as _socket
    from concurrent.futures import ThreadPoolExecutor

    probe = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    try:
        # No packets are actually sent for UDP connect(); it just picks the
        # source IP the OS would route through (the hotspot interface, which has
        # internet via the phone).
        probe.connect(("8.8.8.8", 80))
        local_ip = probe.getsockname()[0]
    except OSError:
        local_ip = "127.0.0.1"
    finally:
        probe.close()

    if local_ip.startswith("127."):
        raise SystemExit(
            "Auto-discovery could not determine this laptop's network IP. "
            "Make sure you've joined the hotspot, or pass --tcp <ip> explicitly.")

    base = local_ip.rsplit(".", 1)[0]
    LOGGER.info("Discovering board: scanning %s.0/24 for TCP port %d ...", base, port)

    def probe_host(host: str) -> Optional[str]:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            return host if s.connect_ex((host, port)) == 0 else None
        except OSError:
            return None
        finally:
            s.close()

    candidates = [f"{base}.{i}" for i in range(1, 255) if f"{base}.{i}" != local_ip]
    with ThreadPoolExecutor(max_workers=64) as ex:
        for result in ex.map(probe_host, candidates):
            if result:
                LOGGER.info("Found board at %s:%d", result, port)
                return result

    raise SystemExit(
        f"No device with TCP port {port} open was found on {base}.0/24.\n"
        "  - Check the board actually joined the hotspot (read its '[wifi] STA "
        "joined ... IP' line on the KitProg3 UART).\n"
        "  - Some Android hotspots isolate clients (block device-to-device). If "
        "so, the laptop can't reach the board — set WIFI_USE_STA 0 to use the "
        "board's own SoftAP instead.\n"
        "  - Or pass the address directly with --tcp <ip>.")


def autodetect_port() -> str:
    ports = list(serial.tools.list_ports.comports())
    kitprog = [p for p in ports if "kitprog" in (p.description or "").lower()]
    if kitprog:
        return kitprog[0].device
    if len(ports) == 1:
        return ports[0].device
    listing = "\n".join(f"  {p.device}: {p.description}" for p in ports) or "  (none)"
    raise SystemExit(
        "Could not auto-detect the KitProg3 port. Available ports:\n"
        f"{listing}\nUse --serial-port COMx to pick one, or --tcp <ip> for Wi-Fi."
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--serial-port", default=None,
                    help="serial port (default: auto-detect KitProg3)")
    ap.add_argument("--baud", type=int, default=115200,
                    help="serial baud rate (default: 115200, matches firmware)")
    ap.add_argument("--tcp", default=None, metavar="HOST[:PORT]",
                    help="connect to the device over TCP instead of serial, "
                         f"e.g. 192.168.10.1 (default port {TCP_DEFAULT_PORT}). "
                         "Use 'auto' (e.g. --tcp auto) to scan the current "
                         "network for the board — handy when it has a DHCP "
                         "address from a phone hotspot instead of a fixed IP.")
    ap.add_argument("--http-port", type=int, default=8765,
                    help="HTTP/WebSocket port (default: 8765)")
    ap.add_argument("--log-dir", type=Path, default=HERE / "logs",
                    help="directory for IMULOG01 files (default: ./logs)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s")

    if not FRONTEND_HTML.exists():
        raise SystemExit(f"Frontend not found: {FRONTEND_HTML}")

    if args.tcp:
        host, _, port_s = args.tcp.partition(":")
        port = int(port_s) if port_s else TCP_DEFAULT_PORT
        if host.lower() == "auto":
            host = discover_board(port)
        link = TcpLink(host, port)
    else:
        port_name = args.serial_port or autodetect_port()
        link = SerialLink(port_name, args.baud)
    LOGGER.info("Device link: %s", link.name)

    bridge = Bridge(link, args.log_dir)

    app = web.Application()
    app["bridge"] = bridge
    app.router.add_get("/", handle_index)
    app.router.add_get("/ws", handle_ws)

    async def on_startup(app_: web.Application):
        loop = asyncio.get_event_loop()
        link.start(loop, bridge.on_device_rx, bridge.on_link_up)

    async def on_cleanup(app_: web.Application):
        bridge.close_log("server shutdown")
        await link.stop()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    LOGGER.info("Open http://localhost:%d in Chrome/Edge "
                "(transport: Host server)", args.http_port)
    web.run_app(app, host="127.0.0.1", port=args.http_port, print=None)


if __name__ == "__main__":
    main()
