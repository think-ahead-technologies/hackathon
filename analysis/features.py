"""Windowed IMU feature extraction for bearing-fault detection.

A bearing fault shows up as a short burst of elevated high-frequency, impulsive
accelerometer vibration as the instrumented box rolls past the defective idler
wheel. Gyro is used to recognise (and de-confound) turns. Features are cheap
enough to run on the target MCU.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from scipy import signal, stats
import imloader as L

WIN_S = 1.0      # window length (seconds)
HOP_S = 0.5      # hop (seconds)

FEATURE_NAMES = [
    "acc_hp_rms",      # RMS of >5Hz accel magnitude  (vibration energy)
    "acc_hp_p2p",      # peak-to-peak of HP accel
    "acc_crest",       # crest factor = peak/rms (impulsiveness)
    "acc_kurt",        # kurtosis of HP accel (impulsiveness, classic bearing metric)
    "acc_band_5_10",   # FFT band energy fraction 5-10 Hz
    "acc_band_10_15",  # 10-15 Hz
    "acc_band_15_25",  # 15-25 Hz (near Nyquist)
    "acc_centroid",    # spectral centroid (Hz) of HP accel
    "gyro_rms",        # gyro magnitude RMS (turn indicator)
    "acc_lf_rms",      # low-freq (<2Hz) accel mag std (gross motion)
]


def _bandpass_design(fs):
    b_hp, a_hp = signal.butter(4, 5.0 / (fs / 2), btype="high")
    return b_hp, a_hp


def extract_windows(imu: pd.DataFrame, fs: float):
    """Return (feat[N,F], centers[N]) of window features and window center times."""
    t = imu["t"].values
    acc = imu[["ax", "ay", "az"]].values
    gyr = imu[["gx", "gy", "gz"]].values
    acc_mag = np.linalg.norm(acc, axis=1)
    gyr_mag = np.linalg.norm(gyr, axis=1)

    b_hp, a_hp = _bandpass_design(fs)
    acc_hp = signal.filtfilt(b_hp, a_hp, acc_mag)
    b_lf, a_lf = signal.butter(2, 2.0 / (fs / 2), btype="low")
    acc_lf = signal.filtfilt(b_lf, a_lf, acc_mag)

    w = int(round(WIN_S * fs))
    h = int(round(HOP_S * fs))
    feats, centers = [], []
    freqs = np.fft.rfftfreq(w, d=1.0 / fs)
    for s in range(0, len(t) - w + 1, h):
        e = s + w
        seg = acc_hp[s:e]
        rms = np.sqrt(np.mean(seg**2)) + 1e-9
        peak = np.max(np.abs(seg))
        # FFT band energies on HP accel
        spec = np.abs(np.fft.rfft(seg * np.hanning(w))) ** 2
        tot = spec.sum() + 1e-12
        band = lambda lo, hi: spec[(freqs >= lo) & (freqs < hi)].sum() / tot
        centroid = (freqs * spec).sum() / tot
        f = [
            rms,
            peak * 2,
            peak / rms,
            stats.kurtosis(seg),
            band(5, 10),
            band(10, 15),
            band(15, 25),
            centroid,
            np.sqrt(np.mean(gyr_mag[s:e] ** 2)),
            np.std(acc_lf[s:e]),
        ]
        feats.append(f)
        centers.append((t[s] + t[e - 1]) / 2)
    return np.array(feats), np.array(centers)


def window_labels(centers: np.ndarray, labels: pd.DataFrame, fault_names=("fault",)):
    """1 if window center falls in a fault label, else 0."""
    y = np.zeros(len(centers), dtype=int)
    for _, r in labels.iterrows():
        if str(r["label"]) in fault_names:
            y[(centers >= r["start"]) & (centers < r["end"])] = 1
    return y


# session classification for building a dataset
FAULT_SESSIONS = [  # have explicit 'fault' labels
    "Session-2026-06-17--12-05-06_faulty_bearing",
    "Session-2026-06-17--12-06-11_faulty_bearing",
    "Session-2026-06-17--12-08-11_faulty_bearing",
    "Session-2026-06-17--12-10-03_faulty_bearing",
    "Session-2026-06-17--12-11-32_bearing_failure",
    "Session-2026-06-17--13-49-38_faulty_bearing",
    "Session-2026-06-17--18-24-09-faulty-bearing",
    "Session-2026-06-17--18-53-07-additional-4-defective-idler-wheel-bearings",
    "Session-2026-06-17--19-11-06-cummulated-8-defective-idler-wheel-bearings",
    "Session-2026-06-17--19-25-09-additional-2-broken-bearings",
]
NORMAL_SESSIONS = [  # no faults present at all -> all windows are negatives
    "Session-2026-06-17--11-25-33_all_normal",
    "Session-2026-06-17--11-26-26_all_normal",
    "Session-2026-06-17--10-46-14_normal",
    "Session-2026-06-18--10-27-16-normal-data-without-defective-parts",
]
