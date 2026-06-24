# ABOUTME: Tests for IMU loaders — the new merged-CSV recorder format and window dispatch.
# ABOUTME: Synthetic fixtures keep these fast and independent of the large committed recordings.
import numpy as np

from wear_detector.io_imu import iter_windows, load_imu_csv

# Header of the merged recorder CSV (data/test1/*.csv): SI columns + raw counts.
HEADER = ("t_host_us,t_rel_s,t_dev_us,acc_x_g,acc_y_g,acc_z_g,"
          "gyr_x_dps,gyr_y_dps,gyr_z_dps,temp_c,mag_x_ut,mag_y_ut,mag_z_ut,"
          "acc_x_raw,acc_y_raw,acc_z_raw,gyr_x_raw,gyr_y_raw,gyr_z_raw,"
          "temp_raw,mag_x_raw,mag_y_raw,mag_z_raw")


def _write_csv(path, n=300, fs=100.0):
    """A merged-format CSV: device clock steps at 1/fs, delivered in 3-sample bursts
    (t_rel repeats within a burst, t_dev resets across bursts) — like the real recorder."""
    lines = [HEADER]
    for i in range(n):
        t_dev_us = (i % 3) * int(1e6 / fs)          # resets every 3-sample burst
        t_rel_s = (i // 3) * (3.0 / fs)             # advances once per burst
        acc = (0.01 * np.sin(i / 5.0), 0.0, 1.0)    # ~1g on z + a tone on x
        gyr = (0.1, 0.0, 10.0 * (i % 2))
        lines.append(f"{i*10000},{t_rel_s:.6f},{t_dev_us},"
                     f"{acc[0]:.6f},{acc[1]:.6f},{acc[2]:.6f},"
                     f"{gyr[0]:.4f},{gyr[1]:.4f},{gyr[2]:.4f},"
                     f"47.9,0,0,0,0,0,0,0,0,0,0,0,0,0")
    path.write_text("\n".join(lines) + "\n")
    return path


def test_load_imu_csv_shapes_and_units(tmp_path):
    csv = _write_csv(tmp_path / "merged.csv", n=300)
    t, accel, gyro = load_imu_csv(str(csv))
    assert accel.shape == (300, 3) and gyro.shape == (300, 3)
    assert t.shape == (300,)
    # SI columns are read directly: z near 1 g, gyro z averages ~5 dps.
    assert abs(np.median(accel[:, 2]) - 1.0) < 0.05
    assert np.isfinite(accel).all() and np.isfinite(gyro).all()


def test_load_imu_csv_infers_nominal_fs_despite_bursts(tmp_path):
    # Burst delivery makes naive t_rel diffs zero; the loader must recover 100 Hz
    # from the device clock and hand back a uniform, strictly increasing timeline.
    csv = _write_csv(tmp_path / "merged.csv", n=300, fs=100.0)
    t, _, _ = load_imu_csv(str(csv))
    dt = np.diff(t)
    assert np.all(dt > 0)
    assert abs(1.0 / np.median(dt) - 100.0) < 1.0


def test_iter_windows_accepts_csv_path_with_session_label(tmp_path):
    csv = _write_csv(tmp_path / "merged.csv", n=500, fs=100.0)
    wins = list(iter_windows(str(csv), window_s=1.0, overlap=0.5, session_label="fault"))
    assert wins, "expected at least one window"
    label, accel, gyro, fs = wins[0]
    assert label == "fault"           # whole-session label for the unlabeled recorder format
    assert abs(fs - 100.0) < 1.0
    assert accel.shape == (int(round(1.0 * fs)), 3)
