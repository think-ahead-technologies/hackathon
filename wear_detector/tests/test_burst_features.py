# ABOUTME: Tests for the per-burst spectral extractor — split bursts, Welch-average, no gap smear.
# ABOUTME: Proves a 600 Hz tone (intra-burst, true 1600 Hz) is resolved despite ~10% duty cycle.
import numpy as np

from wear_detector import burst_features


def _bursty(fs=1600.0, n_segments=4, seg_len=800, gap_s=2.0, tone_hz=None, amp=0.3):
    """Synthesize the merged-recorder shape: a few contiguous 1600 Hz capture segments (the
    device clock steps 625 us and resets each segment) with real time gaps between them."""
    acc = np.zeros((0, 3))
    t_dev = []
    k = 0
    for _ in range(n_segments):
        idx = np.arange(seg_len)
        seg = np.zeros((seg_len, 3))
        seg[:, 2] = 1.0  # gravity on z
        if tone_hz is not None:
            # Tone on the gravity axis so the vector magnitude carries it linearly (a tone on
            # an off-axis would be squared by |a| and frequency-doubled). Phase continues as
            # if wall time advanced across the gap (realistic, not required).
            t = (k + idx) / fs
            seg[:, 2] += amp * np.sin(2 * np.pi * tone_hz * t)
        acc = np.vstack([acc, seg])
        t_dev.extend((idx * int(1e6 / fs)).tolist())  # 0,625,... resets each segment
        k += seg_len + int(gap_s * fs)
    return acc, np.asarray(t_dev, dtype=np.float64)


def test_split_bursts_finds_contiguous_segments():
    _acc, t_dev = _bursty(n_segments=5, seg_len=800)
    bursts = burst_features.split_bursts(t_dev)
    assert len(bursts) == 5
    assert all(e - s == 800 for s, e in bursts)


def test_split_bursts_drops_short_edge_fragments():
    # a clean 800-sample segment then a 3-sample fragment
    t_dev = np.array(list(np.arange(800) * 625) + [0, 625, 1250], dtype=float)
    bursts = burst_features.split_bursts(t_dev, min_len=8)
    assert all(e - s >= 8 for s, e in bursts)
    assert any(e - s == 800 for s, e in bursts)


def test_welch_psd_locates_a_600hz_tone():
    fs = 1600.0
    acc, t_dev = _bursty(fs=fs, n_segments=4, seg_len=800, tone_hz=600.0)
    sig = acc[:, 2] - acc[:, 2].mean()
    bursts = burst_features.split_bursts(t_dev)
    freqs, psd = burst_features.welch_psd(sig, bursts, fs, n_fft=256)
    assert freqs[-1] >= 700.0                       # grid reaches the 800 Hz Nyquist
    peak_hz = freqs[int(np.argmax(psd))]
    assert abs(peak_hz - 600.0) < 20.0             # sharp: contiguous segments, real resolution


def test_burst_extractor_resolves_high_band_tone():
    # 600 Hz lives well above what a naive uniform reading of the slow host clock could
    # represent; the per-segment extractor (true 1600 Hz) must place the energy up high.
    fs = 1600.0
    acc, t_dev = _bursty(fs=fs, n_segments=4, seg_len=800, tone_hz=600.0)
    feats = burst_features.burst_spectral_features(acc, t_dev, fs)
    assert feats["centroid"] > 400.0                # energy genuinely high-band
    bands = [feats[f"band{b}"] for b in range(8)]   # 8 bands over [0,800]; 600 Hz -> band 5/6
    assert int(np.argmax(bands)) in (5, 6)


def test_burst_spectral_features_match_feature_names():
    acc, t_dev = _bursty(n_segments=4, seg_len=800, tone_hz=400.0)
    feats = burst_features.burst_spectral_features(acc, t_dev, 1600.0)
    # same keys the contiguous spectral path emits (so it is a drop-in for those features)
    for k in ("band0", "band7", "hf_ratio", "centroid", "spread",
              "flatness", "entropy", "rolloff"):
        assert k in feats
