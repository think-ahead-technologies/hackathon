# ABOUTME: Tiny dependency-light scoring helpers (ROC-AUC, operating point) for the export eval.
# ABOUTME: Window-level metrics; the honest held-out numbers reported against the README ceiling.
import numpy as np


def auc(scores, labels):
    """ROC-AUC of `scores` against binary `labels` (1=fault). Rank-based (Mann-Whitney)."""
    scores = np.asarray(scores, dtype=np.float64)
    labels = np.asarray(labels).astype(bool)
    pos, neg = scores[labels], scores[~labels]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(len(scores), dtype=np.float64)
    ranks[order] = np.arange(1, len(scores) + 1)
    # Average ranks within ties so the statistic is exact for duplicated scores.
    s = scores[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    return float((ranks[labels].sum() - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg)))


def operating_point(scores, labels, fpr_target):
    """Lowest threshold whose false-positive rate <= fpr_target; return its (thr, fpr, fnr, tpr)."""
    scores = np.asarray(scores, dtype=np.float64)
    labels = np.asarray(labels).astype(bool)
    neg = scores[~labels]
    thr = float(np.quantile(neg, 1.0 - fpr_target))
    pred = scores >= thr
    fpr = float((pred & ~labels).sum() / max(1, (~labels).sum()))
    tpr = float((pred & labels).sum() / max(1, labels.sum()))
    return {"threshold": thr, "fpr": fpr, "fnr": 1.0 - tpr, "tpr": tpr}
