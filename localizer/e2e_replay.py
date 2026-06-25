# ABOUTME: End-to-end replay harness — feeds a real recording (IMU csv + camera frames zip) into the
# ABOUTME: derived topics the localizer consumes, then collects edge.position fixes and reports them.
#
# Run the localizer pointed at the same NATS, then:
#   python e2e_replay.py --imu ../data/test1/merged_20260623_17xx.csv \
#                        --frames-zip ../data/test1/merged_20260623_17xx_frames.zip
# Exits non-zero if no position fixes came back.

import argparse
import asyncio
import base64
import csv
import io
import json
import re
import zipfile

import nats

LINE = "line1"
CONTAINER = "box1"
FRAME_RE = re.compile(r"_f(\d+)_(\d+)\.jpg$")


def load_imu_events(path):
    """csv row -> (t_host_us, imu_msg, mag_msg|None). Already engineering units in the recording."""
    ev = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                th = int(float(r["t_host_us"])); tdev = int(float(r.get("t_dev_us", 0)))
            except (KeyError, ValueError):
                continue
            imu = {
                "t_us": tdev, "t_host_us": th,
                "acc_g": [float(r["acc_x_g"]), float(r["acc_y_g"]), float(r["acc_z_g"])],
                "gyr_dps": [float(r["gyr_x_dps"]), float(r["gyr_y_dps"]), float(r["gyr_z_dps"])],
                "temp_c": float(r.get("temp_c", 0)), "acc_range_g": 4, "gyr_range_dps": 2000,
            }
            mag = None
            if r.get("mag_x_ut"):
                mag = {"t_us": tdev, "t_host_us": th,
                       "mag_ut": [float(r["mag_x_ut"]), float(r["mag_y_ut"]), float(r["mag_z_ut"])]}
            ev.append((th, imu, mag))
    return ev


def load_camera_events(zip_path):
    """frame jpg -> (t_host_us, camera_msg). frame name: ..._f<frameid>_<t_host_us>.jpg."""
    ev = []
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            m = FRAME_RE.search(name)
            if not m:
                continue
            frame_id, th = int(m.group(1)), int(m.group(2))
            data = z.read(name)
            ev.append((th, {
                "frame_id": frame_id, "width": 0, "height": 0, "format": "jpeg",
                "encoding": "base64", "t_us": frame_id, "t_host_us": th,
                "data": base64.b64encode(data).decode("ascii"),
            }))
    return ev


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--imu", required=True)
    ap.add_argument("--frames-zip", required=True)
    ap.add_argument("--nats", default="nats://localhost:4222")
    ap.add_argument("--speedup", type=float, default=200.0, help="x realtime (0 = no pacing)")
    args = ap.parse_args()

    print("loading recording...", flush=True)
    events = []
    for th, imu, mag in load_imu_events(args.imu):
        events.append((th, "imu", imu))
        if mag:
            events.append((th, "mag", mag))
    cam = load_camera_events(args.frames_zip)
    for th, c in cam:
        events.append((th, "camera", c))
    events.sort(key=lambda e: e[0])
    n_imu = sum(1 for e in events if e[1] == "imu")
    n_mag = sum(1 for e in events if e[1] == "mag")
    n_cam = sum(1 for e in events if e[1] == "camera")
    print(f"events: imu={n_imu} mag={n_mag} camera={n_cam}  span={events[-1][0]-events[0][0]:.0f}us", flush=True)

    nc = await nats.connect(args.nats)
    fixes = []

    async def on_pos(m):
        fixes.append(json.loads(m.data))

    await nc.subscribe(f"edge.position.{LINE}.{CONTAINER}", cb=on_pos)
    await nc.flush()

    t0 = events[0][0]
    last = t0
    for i, (th, kind, msg) in enumerate(events):
        if args.speedup > 0:
            dt = (th - last) / 1e6 / args.speedup
            last = th
            if dt > 0:
                await asyncio.sleep(min(dt, 0.05))
        elif i % 200 == 0:
            await asyncio.sleep(0)                 # yield so localizer callbacks drain
        await nc.publish(f"edge.{kind}.{LINE}.{CONTAINER}", json.dumps(msg).encode())

    await nc.flush()
    await asyncio.sleep(2.0)                        # let the tail of fixes arrive
    await nc.drain()

    print(f"\nposition fixes received: {len(fixes)}", flush=True)
    if fixes:
        segs = {}
        for f in fixes:
            segs[f["segment"]] = segs.get(f["segment"], 0) + 1
        xs = [f["x"] for f in fixes]; ys = [f["y"] for f in fixes]
        print(f"  x range {min(xs):.2f}..{max(xs):.2f} m   y range {min(ys):.2f}..{max(ys):.2f} m")
        print(f"  segments: {segs}")
        print("  first 3:", fixes[:3])
        print("  last  3:", fixes[-3:])
    ok = len(fixes) > 0
    print("\nE2E", "PASS" if ok else "FAIL", flush=True)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
