# ABOUTME: Test the clip-grid window tiling — full coverage, clip_s steps, short final clip.
# ABOUTME: The HTML/file emission needs a recording; the tiling math is the pure piece.
from wear_detector import clip_grid


def test_clip_windows_tile_full_duration():
    w = clip_grid.clip_windows(12.0, 5.0)
    assert [(i, round(a, 1), round(b, 1)) for i, a, b in w] == [
        (1, 0.0, 5.0), (2, 5.0, 10.0), (3, 10.0, 12.0)]   # last clip clamped


def test_clip_windows_exact_multiple():
    w = clip_grid.clip_windows(10.0, 5.0)
    assert len(w) == 2 and w[-1][2] == 10.0


def test_clip_windows_indices_are_1_based_and_contiguous():
    w = clip_grid.clip_windows(7.3, 2.0)
    assert [i for i, _, _ in w] == [1, 2, 3, 4]
    assert all(abs(w[k][2] - w[k + 1][1]) < 1e-9 for k in range(len(w) - 1))
