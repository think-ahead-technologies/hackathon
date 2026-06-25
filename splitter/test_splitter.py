# ABOUTME: Unit tests for the splitter's pure decode/scale logic — no NATS, no hardware.

import struct

import main


def _imu_payload(t_us, acc, gyr, temp, mag=None):
    if mag is None:
        return struct.pack("<i3h3hh", t_us, *acc, *gyr, temp)
    return struct.pack(main.IMU_FMT, t_us, *acc, *gyr, temp, *mag)


def _record(rec_type, t_host_us, payload):
    return struct.pack(main.REC_HDR_FMT, rec_type, t_host_us, len(payload)) + payload


def test_parse_record_roundtrip():
    rec = _record(main.REC_IMU, 1750000000000000, b"abc")
    assert main.parse_record(rec) == (main.REC_IMU, 1750000000000000, b"abc")


def test_parse_record_too_short():
    assert main.parse_record(b"\x10\x00") is None


def test_parse_cfg_command():
    assert main.parse_cfg_command("CFG,100,8,200,1000,normal") == {"acc_range": 8, "gyr_range": 1000}
    assert main.parse_cfg_command("S") is None
    assert main.parse_cfg_command("CFG,bad") is None


def test_decode_imu_full_and_legacy():
    full = _imu_payload(123, (100, 200, 32767), (1, 2, 3), 512, (256, -256, 0))
    dec = main.decode_imu(full)
    assert dec[0] == 123 and dec[4] == (256, -256, 0)
    legacy = _imu_payload(123, (1, 2, 3), (4, 5, 6), 0)
    assert main.decode_imu(legacy)[4] is None


def test_imu_message_scaling():
    dec = (123, (32768, 0, 0), (0, 32768, 0), 512, (0, 0, 0))
    msg = main.imu_message(dec, acc_range=4, gyr_range=2000, t_host_us=999)
    assert msg["acc_g"][0] == 4.0           # 32768/32768 * 4
    assert msg["gyr_dps"][1] == 2000.0
    assert msg["temp_c"] == 24.0            # 23 + 512/512
    assert msg["t_host_us"] == 999


def test_mag_message():
    dec = (1, (0,), (0,), 0, (256, 512, -256))
    assert main.mag_message(dec, 5)["mag_ut"] == [1.0, 2.0, -1.0]
    legacy = (1, (0,), (0,), 0, None)
    assert main.mag_message(legacy, 5) is None


def test_camera_message():
    jpg = b"\xff\xd8\xff\xd9"
    payload = struct.pack(main.CAM_HDR_FMT, 42, 640, 480) + jpg
    msg = main.camera_message(payload, 777)
    assert msg["frame_id"] == 42 and msg["width"] == 640 and msg["height"] == 480
    import base64
    assert base64.b64decode(msg["data"]) == jpg
    assert main.camera_message(b"\x00" * 4, 1) is None  # header-only, no image


def test_out_subject():
    assert main.out_subject("imu", "line1", "cnc-7") == "edge.imu.line1.cnc-7"


def test_fusion_emits_on_window():
    main.POS_WINDOW = 3
    f = main.Fusion("line1")
    f._t_host = 1
    out = [f.update((i * 10000, (10, 0, 16384), (0, 0, 0), 0, (256, 0, 0))) for i in range(1, 7)]
    fixes = [o for o in out if o is not None]
    assert fixes and all("segment" in fx and "x" in fx and "y" in fx for fx in fixes)
