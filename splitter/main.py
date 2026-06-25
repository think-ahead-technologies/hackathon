# ABOUTME: Splitter — consumes the `raw` topic (edge.raw.<line>.<container>) and fans each
# ABOUTME: IMULOG01 record out to the derived sensor topics: imu-data, magnetometer-data, camera-data.
# ABOUTME: positinal-data is produced downstream by the `localizer` service (real fusion pipeline).

import asyncio
import base64
import json
import math
import os
import ssl
import struct

import nats

# --- config ---------------------------------------------------------------

NATS_URL = os.environ.get("NATS_URL", "nats://nats:4222")
# Per-identity nkey seed (CRA unique-credential auth). Unset on the open demo fabric.
NATS_NKEY_SEED = os.environ.get("NATS_NKEY_SEED")
# CA that signed the NATS server cert (CRA confidentiality in transit). Unset -> plaintext demo.
NATS_CA_FILE = os.environ.get("NATS_CA_FILE")
NATS_TLS_HOSTNAME = os.environ.get("NATS_TLS_HOSTNAME", "nats")
# Subject the raw producer publishes to (raw/contract.md): edge.raw.<line>.<container>.
RAW_SUBJECT = os.environ.get("RAW_SUBJECT", "edge.raw.*.*")
# Emit one position fix per this many IMU samples (fusion window).
POS_WINDOW = int(os.environ.get("POS_WINDOW", "50"))

# --- IMULOG01 record framing (binary/imu_log.py) --------------------------

REC_HDR_FMT = "<BQH"                       # u8 type | u64 t_host_us | u16 len
REC_HDR_LEN = struct.calcsize(REC_HDR_FMT)  # 11

REC_META = 0x01
REC_CMD = 0x02
REC_IMU = 0x10
REC_STATUS = 0x20
REC_CAMERA = 0x30
REC_AUDIO = 0x40

IMU_FMT = "<i3h3hh3h"                # i32 t_us, i16 acc[3], gyr[3], temp, mag[3]
IMU_LEN = struct.calcsize(IMU_FMT)   # 24
IMU_MIN_LEN = 18                     # legacy: no magnetometer
CAM_HDR_FMT = "<IHH"                 # u32 frame_id, u16 w, u16 h
CAM_HDR_LEN = struct.calcsize(CAM_HDR_FMT)  # 8

# UI/firmware defaults until a CFG (CMD/META) overrides them.
DEFAULT_ACC_RANGE_G = 4
DEFAULT_GYR_RANGE_DPS = 2000


# --- pure logic (unit-tested in test_splitter.py) -------------------------

def parse_record(data: bytes):
    """Split one raw NATS message into (rec_type, t_host_us, payload). None if too short."""
    if len(data) < REC_HDR_LEN:
        return None
    rec_type, t_host_us, ln = struct.unpack(REC_HDR_FMT, data[:REC_HDR_LEN])
    payload = data[REC_HDR_LEN:REC_HDR_LEN + ln]
    if len(payload) < ln:
        return None
    return rec_type, t_host_us, payload


def parse_cfg_command(cmd: str):
    """'CFG,100,4,200,2000,normal' -> {acc_range, gyr_range, ...}, or None."""
    parts = cmd.strip().split(",")
    if len(parts) < 5 or parts[0].upper() != "CFG":
        return None
    try:
        return {"acc_range": int(parts[2]), "gyr_range": int(parts[4])}
    except ValueError:
        return None


def decode_imu(payload: bytes):
    """Raw IMU payload -> (t_us, acc[3], gyr[3], temp_raw, mag[3]|None). mag None for legacy 18 B."""
    if len(payload) >= IMU_LEN:
        v = struct.unpack(IMU_FMT, payload[:IMU_LEN])
        return v[0], v[1:4], v[4:7], v[7], v[8:11]
    if len(payload) >= IMU_MIN_LEN:
        v = struct.unpack("<i3h3hh", payload[:IMU_MIN_LEN])
        return v[0], v[1:4], v[4:7], v[7], None
    return None


def imu_message(dec, acc_range: int, gyr_range: int, t_host_us: int) -> dict:
    """Scale a decoded IMU sample to engineering units (imu-data/contract.md)."""
    t_us, acc, gyr, temp_raw, _mag = dec
    return {
        "t_us": t_us,
        "t_host_us": t_host_us,
        "acc_g": [r / 32768 * acc_range for r in acc],
        "gyr_dps": [r / 32768 * gyr_range for r in gyr],
        "temp_c": 23.0 + temp_raw / 512.0,
        "acc_range_g": acc_range,
        "gyr_range_dps": gyr_range,
    }


def mag_message(dec, t_host_us: int):
    """Scale the mag triplet to µT (magnetometer-data/contract.md). None for legacy frames."""
    t_us, _acc, _gyr, _temp, mag = dec
    if mag is None:
        return None
    return {"t_us": t_us, "t_host_us": t_host_us, "mag_ut": [r / 256.0 for r in mag]}


def camera_message(payload: bytes, t_host_us: int):
    """Raw camera payload (<IHH + JPEG) -> camera-data/contract.md JSON. None if no image."""
    if len(payload) <= CAM_HDR_LEN:
        return None
    frame_id, w, h = struct.unpack(CAM_HDR_FMT, payload[:CAM_HDR_LEN])
    return {
        "frame_id": frame_id,
        "width": w,
        "height": h,
        "format": "jpeg",
        "encoding": "base64",
        "t_us": frame_id,  # device capture clock not in header; frame_id is the only device counter
        "t_host_us": t_host_us,
        "data": base64.b64encode(payload[CAM_HDR_LEN:]).decode("ascii"),
    }


