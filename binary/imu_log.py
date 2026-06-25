"""IMULOG01 binary log format — writer, reader, and CSV decoder CLI.

A log file captures the CRC-validated frames the PSOC Edge firmware streams
over UART (see proj_cm55/bmi270/uart_stream.h), each stamped with the host
wall clock, plus the commands that were sent to the device. Everything is
little-endian.

File layout
-----------
    Header (20 bytes):
        char[8]  magic        b"IMULOG01"
        uint64   t_start_us   host wall clock at file creation (unix epoch, us)
        uint32   baud         serial baud rate
    Records, back to back until EOF:
        uint8    rec_type
        uint64   t_host_us    host wall clock (unix epoch, us)
        uint16   len          payload length in bytes
        uint8[len] payload

Record types
------------
    0x01 META    UTF-8 JSON: serial port, baud, last known sensor CFG, ...
    0x02 CMD     ASCII command forwarded to the device ("S", "Q", "CFG,...")
    0x10 IMU     verbatim 24-byte wire payload (CRC already verified):
                     int32 t_us, int16 acc[3], int16 gyr[3],
                     int16 temp_raw, int16 mag[3]
    0x20 STATUS  verbatim status payload: imu_src(u8), mag_src(u8), reason text
    0x30 CAMERA  verbatim camera payload: u32 frame_id, u16 w, u16 h, JPEG
    0x40 AUDIO   verbatim audio payload: u32 seq, u16 sample_rate, u8 channels,
                 u8 bits, then interleaved signed PCM (L,R,L,R,...)

Scaling (matches bmi270_web_streaming.html):
    acc [g]   = raw / 32768 * acc_range_g
    gyr [dps] = raw / 32768 * gyr_range_dps
    temp [C]  = 23.0 + temp_raw / 512.0
    mag [uT]  = raw / 256.0

CLI:
    python imu_log.py dump logs/imu_20260611_153000.bin            # CSV to stdout
    python imu_log.py dump logs/imu_20260611_153000.bin -o out.csv
    python imu_log.py info logs/imu_20260611_153000.bin            # summary only
    python imu_log.py extract logs/imu_20260611_153000.bin         # JPEGs out
"""

from __future__ import annotations

import io
import json
import struct
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator, Optional

MAGIC = b"IMULOG01"
HEADER_FMT = "<8sQI"
HEADER_LEN = struct.calcsize(HEADER_FMT)   # 20
REC_HDR_FMT = "<BQH"
REC_HDR_LEN = struct.calcsize(REC_HDR_FMT)  # 11

REC_META = 0x01
REC_CMD = 0x02
REC_IMU = 0x10      # same value as the wire frame type
REC_STATUS = 0x20   # same value as the wire frame type
REC_CAMERA = 0x30   # camera frame: u32 frame_id, u16 w, u16 h, JPEG bitstream
REC_AUDIO = 0x40    # audio chunk: u32 seq, u16 rate, u8 channels, u8 bits, PCM

AUDIO_HDR_FMT = "<IHBB"
AUDIO_HDR_LEN = struct.calcsize(AUDIO_HDR_FMT)  # 8

IMU_PAYLOAD_FMT = "<i3h3hh3h"
IMU_PAYLOAD_LEN = struct.calcsize(IMU_PAYLOAD_FMT)  # 24
IMU_MIN_PAYLOAD = 18  # legacy firmware without magnetometer fields

# UI / firmware defaults, used when no CFG record precedes the samples
DEFAULT_ACC_RANGE_G = 4
DEFAULT_GYR_RANGE_DPS = 2000


class LogWriter:
    """Appends records to a freshly created IMULOG01 file."""

    def __init__(self, path: Path, baud: int, t_start_us: int):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f: Optional[BinaryIO] = open(self.path, "wb")
        self._f.write(struct.pack(HEADER_FMT, MAGIC, t_start_us, baud))
        self.records = 0

    def write(self, rec_type: int, t_host_us: int, payload: bytes) -> None:
        if self._f is None:
            return
        self._f.write(struct.pack(REC_HDR_FMT, rec_type, t_host_us, len(payload)))
        self._f.write(payload)
        self.records += 1

    def flush(self) -> None:
        if self._f is not None:
            self._f.flush()

    def close(self) -> None:
        if self._f is not None:
            self._f.close()
            self._f = None

    @property
    def closed(self) -> bool:
        return self._f is None


@dataclass
class Record:
    rec_type: int
    t_host_us: int
    payload: bytes


