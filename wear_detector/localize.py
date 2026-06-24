# ABOUTME: Phase 4 localization for a variable-route figure-8 — signed-gyro turns, crossover-landmark laps.
# ABOUTME: Per-route-variant track-health map + track-vs-onboard discriminator (spec §6), no track specs.
import numpy as np
from scipy.ndimage import uniform_filter1d

# The track is a figure-8 and the cart picks different routes (inner / outer / full),
# so lap DURATION is multi-modal and a fixed lap period smears. Signed yaw distinguishes
# the two lobes; the minority-direction turn is the figure-8 crossover = a once-per-lap
# landmark to re-anchor on. Laps are segmented between crossovers (any length), position
# is landmark-relative phase (0..1 within each lap), and laps are grouped by route variant
# (duration cluster) so only comparable routes are pooled.


def yaw_envelope(gyro, fs, smooth_s=2.0):
    """Smoothed absolute yaw rate — turn-energy envelope."""
    return uniform_filter1d(np.abs(gyro[:, 2]), max(1, int(smooth_s * fs)))


def detect_turns(t, gyro, fs, min_sep_s=8.0, k=3.0):
    """Signed turn events: list of (time, direction in {+1,-1}). Sign = lobe / turn direction."""
    yaw = gyro[:, 2]
    env = yaw_envelope(gyro, fs)
    med = np.median(env)
    mad = np.median(np.abs(env - med)) * 1.4826 + 1e-9
    thr = med + k * mad
    min_sep = int(min_sep_s * fs)
    win = int(fs)
    turns, last = [], -min_sep
    for i in range(1, len(env) - 1):
        if env[i] > thr and env[i] >= env[i - 1] and env[i] > env[i + 1] and i - last >= min_sep:
            direction = 1 if yaw[max(0, i - win):i + win].mean() > 0 else -1
            turns.append((float(t[i]), direction))
            last = i
    return turns


def crossover_landmarks(turns):
    """Times of the minority-direction turns — the figure-8 crossover, once per lap."""
    if not turns:
        return []
    signs = [d for _, d in turns]
    minority = 1 if signs.count(1) <= signs.count(-1) else -1
    return [tm for tm, d in turns if d == minority]


def segment_laps(landmarks):
    """Full laps as (start, end) between consecutive landmarks; partial head/tail dropped."""
    return [(landmarks[i], landmarks[i + 1]) for i in range(len(landmarks) - 1)]


def cluster_route_variants(laps, tol=0.18):
    """Group laps by duration into route variants. Returns variant id per lap (0,1,...).

    1D agglomerative-ish clustering: sort durations, start a new cluster when the gap to
    the running centroid exceeds tol (fractional). Handles inner/outer/full figure-8.
    """
    if not laps:
        return []
    durs = np.array([e - s for s, e in laps])
    order = np.argsort(durs)
    variant = np.zeros(len(durs), dtype=int)
    cur, centroid, members = 0, durs[order[0]], [durs[order[0]]]
    for idx in order[1:]:
        d = durs[idx]
        if d > centroid * (1 + tol):
            cur += 1
            members = [d]
        else:
            members.append(d)
        centroid = np.mean(members)
        variant[idx] = cur
    return variant.tolist()


def landmark_phase(t, lap_start, lap_end):
    """Position within a lap, 0..1 between crossover landmarks (speed/length invariant)."""
    span = lap_end - lap_start
    return ((t - lap_start) / span) if span > 0 else 0.0


class TrackHealthMap:
    """Position-indexed accumulator for ONE route variant: anomaly score binned by lap phase.

    A track-localized fault sits at the same bin every lap (high spatial contrast); a fault
    that rides the unit is uniform across bins (contrast ~1). Noise averages out over laps.
    """

    def __init__(self, n_bins=24):
        self.n_bins = n_bins
        self._sum = np.zeros(n_bins)
        self._cnt = np.zeros(n_bins)

    def add(self, phase, value):
        b = int(phase * self.n_bins) % self.n_bins
        self._sum[b] += value
        self._cnt[b] += 1
        return b

    def bin_means(self):
        out = np.full(self.n_bins, np.nan)
        nz = self._cnt > 0
        out[nz] = self._sum[nz] / self._cnt[nz]
        return out

    def coverage(self):
        return float(np.mean(self._cnt > 0))

    def spatial_contrast(self):
        """peak_bin / median_bin (spec §6). High -> track-localized; ~1 -> onboard."""
        m = self.bin_means()
        m = m[~np.isnan(m)]
        if len(m) == 0:
            return float("nan")
        med = np.median(m)
        return float(np.max(m) / med) if med > 1e-12 else float("nan")

    def peak_phase(self):
        """Lap phase (0..1) of the most anomalous bin — the 'where' in an alert."""
        m = self.bin_means()
        if np.all(np.isnan(m)):
            return None
        return (np.nanargmax(m) + 0.5) / self.n_bins

    def classify_locus(self, contrast_thr=2.0, min_coverage=0.6):
        if self.coverage() < min_coverage:
            return "unknown"
        c = self.spatial_contrast()
        if np.isnan(c):
            return "unknown"
        return "track" if c >= contrast_thr else "onboard"
