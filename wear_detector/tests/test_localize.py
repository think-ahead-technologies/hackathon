# ABOUTME: Unit tests for figure-8 localization — signed turns, crossover laps, variants, contrast.
# ABOUTME: Synthetic gyro with known turns/laps validates detection and the track/onboard discriminator.
import numpy as np

from wear_detector import localize


def _synth_figure8(fs=50.0, laps=6, lap_s=48.0, signs=(1, -1, 1, 1)):
    """Build gyro with `laps` laps, each a fixed signed-turn signature.

    Turns are spaced lap_s/len(signs) apart (= 12 s here, above the 8 s refractory) with
    straights (zero yaw) between, so the envelope has clean, separable peaks.
    """
    n = int(laps * lap_s * fs)
    t = np.arange(n) / fs
    gyro = np.zeros((n, 3))
    turn_per_lap = len(signs)
    for L in range(laps):
        for k, s in enumerate(signs):
            center = (L * lap_s) + (k + 0.5) * (lap_s / turn_per_lap)
            i = int(center * fs)
            half = int(0.5 * fs)
            gyro[max(0, i - half):i + half, 2] = s * 60.0  # turn burst on yaw
    return t, gyro, fs


def test_detect_signed_turns_count_and_direction():
    t, gyro, fs = _synth_figure8(laps=5)
    turns = localize.detect_turns(t, gyro, fs)
    assert len(turns) == 5 * 4  # 4 turns/lap
    assert {d for _, d in turns} == {1, -1}


def test_crossover_landmarks_once_per_lap():
    t, gyro, fs = _synth_figure8(laps=6, signs=(1, -1, 1, 1))
    landmarks = localize.crossover_landmarks(localize.detect_turns(t, gyro, fs))
    assert len(landmarks) == 6  # one minority (-1) turn per lap


def test_segment_and_variant_clusters_by_duration():
    # two short laps then two long laps -> two variants
    laps = [(0, 20), (20, 40), (40, 80), (80, 120)]
    variants = localize.cluster_route_variants(laps)
    assert len(set(variants)) == 2
    assert variants[0] == variants[1] and variants[2] == variants[3]
    assert variants[0] != variants[2]


def test_landmark_phase_is_length_invariant():
    # halfway through a short lap and a long lap both -> 0.5
    assert abs(localize.landmark_phase(10, 0, 20) - 0.5) < 1e-9
    assert abs(localize.landmark_phase(40, 0, 80) - 0.5) < 1e-9


def test_localized_anomaly_is_high_contrast_track():
    m = localize.TrackHealthMap(n_bins=20)
    rng = np.random.default_rng(0)
    for _ in range(2000):
        ph = rng.random()
        val = 5.0 if 0.30 <= ph < 0.35 else 0.1  # spike at one position every lap
        m.add(ph, val)
    assert m.spatial_contrast() > 3.0
    assert m.classify_locus() == "track"
    assert abs(m.peak_phase() - 0.325) < 0.06


def test_uniform_anomaly_is_onboard():
    m = localize.TrackHealthMap(n_bins=20)
    rng = np.random.default_rng(1)
    for _ in range(2000):
        m.add(rng.random(), 1.0 + rng.normal(0, 0.05))  # same everywhere = rides the unit
    assert m.spatial_contrast() < 1.5
    assert m.classify_locus() == "onboard"