@dataclass
class ImuSample:
    t_us: int
    acc: tuple
    gyr: tuple
    temp_raw: int
    mag: tuple  # zeros for legacy 18-byte payloads


def decode_imu_payload(payload: bytes) -> ImuSample:
    if len(payload) >= IMU_PAYLOAD_LEN:
        v = struct.unpack(IMU_PAYLOAD_FMT, payload[:IMU_PAYLOAD_LEN])
        return ImuSample(v[0], v[1:4], v[4:7], v[7], v[8:11])
    v = struct.unpack("<i3h3hh", payload[:IMU_MIN_PAYLOAD])
    return ImuSample(v[0], v[1:4], v[4:7], v[7], (0, 0, 0))


def open_log(path: Path):
    """Returns ((t_start_us, baud), iterator over Record)."""
    f = open(path, "rb")
    hdr = f.read(HEADER_LEN)
    if len(hdr) != HEADER_LEN or hdr[:8] != MAGIC:
        f.close()
        raise ValueError(f"{path}: not an IMULOG01 file")
    _, t_start_us, baud = struct.unpack(HEADER_FMT, hdr)

    def records() -> Iterator[Record]:
        try:
            while True:
                rh = f.read(REC_HDR_LEN)
                if len(rh) < REC_HDR_LEN:
                    break  # clean EOF or truncated trailing record
                rec_type, t_host_us, ln = struct.unpack(REC_HDR_FMT, rh)
                payload = f.read(ln)
                if len(payload) < ln:
                    break  # truncated (e.g. server killed mid-write)
                yield Record(rec_type, t_host_us, payload)
        finally:
            f.close()

    return (t_start_us, baud), records()


def parse_cfg_command(cmd: str):
    """'CFG,100,4,200,2000,normal' -> dict, or None if not a CFG command."""
    parts = cmd.strip().split(",")
    if len(parts) < 5 or parts[0].upper() != "CFG":
        return None
    try:
        return {
            "acc_odr": int(parts[1]),
            "acc_range": int(parts[2]),
            "gyr_odr": int(parts[3]),
            "gyr_range": int(parts[4]),
            "power": parts[5] if len(parts) > 5 else "normal",
        }
    except ValueError:
        return None


CSV_HEADER = (
    "t_host_us,t_rel_s,t_dev_us,"
    "acc_x_g,acc_y_g,acc_z_g,gyr_x_dps,gyr_y_dps,gyr_z_dps,temp_c,"
    "mag_x_ut,mag_y_ut,mag_z_ut,"
    "acc_x_raw,acc_y_raw,acc_z_raw,gyr_x_raw,gyr_y_raw,gyr_z_raw,temp_raw,"
    "mag_x_raw,mag_y_raw,mag_z_raw"
)


