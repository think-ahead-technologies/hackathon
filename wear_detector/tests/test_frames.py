# ABOUTME: Tests for frame<->anomaly correlation — parse frame index, recover origin, match by time.
# ABOUTME: Synthetic zip + CSV keep it hermetic; no dependency on the large committed recording.
import zipfile

from wear_detector import frames


def _make_zip(path, stamps):
    """stamps: list of (frame_no, host_us). Names match the recorder convention."""
    with zipfile.ZipFile(str(path), "w") as z:
        for fn, us in stamps:
            z.writestr(f"merged_x_f{fn:06d}_{us}.jpg", b"\xff\xd8\xff\xd9")  # tiny JPEG
    return str(path)


def _make_csv(path, origin_us=1_000_000_000, fs=100.0, n=300):
    header = "t_host_us,t_rel_s,t_dev_us,acc_x_g,acc_y_g,acc_z_g,gyr_x_dps,gyr_y_dps,gyr_z_dps"
    lines = [header]
    for i in range(n):
        t_rel = i / fs
        host = int(origin_us + t_rel * 1e6)
        lines.append(f"{host},{t_rel:.6f},{(i % 3)*int(1e6/fs)},0,0,1,0,0,0")
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def test_parse_frame_index_sorted_by_time(tmp_path):
    z = _make_zip(tmp_path / "f.zip", [(12, 2_000), (10, 1_000), (11, 1_500)])
    idx = frames.parse_frame_index(z)
    assert [f[0] for f in idx] == [10, 11, 12]        # sorted by host_us
    assert [f[1] for f in idx] == [1_000, 1_500, 2_000]


def test_recording_origin_us_recovers_t_rel_zero(tmp_path):
    csv = _make_csv(tmp_path / "imu.csv", origin_us=1_782_000_000_000)
    assert abs(frames.recording_origin_us(csv) - 1_782_000_000_000) < 1.0


def test_nearest_frame_picks_closest_and_reports_delta(tmp_path):
    z = _make_zip(tmp_path / "f.zip", [(10, 1_000_000), (11, 1_100_000), (12, 1_200_000)])
    idx = frames.parse_frame_index(z)
    fr, dt = frames.nearest_frame(idx, 1_180_000)
    assert fr[0] == 12                                # 1.20 Ms is closest to 1.18 Ms
    assert abs(dt - 0.02) < 1e-6                      # frame is 20 ms after the query


def test_correlate_events_maps_audio_time_to_frames(tmp_path):
    origin = 1_000_000_000
    csv = _make_csv(tmp_path / "imu.csv", origin_us=origin)
    # one frame per 0.5 s for 5 s, at the same clock as the IMU
    z = _make_zip(tmp_path / "f.zip",
                  [(i, origin + int(i * 0.5 * 1e6)) for i in range(10)])
    idx = frames.parse_frame_index(z)
    origin_us = frames.recording_origin_us(csv)
    matches = frames.correlate_events(idx, origin_us, [1.0, 2.5])
    assert matches[0]["frame_no"] == 2               # t=1.0s -> frame at 1.0s
    assert matches[1]["frame_no"] == 5               # t=2.5s -> frame at 2.5s
    assert all(abs(m["dt_s"]) < 0.26 for m in matches)


def test_extract_frames_writes_files(tmp_path):
    z = _make_zip(tmp_path / "f.zip", [(10, 1_000), (11, 2_000)])
    idx = frames.parse_frame_index(z)
    out = tmp_path / "out"
    paths = frames.extract_frames(z, [idx[0][2]], str(out))
    assert len(paths) == 1
    assert paths[0].endswith(".jpg")
    assert (out / "merged_x_f000010_1000.jpg").exists()
