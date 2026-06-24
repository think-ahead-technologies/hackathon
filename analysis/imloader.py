"""Loader utilities for Imagimob Studio session data (thinkathon).

Sessions live in ../thinkathon_kickstart/data/<Session-...>/ and contain:
  IMU-Data.data                  CSV: # Time (seconds),Accel_X,Accel_Y,Accel_Z,Gyro_X,Gyro_Y,Gyro_Z  (~50 Hz)
  Magnetometer-Data.data         CSV: # Time (seconds),X,Y,Z
  Combined-IMU-and-Magnetometer.data
  Microphone-Data.wav            mono 16 kHz
  Live-Labeling.label            CSV: Time(Seconds),Length(Seconds),Label(string),Confidence(double),Comment
"""
from __future__ import annotations
import os, glob, wave
import numpy as np
import pandas as pd

DATA_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                         "thinkathon_kickstart", "data"))

IMU_COLS = ["t", "ax", "ay", "az", "gx", "gy", "gz"]


def list_sessions(root: str = DATA_ROOT):
    return sorted(d for d in glob.glob(os.path.join(root, "Session-*")) if os.path.isdir(d))


def load_imu(session_dir: str) -> pd.DataFrame | None:
    f = os.path.join(session_dir, "IMU-Data.data")
    if not os.path.exists(f):
        return None
    df = pd.read_csv(f, comment=None, skiprows=1, header=None, names=IMU_COLS)
    return df


def load_mag(session_dir: str) -> pd.DataFrame | None:
    f = os.path.join(session_dir, "Magnetometer-Data.data")
    if not os.path.exists(f):
        return None
    return pd.read_csv(f, skiprows=1, header=None, names=["t", "mx", "my", "mz"])


def load_labels(session_dir: str) -> pd.DataFrame:
    f = os.path.join(session_dir, "Live-Labeling.label")
    if not os.path.exists(f):
        return pd.DataFrame(columns=["start", "length", "label", "conf", "comment"])
    df = pd.read_csv(f)
    df.columns = ["start", "length", "label", "conf", "comment"][: len(df.columns)]
    df = df.dropna(subset=["start"])
    df["end"] = df["start"] + df["length"]
    return df


def load_audio(session_dir: str):
    """Return (samples float32 in [-1,1], samplerate) or (None, None)."""
    f = os.path.join(session_dir, "Microphone-Data.wav")
    if not os.path.exists(f):
        return None, None
    w = wave.open(f, "rb")
    sr = w.getframerate()
    n = w.getnframes()
    raw = w.readframes(n)
    w.close()
    sw = 2  # 16-bit assumed
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if w.getnchannels() > 1:
        x = x.reshape(-1, w.getnchannels()).mean(axis=1)
    return x, sr


def imu_fs(df: pd.DataFrame) -> float:
    dt = np.diff(df["t"].values)
    return float(1.0 / np.median(dt))


def label_mask(t: np.ndarray, labels: pd.DataFrame, names=("fault",)) -> np.ndarray:
    """Boolean mask over time vector t for samples falling inside any matching label."""
    m = np.zeros(len(t), dtype=bool)
    for _, r in labels.iterrows():
        if names is None or str(r["label"]) in names:
            m |= (t >= r["start"]) & (t < r["end"])
    return m


def session_name(session_dir: str) -> str:
    return os.path.basename(session_dir)