def dump_csv(path: Path, out: io.TextIOBase, info_out: io.TextIOBase) -> None:
    (t_start_us, baud), records = open_log(path)
    start = datetime.fromtimestamp(t_start_us / 1e6, tz=timezone.utc)
    print(f"# file:    {path}", file=info_out)
    print(f"# started: {start.isoformat()}  baud: {baud}", file=info_out)

    acc_range = DEFAULT_ACC_RANGE_G
    gyr_range = DEFAULT_GYR_RANGE_DPS
    n_samples = 0
    n_camera = 0
    n_audio = 0
    out.write(CSV_HEADER + "\n")

    for rec in records:
        if rec.rec_type == REC_IMU:
            s = decode_imu_payload(rec.payload)
            a = [r / 32768 * acc_range for r in s.acc]
            g = [r / 32768 * gyr_range for r in s.gyr]
            m = [r / 256.0 for r in s.mag]
            temp_c = 23.0 + s.temp_raw / 512.0
            t_rel = (rec.t_host_us - t_start_us) / 1e6
            out.write(
                f"{rec.t_host_us},{t_rel:.6f},{s.t_us},"
                f"{a[0]:.6f},{a[1]:.6f},{a[2]:.6f},"
                f"{g[0]:.4f},{g[1]:.4f},{g[2]:.4f},{temp_c:.2f},"
                f"{m[0]:.3f},{m[1]:.3f},{m[2]:.3f},"
                f"{s.acc[0]},{s.acc[1]},{s.acc[2]},"
                f"{s.gyr[0]},{s.gyr[1]},{s.gyr[2]},{s.temp_raw},"
                f"{s.mag[0]},{s.mag[1]},{s.mag[2]}\n"
            )
            n_samples += 1
        elif rec.rec_type == REC_CMD:
            cmd = rec.payload.decode("ascii", "replace").strip()
            cfg = parse_cfg_command(cmd)
            if cfg:
                acc_range, gyr_range = cfg["acc_range"], cfg["gyr_range"]
            print(f"# cmd @ {rec.t_host_us}: {cmd}", file=info_out)
        elif rec.rec_type == REC_META:
            try:
                meta = json.loads(rec.payload)
            except ValueError:
                meta = {}
            cfg = meta.get("cfg")
            if cfg:
                acc_range = cfg.get("acc_range", acc_range)
                gyr_range = cfg.get("gyr_range", gyr_range)
            print(f"# meta: {rec.payload.decode('utf-8', 'replace')}", file=info_out)
        elif rec.rec_type == REC_STATUS:
            imu_src = rec.payload[0] if len(rec.payload) > 0 else 0
            mag_src = rec.payload[1] if len(rec.payload) > 1 else 0
            reason = rec.payload[2:].decode("ascii", "replace")
            print(f"# status: imu_src={imu_src} mag_src={mag_src} ({reason})",
                  file=info_out)
        elif rec.rec_type == REC_CAMERA:
            n_camera += 1
        elif rec.rec_type == REC_AUDIO:
            n_audio += 1

    print(f"# samples: {n_samples}", file=info_out)
    if n_camera:
        print(f"# camera frames: {n_camera} (use 'extract' to export JPEGs)",
              file=info_out)
    if n_audio:
        print(f"# audio chunks: {n_audio} (use 'audio' to export a WAV)",
              file=info_out)


def extract_camera(path: Path, out_dir: Path, info_out: io.TextIOBase) -> int:
    """Exports every camera record as <stem>_f<frame_id>_<t_host_us>.jpg."""
    (t_start_us, _), records = open_log(path)
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for rec in records:
        if rec.rec_type != REC_CAMERA or len(rec.payload) <= 8:
            continue
        frame_id, w, h = struct.unpack("<IHH", rec.payload[:8])
        jpg = out_dir / f"{path.stem}_f{frame_id:06d}_{rec.t_host_us}.jpg"
        jpg.write_bytes(rec.payload[8:])
        n += 1
    print(f"# extracted {n} camera frames to {out_dir}", file=info_out)
    return n


