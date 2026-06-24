# ABOUTME: Motion gate — only assess wear while the unit is actually moving (suppress parked/handled).
# ABOUTME: Human handling and station dwells show near-zero gyro energy; in-transit operation does not.
import numpy as np

# Why gyro (not accel): on the conveyor the carrier rotates through the line under power, so
# transit windows carry tens of dps of yaw while a parked or hand-held unit sits near zero —
# a cleaner running/not-running split than translational accel, which barely separates the two.


def window_gyro_rms(t_wall, gyro, centres, win_s=1.0):
    """Per-centre RMS gyro magnitude (dps) over [c - win/2, c + win/2); NaN if <3 samples.

    t_wall is the IMU wall clock (load_merged_csv), the same origin as the audio window
    centres, so an acoustic window can be asked "was the unit moving then?".
    """
    t_wall = np.asarray(t_wall)
    gyro = np.asarray(gyro)
    out = np.full(len(centres), np.nan)
    for i, c in enumerate(centres):
        m = (t_wall >= c - win_s / 2) & (t_wall < c + win_s / 2)
        if int(m.sum()) >= 3:
            out[i] = float(np.sqrt((gyro[m] ** 2).sum(axis=1).mean()))
    return out


def suggest_threshold(energies, pct=50.0):
    """Data-driven motion threshold: the pct-th percentile of windowed gyro RMS.

    The energy distribution is bimodal (dwell vs transit); a percentile between the modes
    suppresses the quietest pct% of windows without hard-coding a dps value per rig.
    """
    e = np.asarray(energies, dtype=float)
    e = e[~np.isnan(e)]
    if e.size == 0:
        raise ValueError("no covered windows to derive a motion threshold")
    return float(np.percentile(e, pct))


def in_motion(t_wall, gyro, centres, win_s=1.0, pct=50.0, threshold=None,
              keep_missing=True):
    """Boolean 'unit is moving' mask per window, plus the energies and threshold used.

    Windows with no IMU coverage (NaN energy) take keep_missing — default True, so an IMU
    dropout never silently suppresses a real anomaly (absence of data is not evidence of rest).
    """
    energies = window_gyro_rms(t_wall, gyro, centres, win_s)
    thr = suggest_threshold(energies, pct) if threshold is None else float(threshold)
    mask = energies >= thr
    mask[np.isnan(energies)] = keep_missing
    return mask, energies, thr


def gate_events(t_wall, gyro, event_times, reference_centres, win_s=1.0, pct=50.0,
                threshold=None, keep_missing=True):
    """Keep-mask for anomaly EVENTS, judged at each event's (peak) time.

    An event is the loudest instant of an anomaly cluster, so gate it on whether the unit
    was moving *then* — a fault heard while parked/handled (even one mid-pickup that briefly
    rotates) gets dropped. The threshold is derived from the full window distribution
    (reference_centres), not the few event times, so it stays bimodal-aware. Returns the
    keep mask, per-event energy, the threshold, and the suppressed count.
    """
    ref = window_gyro_rms(t_wall, gyro, reference_centres, win_s)
    thr = suggest_threshold(ref, pct) if threshold is None else float(threshold)
    energy = window_gyro_rms(t_wall, gyro, np.asarray(event_times), win_s)
    keep = energy >= thr
    keep[np.isnan(energy)] = keep_missing
    return {
        "keep": keep,
        "energy": energy,
        "threshold": thr,
        "suppressed": int((~keep).sum()),
    }