def segment_for(x: float, y: float, line: str) -> str:
    """Coarse zone id from the map coordinate — a 5 m grid (A,B,C... by row/col)."""
    col = int(x // 5)
    row = chr(ord("A") + max(0, int(y // 5)) % 26)
    return f"{line}.station-{row}{col}"


class Fusion:
    """Drift-prone dead-reckoning: integrate accel->velocity->position, heading from mag.

    A real fix needs proper sensor fusion + a floor map; this is a deliberate estimate
    (positinal-data/contract.md flags it as such) so the topic carries plausible x/y/segment.
    """

    def __init__(self, line: str):
        self.line = line
        self.t_prev = None
        self.vx = self.vy = 0.0
        self.x = self.y = 0.0
        self.heading = 0.0
        self.n = 0

    def update(self, dec):
        t_us, acc, gyr, _temp, mag = dec
        self.n += 1
        if self.t_prev is None:
            self.t_prev = t_us
            return None
        dt = (t_us - self.t_prev) / 1e6
        self.t_prev = t_us
        if dt <= 0 or dt > 1.0:        # clock reset / gap — skip integration step
            return None
        if mag is not None:
            self.heading = math.atan2(mag[1], mag[0])
        # horizontal accel in g -> m/s^2; z holds gravity, ignore it
        ax = acc[0] / 32768 * 4 * 9.81
        ay = acc[1] / 32768 * 4 * 9.81
        self.vx = self.vx * 0.9 + ax * dt   # leak term damps unbounded drift
        self.vy = self.vy * 0.9 + ay * dt
        cos_h, sin_h = math.cos(self.heading), math.sin(self.heading)
        self.x += (self.vx * cos_h - self.vy * sin_h) * dt
        self.y += (self.vx * sin_h + self.vy * cos_h) * dt
        if self.n % POS_WINDOW:
            return None
        return {
            "t_us": t_us,
            "t_host_us": self._t_host,
            "segment": segment_for(self.x, self.y, self.line),
            "x": round(self.x, 3),
            "y": round(self.y, 3),
        }

    _t_host = 0


# --- subjects --------------------------------------------------------------

def out_subject(kind: str, line: str, container: str) -> str:
    return f"edge.{kind}.{line}.{container}"


# --- wiring ----------------------------------------------------------------

async def main() -> None:
    auth = {}
    if NATS_NKEY_SEED:
        auth["nkeys_seed"] = NATS_NKEY_SEED
    if NATS_CA_FILE:
        auth["tls"] = ssl.create_default_context(cafile=NATS_CA_FILE)
        auth["tls_hostname"] = NATS_TLS_HOSTNAME
    nc = await nats.connect(NATS_URL, reconnect_time_wait=2, max_reconnect_attempts=-1, **auth)

    cfg: dict = {}       # (line,container) -> {acc_range, gyr_range}

    async def pub(kind, line, container, msg):
        await nc.publish(out_subject(kind, line, container), json.dumps(msg).encode())

    async def on_raw(m):
        try:
            # subject = edge.raw.<line>.<container>
            _, _, line, container = m.subject.split(".", 3)
            rec = parse_record(m.data)
            if rec is None:
                return
            rec_type, t_host_us, payload = rec
            key = (line, container)

            if rec_type == REC_CMD:
                c = parse_cfg_command(payload.decode("ascii", "replace"))
                if c:
                    cfg[key] = c
            elif rec_type == REC_META:
                try:
                    meta = json.loads(payload)
                    c = meta.get("cfg") or {}
                    if "acc_range" in c or "gyr_range" in c:
                        cfg[key] = {
                            "acc_range": c.get("acc_range", DEFAULT_ACC_RANGE_G),
                            "gyr_range": c.get("gyr_range", DEFAULT_GYR_RANGE_DPS),
                        }
                except ValueError:
                    pass
            elif rec_type == REC_IMU:
                dec = decode_imu(payload)
                if dec is None:
                    return
                c = cfg.get(key, {})
                acc_range = c.get("acc_range", DEFAULT_ACC_RANGE_G)
                gyr_range = c.get("gyr_range", DEFAULT_GYR_RANGE_DPS)
                await pub("imu", line, container, imu_message(dec, acc_range, gyr_range, t_host_us))
                mag = mag_message(dec, t_host_us)
                if mag is not None:
                    await pub("mag", line, container, mag)
                # position is NOT emitted here — the localizer service fuses camera+imu+mag into
                # edge.position downstream (see localizer/main.py).
            elif rec_type == REC_CAMERA:
                cam = camera_message(payload, t_host_us)
                if cam is not None:
                    await pub("camera", line, container, cam)
            # STATUS / AUDIO: no derived topic — drop.
        except Exception as exc:  # noqa: BLE001 — never kill the subscription on one bad msg
            print(f"[splitter] dropped: {exc}", flush=True)

    await nc.subscribe(RAW_SUBJECT, cb=on_raw)
    print(f"[splitter] {RAW_SUBJECT} -> edge.imu/mag/camera.<line>.<container>", flush=True)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    import signal
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await nc.drain()


if __name__ == "__main__":
    asyncio.run(main())
