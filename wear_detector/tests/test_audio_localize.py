# ABOUTME: Tests for audio<->lap-position correlation — bin acoustic anomalies by track phase.
# ABOUTME: Pure helper gets deterministic asserts; the full correlate() gets a synthetic smoke run.
import wave

import numpy as np

from wear_detector import audio_localize


def test_bin_audio_by_phase_phase_locked_is_high_contrast():
    # 10 laps of 30 s. Anomaly value spikes at the SAME lap phase (~0.5) every lap —
    # a track-fixed fault. The phase map must show high contrast and peak near 0.5.
    laps = [(k * 30.0, (k + 1) * 30.0) for k in range(10)]
    times, values = [], []
    for k in range(10):
        for ph in np.linspace(0.0, 1.0, 30, endpoint=False):
            t = k * 30.0 + ph * 30.0
            times.append(t)
            values.append(5.0 if abs(ph - 0.5) < 0.02 else 0.1)
    hmap = audio_localize.bin_audio_by_phase(laps, np.array(times), np.array(values))
    assert hmap.spatial_contrast() > 3.0
    assert abs(hmap.peak_phase() - 0.5) < 0.1
    assert hmap.classify_locus() == "track"


def test_bin_audio_by_phase_uniform_is_low_contrast():
    # Same energy total but spread across all phases (rides the unit) -> contrast ~1.
    laps = [(k * 30.0, (k + 1) * 30.0) for k in range(10)]
    rng = np.random.default_rng(0)
    times, values = [], []
    for k in range(10):
        for ph in np.linspace(0.0, 1.0, 30, endpoint=False):
            times.append(k * 30.0 + ph * 30.0)
            values.append(1.0 + 0.01 * rng.standard_normal())
    hmap = audio_localize.bin_audio_by_phase(laps, np.array(times), np.array(values))
    assert hmap.spatial_contrast() < 1.5
    assert hmap.classify_locus() in ("onboard", "unknown")


def test_bin_audio_by_phase_skips_windows_outside_any_lap():
    laps = [(10.0, 40.0)]
    times = np.array([5.0, 25.0, 50.0])      # before / inside / after the only lap
    values = np.array([9.0, 2.0, 9.0])
    hmap = audio_localize.bin_audio_by_phase(laps, times, values, n_bins=10)
    assert hmap.coverage() == 0.1             # exactly one bin populated (the inside one)


# ---- full correlate() smoke run on synthetic, time-aligned IMU + audio ----------

def _write_imu_csv(path, n_laps=8, lap_s=30.0, fs=100.0):
    """Figure-8-ish gyro: per lap one negative (minority -> crossover landmark) and two
    positive turns, >8 s apart, so detect_turns/segment_laps recover laps."""
    header = ("t_host_us,t_rel_s,t_dev_us,acc_x_g,acc_y_g,acc_z_g,"
              "gyr_x_dps,gyr_y_dps,gyr_z_dps,temp_c,mag_x_ut,mag_y_ut,mag_z_ut,"
              "acc_x_raw,acc_y_raw,acc_z_raw,gyr_x_raw,gyr_y_raw,gyr_z_raw,"
              "temp_raw,mag_x_raw,mag_y_raw,mag_z_raw")
    n = int(n_laps * lap_s * fs)
    lines = [header]
    for i in range(n):
        t = i / fs
        lap_t = t % lap_s
        gz = 0.0
        if lap_t < 1.0:
            gz = -220.0                       # crossover (minority) turn at lap start
        elif 10.0 <= lap_t < 11.0 or 20.0 <= lap_t < 21.0:
            gz = 220.0                        # two majority turns
        lines.append(f"{i*10000},{t:.6f},{(i % 3)*int(1e6/fs)},"
                     f"0.0,0.0,1.0,0.0,0.0,{gz:.1f},47.9,0,0,0,0,0,0,0,0,0,0,0,0,0")
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def _write_audio(path, n_laps=8, lap_s=30.0, fs=16000, fault_phase=0.5):
    """Quiet broadband run with a loud burst at the same lap phase every lap."""
    rng = np.random.default_rng(1)
    total = int(n_laps * lap_s * fs)
    x = 0.02 * rng.standard_normal(total)
    for k in range(n_laps):
        c = (k * lap_s + fault_phase * lap_s) * fs
        a, b = int(c - 0.25 * fs), int(c + 0.25 * fs)
        x[a:b] += 0.4 * rng.standard_normal(b - a)
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    pcm = np.repeat(pcm[:, None], 2, axis=1).reshape(-1)      # stereo
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(fs)
        w.writeframes(pcm.tobytes())
    return str(path)


def test_correlate_links_audio_faults_to_a_track_position(tmp_path):
    csv = _write_imu_csv(tmp_path / "imu.csv")
    wav = _write_audio(tmp_path / "audio.wav")
    res = audio_localize.correlate(csv, wav)
    assert res["laps"] >= 4                                   # laps recovered from gyro
    assert res["anomaly"]["coverage"] > 0.3
    # Faults injected at the same phase every lap -> track-localized, peak near 0.5.
    assert res["anomaly"]["contrast"] > 2.0
    assert abs(res["anomaly"]["peak_phase"] - 0.5) < 0.15
    assert res["anomaly"]["locus"] == "track"
