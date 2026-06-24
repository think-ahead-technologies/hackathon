# ABOUTME: Per-unit baseline wear detector — fit on healthy data only, one baseline per device.
# ABOUTME: Default "directed" score (wear only adds energy); "mahalanobis" mode kept for comparison.
import numpy as np


class PerUnitBaselineDetector:
    """Learns one device's healthy feature distribution and flags drift away from it.

    Fit on healthy windows only (no fault labels needed). Two scoring modes:

    - "directed" (default): robust per-feature baseline (median / MAD), then the
      score is the mean of the *positive* standardized deviations. Wear adds
      broadband energy and never removes it, so a one-sided score matches the
      physics — and it does not get whitened away the way Mahalanobis does when
      the discriminative direction is also the highest-variance healthy direction.
    - "mahalanobis": classic distance from the healthy mean. Kept because it is
      the textbook choice, but at low fs it underperforms here (the energy signal
      lives in the high-variance direction that Sigma^-1 suppresses).

    Either way the raw score is mapped to 0..1 through the empirical CDF of healthy
    scores, so 0.95 means "more anomalous than 95% of this unit's healthy windows".
    """

    def __init__(self, feature_names, method="directed", shrinkage=0.15, ridge=1e-6,
                 threshold_pct=99.0):
        self.feature_names = list(feature_names)
        self.method = method
        self.shrinkage = shrinkage
        self.ridge = ridge
        self.threshold_pct = threshold_pct
        self.center_ = None
        self.scale_ = None
        self.cov_inv_ = None
        self.healthy_score_ = None  # sorted healthy raw scores
        self.threshold_ = None

    def _matrix(self, feature_dicts):
        return np.array([[fd[k] for k in self.feature_names] for fd in feature_dicts],
                        dtype=np.float64)

    def fit(self, healthy_feature_dicts):
        X = self._matrix(healthy_feature_dicts)
        if self.method == "directed":
            self.center_ = np.median(X, axis=0)
            mad = np.median(np.abs(X - self.center_), axis=0) * 1.4826
            mad[mad < 1e-9] = X.std(axis=0)[mad < 1e-9]
            mad[mad < 1e-9] = 1.0
            self.scale_ = mad
        else:  # mahalanobis
            self.center_ = X.mean(axis=0)
            self.scale_ = X.std(axis=0)
            self.scale_[self.scale_ < 1e-9] = 1.0
            Z = (X - self.center_) / self.scale_
            cov = np.atleast_2d(np.cov(Z, rowvar=False))
            cov = (1.0 - self.shrinkage) * cov + self.shrinkage * np.diag(np.diag(cov))
            cov += self.ridge * np.eye(cov.shape[0])
            self.cov_inv_ = np.linalg.inv(cov)

        d = self._raw(X)
        self.healthy_score_ = np.sort(d)
        self.threshold_ = float(np.percentile(d, self.threshold_pct))
        return self

    def _raw(self, X):
        Z = (X - self.center_) / self.scale_
        if self.method == "directed":
            return np.mean(np.clip(Z, 0.0, None), axis=1)  # mean positive deviation
        left = Z @ self.cov_inv_
        return np.sqrt(np.clip(np.einsum("ij,ij->i", left, Z), 0.0, None))

    def raw_scores(self, feature_dicts):
        return self._raw(self._matrix(feature_dicts))

    def score(self, feature_dicts):
        """Normalized anomaly scores in 0..1 via the healthy empirical CDF."""
        d = self.raw_scores(feature_dicts)
        ranks = np.searchsorted(self.healthy_score_, d, side="right")
        return ranks / len(self.healthy_score_)

    def predict(self, feature_dicts):
        """Boolean anomaly flags at the configured healthy-percentile threshold."""
        return self.raw_scores(feature_dicts) >= self.threshold_