def extract_audio(path: Path, out_path: Path, info_out: io.TextIOBase) -> int:
    """Concatenates every audio chunk into a single WAV file.

    Format (rate/channels/bits) is taken from the first audio record. Chunks
    carry a running `seq`; a jump flags dropped chunks (a brief discontinuity),
    which is logged but not gap-filled.
    """
    import wave

    (_t_start_us, _), records = open_log(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wav: Optional["wave.Wave_write"] = None
    n = 0
    expect_seq: Optional[int] = None
    gaps = 0
    rate = channels = bits = 0
    for rec in records:
        if rec.rec_type != REC_AUDIO or len(rec.payload) <= AUDIO_HDR_LEN:
            continue
        seq, rate, channels, bits = struct.unpack(AUDIO_HDR_FMT,
                                                  rec.payload[:AUDIO_HDR_LEN])
        pcm = rec.payload[AUDIO_HDR_LEN:]
        if wav is None:
            wav = wave.open(str(out_path), "wb")
            wav.setnchannels(channels)
            wav.setsampwidth(bits // 8)
            wav.setframerate(rate)
        if expect_seq is not None and seq != expect_seq:
            gaps += 1
        expect_seq = seq + 1
        wav.writeframes(pcm)
        n += 1
    if wav is not None:
        wav.close()
        print(f"# wrote {out_path} ({n} chunks, {rate} Hz, {channels}ch, "
              f"{bits}-bit, {gaps} gap(s))", file=info_out)
    else:
        print("# no audio records in log", file=info_out)
    return n


def merge_logs(paths, out_path: Path, info_out: io.TextIOBase) -> int:
    """Merges several IMULOG01 files into one, ordered by host arrival time.

    Every record carries an absolute host timestamp (t_host_us, unix epoch), so
    merging is a stable sort of all records across the inputs by that stamp. The
    output header keeps the earliest t_start_us and the first non-zero baud, and
    all META/CMD/STATUS/IMU/CAMERA/AUDIO records are preserved verbatim so that
    later dump/extract/audio passes see CFG changes in the right order.
    """
    inputs = [Path(p) for p in paths]
    records: list = []        # (t_host_us, order, rec_type, payload)
    t_start_us = None
    baud = 0
    for src in inputs:
        (t0, b), recs = open_log(src)
        if t_start_us is None or t0 < t_start_us:
            t_start_us = t0
        if baud == 0 and b:
            baud = b
        n_src = 0
        for rec in recs:
            records.append((rec.t_host_us, len(records), rec.rec_type, rec.payload))
            n_src += 1
        print(f"# {src.name}: {n_src} records", file=info_out)

    if t_start_us is None:
        print("# no input records", file=info_out)
        return 0

    records.sort(key=lambda r: (r[0], r[1]))  # stable by arrival time

    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = LogWriter(out_path, baud, t_start_us)
    for t_host_us, _order, rec_type, payload in records:
        writer.write(rec_type, t_host_us, payload)
    writer.close()
    print(f"# merged {len(inputs)} files -> {out_path} ({len(records)} records)",
          file=info_out)
    return len(records)


def info(path: Path, out: io.TextIOBase) -> None:
    (t_start_us, baud), records = open_log(path)
    start = datetime.fromtimestamp(t_start_us / 1e6, tz=timezone.utc)
    counts: dict = {}
    first_us = last_us = None
    for rec in records:
        counts[rec.rec_type] = counts.get(rec.rec_type, 0) + 1
        if rec.rec_type == REC_IMU:
            if first_us is None:
                first_us = rec.t_host_us
            last_us = rec.t_host_us
    names = {REC_META: "META", REC_CMD: "CMD", REC_IMU: "IMU",
             REC_STATUS: "STATUS", REC_CAMERA: "CAMERA", REC_AUDIO: "AUDIO"}
    print(f"file:     {path}", file=out)
    print(f"started:  {start.isoformat()}", file=out)
    print(f"baud:     {baud}", file=out)
    for t, n in sorted(counts.items()):
        print(f"records:  {names.get(t, hex(t))} x {n}", file=out)
    if first_us is not None and last_us is not None and last_us > first_us:
        dur = (last_us - first_us) / 1e6
        rate = (counts.get(REC_IMU, 1) - 1) / dur if dur > 0 else 0
        print(f"duration: {dur:.2f} s  (~{rate:.1f} samples/s)", file=out)


def main(argv) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Decode IMULOG01 binary logs")
    sub = p.add_subparsers(dest="cmd", required=True)
    pd = sub.add_parser("dump", help="decode IMU samples to CSV")
    pd.add_argument("file", type=Path)
    pd.add_argument("-o", "--out", type=Path, default=None,
                    help="output CSV path (default: stdout)")
    pi = sub.add_parser("info", help="print a summary of the log file")
    pi.add_argument("file", type=Path)
    pe = sub.add_parser("extract", help="export camera frames (JPEG) + audio (WAV)")
    pe.add_argument("file", type=Path)
    pe.add_argument("-o", "--out-dir", type=Path, default=None,
                    help="output directory (default: <logfile>_frames/)")
    pa = sub.add_parser("audio", help="export the audio track as a WAV file")
    pa.add_argument("file", type=Path)
    pa.add_argument("-o", "--out", type=Path, default=None,
                    help="output WAV path (default: <logfile>.wav)")
    pm = sub.add_parser("merge", help="merge several logs into one, by host time")
    pm.add_argument("files", type=Path, nargs="+")
    pm.add_argument("-o", "--out", type=Path, required=True,
                    help="output merged IMULOG01 path")

    args = p.parse_args(argv)
    if args.cmd == "merge":
        merge_logs(args.files, args.out, sys.stderr)
        print(f"wrote {args.out}", file=sys.stderr)
        return 0
    if args.cmd == "info":
        info(args.file, sys.stdout)
        return 0
    if args.cmd == "extract":
        out_dir = args.out_dir or args.file.with_name(args.file.stem + "_frames")
        extract_camera(args.file, out_dir, sys.stderr)
        extract_audio(args.file, out_dir / f"{args.file.stem}.wav", sys.stderr)
        return 0
    if args.cmd == "audio":
        out_path = args.out or args.file.with_suffix(".wav")
        extract_audio(args.file, out_path, sys.stderr)
        return 0
    if args.out:
        with open(args.out, "w", newline="") as f:
            dump_csv(args.file, f, sys.stderr)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        dump_csv(args.file, sys.stdout, sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
