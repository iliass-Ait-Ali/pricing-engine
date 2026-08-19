"""Covariance estimators for panel elasticity regressions (Phase M).

The Dominick's data is a panel: the same UPC is observed in ~90 stores for ~250
weeks. Three dependence structures are obviously present and all three violate
the independence assumption behind conventional and heteroskedasticity-robust
standard errors:

1. **within store** - a store's demand shocks (footfall, local competition,
   staffing) persist across weeks;
2. **within UPC** - a product's demand shocks (national advertising, a
   competitor's launch) hit every store at once;
3. **within week** - category-wide shocks (holidays, weather, a chain-wide
   promotion calendar) hit every store and product at once.

This module implements the covariance estimators needed to say how much the
reported precision changes once that structure is admitted:

* :func:`ols_fit` - plain OLS on a design matrix, returning what every
  estimator below needs (coefficients, residuals, ``(X'X)^-1``).
* :func:`classical_cov` - homoskedastic ``s^2 (X'X)^-1``.
* :func:`hc1_cov` - White / HC1 heteroskedasticity-robust.
* :func:`cluster_cov` - one-way cluster-robust (Liang-Zeger), with the usual
  finite-sample correction ``G/(G-1) * (n-1)/(n-k)``.
* :func:`two_way_cluster_cov` - Cameron-Gelbach-Miller
  ``V_a + V_b - V_ab``, projected back to the nearest positive semi-definite
  matrix when the subtraction makes it indefinite (which happens, and silently
  reporting a negative variance would be worse).

Absorbed fixed effects
----------------------
When fixed effects are absorbed by the within transformation rather than
entered as dummies, the residual degrees of freedom must still be charged for
them, otherwise every standard error is too small. ``n_absorbed`` does that:
the variance is scaled by ``(n - k) / (n - k - n_absorbed)``.

Nothing here is causal. A wider interval on an observational elasticity is a
more honest observational elasticity, not an identified one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

SQRT_EPS = 1e-12


class InferenceError(ValueError):
    """Raised when a covariance estimator cannot be computed."""


@dataclass
class OLSFit:
    """Everything the covariance estimators need from one OLS fit."""

    beta: np.ndarray
    resid: np.ndarray
    xtx_inv: np.ndarray
    X: np.ndarray
    n: int
    k: int
    n_absorbed: int = 0
    r_squared: float = float("nan")

    @property
    def dof_scale(self) -> float:
        """Extra variance inflation for degrees of freedom used by absorbed FE."""
        denom = self.n - self.k - self.n_absorbed
        if denom <= 0:
            raise InferenceError(
                f"no residual degrees of freedom: n={self.n}, k={self.k}, "
                f"absorbed={self.n_absorbed}"
            )
        return (self.n - self.k) / denom


def ols_fit(X: np.ndarray, y: np.ndarray, *, n_absorbed: int = 0) -> OLSFit:
    """Plain OLS. ``X`` must already contain a constant column if one is wanted."""
    X = np.asarray(X, dtype="float64")
    y = np.asarray(y, dtype="float64")
    if X.ndim != 2 or len(X) != len(y):
        raise InferenceError(f"incompatible shapes X={X.shape}, y={y.shape}")
    xtx = X.T @ X
    try:
        xtx_inv = np.linalg.inv(xtx)
    except np.linalg.LinAlgError as exc:
        raise InferenceError("design matrix is singular") from exc
    beta = xtx_inv @ (X.T @ y)
    resid = y - X @ beta
    tss = float(np.sum((y - y.mean()) ** 2))
    rss = float(np.sum(resid**2))
    return OLSFit(
        beta=beta,
        resid=resid,
        xtx_inv=xtx_inv,
        X=X,
        n=len(y),
        k=X.shape[1],
        n_absorbed=int(n_absorbed),
        r_squared=1.0 - rss / tss if tss > 0 else float("nan"),
    )


def classical_cov(fit: OLSFit) -> np.ndarray:
    """Homoskedastic covariance ``s^2 (X'X)^-1``."""
    dof = fit.n - fit.k - fit.n_absorbed
    if dof <= 0:
        raise InferenceError("no residual degrees of freedom")
    s2 = float(fit.resid @ fit.resid) / dof
    return s2 * fit.xtx_inv


def hc1_cov(fit: OLSFit) -> np.ndarray:
    """White heteroskedasticity-robust covariance with the HC1 correction."""
    meat = (fit.X * (fit.resid**2)[:, None]).T @ fit.X
    scale = fit.n / max(fit.n - fit.k - fit.n_absorbed, 1)
    return scale * (fit.xtx_inv @ meat @ fit.xtx_inv)


def cluster_cov(fit: OLSFit, groups: np.ndarray) -> np.ndarray:
    """One-way cluster-robust (Liang-Zeger) covariance.

    ``groups`` is an integer code per observation. Correlation of any form is
    permitted *within* a cluster and none is permitted across clusters, so the
    cluster must be chosen at the level where the dependence actually lives.
    """
    codes = np.asarray(groups)
    if len(codes) != fit.n:
        raise InferenceError(f"{len(codes)} group labels for {fit.n} observations")
    codes = np.unique(codes, return_inverse=True)[1]
    n_groups = int(codes.max()) + 1
    if n_groups < 2:
        raise InferenceError("cluster-robust inference needs at least two clusters")

    scores = fit.X * fit.resid[:, None]
    sums = np.zeros((n_groups, fit.k), dtype="float64")
    np.add.at(sums, codes, scores)
    meat = sums.T @ sums
    correction = (n_groups / (n_groups - 1)) * (
        (fit.n - 1) / max(fit.n - fit.k - fit.n_absorbed, 1)
    )
    return correction * (fit.xtx_inv @ meat @ fit.xtx_inv)


def two_way_cluster_cov(fit: OLSFit, groups_a: np.ndarray, groups_b: np.ndarray) -> np.ndarray:
    """Cameron-Gelbach-Miller two-way cluster covariance ``V_a + V_b - V_ab``.

    The subtraction can produce an indefinite matrix in finite samples. When it
    does, the result is projected onto the nearest positive semi-definite matrix
    by zeroing the negative eigenvalues, and that is the standard remedy - not a
    quiet fudge, and it is reported alongside the estimate.
    """
    a = np.unique(np.asarray(groups_a), return_inverse=True)[1]
    b = np.unique(np.asarray(groups_b), return_inverse=True)[1]
    intersect = np.unique(np.stack([a, b], axis=1), axis=0, return_inverse=True)[1]
    v = cluster_cov(fit, a) + cluster_cov(fit, b) - cluster_cov(fit, intersect)
    vals, vecs = np.linalg.eigh((v + v.T) / 2.0)
    if np.any(vals < 0):
        vals = np.clip(vals, 0.0, None)
        v = vecs @ np.diag(vals) @ vecs.T
    return v


def std_errors(cov: np.ndarray) -> np.ndarray:
    """Standard errors from a covariance matrix (NaN where the variance is < 0)."""
    diag = np.diag(cov)
    return np.where(diag > SQRT_EPS, np.sqrt(np.clip(diag, 0.0, None)), np.nan)


def keep_independent_columns(X: np.ndarray, *, tol: float = 1e-10) -> np.ndarray:
    """Indices of the columns that are linearly independent, left to right.

    A control that is constant within the estimation sample - a promotion flag
    that is never set, say - becomes an exactly zero column after the within
    transformation and makes ``X'X`` singular. Dropping it is the honest fix: a
    constant regressor carries no information and estimating "its" coefficient
    by pseudo-inverse only hides the degeneracy. Column order is respected, so
    the constant and the price coefficient are kept first.
    """
    X = np.asarray(X, dtype="float64")
    kept: list[int] = []
    for j in range(X.shape[1]):
        trial = X[:, [*kept, j]]
        if np.linalg.matrix_rank(trial, tol=tol) == len(kept) + 1:
            kept.append(j)
    return np.asarray(kept, dtype=int)


def demean(X: np.ndarray, codes: np.ndarray) -> np.ndarray:
    """Within transformation: subtract group means column-wise."""
    X = np.asarray(X, dtype="float64")
    if X.ndim == 1:
        X = X.reshape(-1, 1)
        squeeze = True
    else:
        squeeze = False
    codes = np.unique(np.asarray(codes), return_inverse=True)[1]
    n_groups = int(codes.max()) + 1
    counts = np.bincount(codes, minlength=n_groups).astype("float64")
    out = np.empty_like(X)
    for j in range(X.shape[1]):
        sums = np.bincount(codes, weights=X[:, j], minlength=n_groups)
        out[:, j] = X[:, j] - (sums / np.maximum(counts, 1.0))[codes]
    return out.ravel() if squeeze else out


def elasticity_inference(
    X: np.ndarray,
    y: np.ndarray,
    *,
    cluster_specs: dict[str, np.ndarray] | None = None,
    two_way: tuple[str, str] | None = None,
    n_absorbed: int = 0,
    coefficient_index: int = 1,
    confidence: float = 0.95,
) -> dict[str, Any]:
    """Fit once and report the coefficient under every covariance assumption.

    Returns the coefficient plus, for each specification, its standard error,
    t statistic, confidence interval and the ratio of that standard error to the
    conventional one - which is the number that answers "is the reported
    precision overconfident?".
    """
    from scipy import stats as sps

    fit = ols_fit(X, y, n_absorbed=n_absorbed)
    beta = float(fit.beta[coefficient_index])
    dof = max(fit.n - fit.k - fit.n_absorbed, 1)
    crit = float(sps.t.ppf(0.5 + confidence / 2.0, dof))

    covs = {"classical": classical_cov(fit), "hc1 (heteroskedasticity-robust)": hc1_cov(fit)}
    for name, groups in (cluster_specs or {}).items():
        covs[f"clustered by {name}"] = cluster_cov(fit, groups)
    if two_way and cluster_specs and all(t in cluster_specs for t in two_way):
        covs[f"two-way ({two_way[0]}, {two_way[1]})"] = two_way_cluster_cov(
            fit, cluster_specs[two_way[0]], cluster_specs[two_way[1]]
        )

    base_se = float(std_errors(covs["classical"])[coefficient_index])
    out = {
        "coefficient": beta,
        "n_obs": int(fit.n),
        "k": int(fit.k),
        "n_absorbed": int(fit.n_absorbed),
        "r_squared": fit.r_squared,
        "specifications": {},
    }
    for name, cov in covs.items():
        se = float(std_errors(cov)[coefficient_index])
        out["specifications"][name] = {
            "std_error": se,
            "t_stat": beta / se if se and np.isfinite(se) else float("nan"),
            "ci_low": beta - crit * se,
            "ci_high": beta + crit * se,
            "ratio_to_classical": se / base_se if base_se else float("nan"),
            "n_clusters": int(len(np.unique(cluster_specs[name.removeprefix("clustered by ")])))
            if name.startswith("clustered by ") and cluster_specs
            else None,
        }
    return out
