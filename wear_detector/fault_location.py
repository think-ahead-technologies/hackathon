# ABOUTME: Locate faults by camera view — frames at the same spot on the line look alike, cluster them.
# ABOUTME: Without an encoder, the onboard camera is the position sensor; co-viewed faults share a location.
import numpy as np


def cluster_by_similarity(feature_vectors, threshold=0.5):
    """Greedy single-link clustering of row feature vectors by normalized correlation.

    Returns an integer location label per row (0,1,...), assigned in input order: a row joins
    the first earlier row it correlates with above `threshold`, else starts a new location.
    Pure numpy — the camera/PIL part lives in image_feature(), so this stays CI-testable.
    """
    X = np.asarray(feature_vectors, dtype=np.float64)
    n = len(X)
    if n == 0:
        return []
    Xn = (X - X.mean(axis=1, keepdims=True)) / (X.std(axis=1, keepdims=True) + 1e-9)
    corr = (Xn @ Xn.T) / X.shape[1]
    labels = [-1] * n
    nxt = 0
    for i in range(n):
        if labels[i] < 0:
            labels[i] = nxt
            nxt += 1
        for j in range(i + 1, n):
            if labels[j] < 0 and corr[i, j] > threshold:
                labels[j] = labels[i]
    return labels


def image_feature(path, size=32):
    """Lighting-normalized, downscaled grayscale vector for one frame (needs Pillow)."""
    from PIL import Image
    im = Image.open(path).convert("L").resize((size, size))
    a = np.asarray(im, dtype=np.float64).ravel()
    return (a - a.mean()) / (a.std() + 1e-6)


def locate(image_paths, threshold=0.5, size=32):
    """Location label per image path (same label == same physical spot on the line)."""
    feats = [image_feature(p, size) for p in image_paths]
    return cluster_by_similarity(feats, threshold)
