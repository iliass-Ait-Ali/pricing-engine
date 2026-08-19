"""Descriptive and regression-based price-response estimation.

Nothing in this module is causal. Every estimate here is an **observational
price-response association**: prices in the Dominick's data were set by the
retailer, not randomised, so P(Q | price) is not P(Q | do(price)). See
docs/CAUSAL_LIMITATIONS.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

MIN_REL_PRICE_CHANGE = 1e-4


@dataclass
class ElasticityFit:
    """Result of a log-log demand regression."""

    name: str
    price_elasticity: float
    std_error: float
    ci_low: float
    ci_high: float
    n_obs: int
    r_squared: float
    controls: str
    zero_handling: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.name,
            "price_elasticity": self.price_elasticity,
            "std_error": self.std_error,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "n_obs": self.n_obs,
            "r_squared": self.r_squared,
            "controls": self.controls,
            "zero_handling": self.zero_handling,
        }


# ---------------------------------------------------------------------------
# descriptive: midpoint / arc elasticity
# ---------------------------------------------------------------------------
def arc_elasticity(
    q1, q2, p1, p2, *, min_rel_change: float = MIN_REL_PRICE_CHANGE
) -> np.ndarray:
    """Midpoint (arc) elasticity between two observations.

        E = ((q2 - q1) / ((q1 + q2) / 2)) / ((p2 - p1) / ((p1 + p2) / 2))

    Returns NaN - never a fabricated number - when the calculation is
    undefined: equal or near-equal prices, zero midpoints, or invalid
    quantities.
    """
    q1 = np.asarray(q1, dtype="float64")
    q2 = np.asarray(q2, dtype="float64")
    p1 = np.asarray(p1, dtype="float64")
    p2 = np.asarray(p2, dtype="float64")

    q_mid = (q1 + q2) / 2.0
    p_mid = (p1 + p2) / 2.0

    with np.errstate(divide="ignore", invalid="ignore"):
        rel_p = np.where(p_mid > 0, (p2 - p1) / p_mid, np.nan)
        rel_q = np.where(q_mid > 0, (q2 - q1) / q_mid, np.nan)
        elasticity = rel_q / rel_p

    invalid = (
        ~np.isfinite(elasticity)
        | (np.abs(rel_p) < min_rel_change)
        | (q_mid <= 0)
        | (p_mid <= 0)
        | (q1 < 0)
        | (q2 < 0)
    )
    return np.where(invalid, np.nan, elasticity)


def pairwise_arc_elasticities(
    df: pd.DataFrame,
    group_cols: list[str],
    *,
    price_col: str = "effective_unit_price",
    qty_col: str = "move",
    week_col: str = "week",
    min_rel_change: float = MIN_REL_PRICE_CHANGE,
) -> pd.DataFrame:
    """Week-over-week arc elasticities within each series.

    Consecutive weeks only (gaps are skipped), because a 30-week jump is not a
    price experiment.
    """
    d = df.sort_values(group_cols + [week_col]).copy()
    g = d.groupby(group_cols, observed=True)
    d["prev_price"] = g[price_col].shift(1)
    d["prev_move"] = g[qty_col].shift(1)
    d["prev_week"] = g[week_col].shift(1)

    consecutive = (d[week_col] - d["prev_week"]) == 1
    d = d[consecutive & d["prev_price"].notna()].copy()
    d["arc_elasticity"] = arc_elasticity(
        d["prev_move"], d[qty_col], d["prev_price"], d[price_col], min_rel_change=min_rel_change
    )
    d["price_change_pct"] = d[price_col] / d["prev_price"] - 1.0
    return d


# ---------------------------------------------------------------------------
# regression: log-log demand
# ---------------------------------------------------------------------------
def prepare_loglog_frame(
    df: pd.DataFrame,
    *,
    price_col: str = "effective_unit_price",
    qty_col: str = "move",
    zero_handling: str = "drop",
) -> tuple[pd.DataFrame, str]:
    """Return a frame with log price / log quantity, handling zero sales.

    ``zero_handling``:
      * ``drop``  - drop zero-sales weeks (default; the coefficient is then
        conditional on positive sales and this is stated in the report)
      * ``plus1`` - use log(1 + q), which changes the interpretation of the
        coefficient and is documented as such
    """
    d = df.copy()
    if zero_handling == "drop":
        n_before = len(d)
        d = d[d[qty_col] > 0]
        note = f"dropped {n_before - len(d):,} zero-sales rows ({100.0 * (n_before - len(d)) / max(n_before, 1):.2f}%)"
        d["log_q"] = np.log(d[qty_col])
    elif zero_handling == "plus1":
        note = "log(1 + units) transform applied to keep zero-sales weeks"
        d["log_q"] = np.log1p(d[qty_col])
    else:
        raise ValueError(f"unknown zero_handling: {zero_handling}")

    d = d[d[price_col] > 0]
    d["log_p"] = np.log(d[price_col])
    return d, note


def fit_loglog(
    df: pd.DataFrame,
    *,
    name: str,
    controls: list[str] | None = None,
    absorb: list[str] | None = None,
    zero_handling: str = "drop",
    cluster_col: str | None = None,
    sample_size: int | None = None,
    seed: int = 42,
) -> ElasticityFit:
    """Fit log(units) ~ log(price) [+ controls] [+ absorbed fixed effects].

    ``absorb`` applies the within transformation (demeaning by group) instead
    of building thousands of dummy columns, so UPC x store fixed effects stay
    tractable on a 4.7 M row panel.
    """
    import statsmodels.api as sm

    d, note = prepare_loglog_frame(df, zero_handling=zero_handling)
    if sample_size and len(d) > sample_size:
        d = d.sample(sample_size, random_state=seed)
        note += f"; fitted on a random subsample of {sample_size:,} rows (seed={seed})"

    y = d["log_q"].to_numpy(dtype="float64")
    x_cols = ["log_p"] + list(controls or [])
    X = d[x_cols].astype("float64").to_numpy()

    if absorb:
        key = d[absorb[0]].astype(str)
        for col in absorb[1:]:
            key = key + "_" + d[col].astype(str)
        codes = pd.factorize(key)[0]
        y = _demean(y.reshape(-1, 1), codes).ravel()
        X = _demean(X, codes)

    X = sm.add_constant(X, has_constant="add")
    model = sm.OLS(y, X, missing="drop")
    if cluster_col is not None and cluster_col in d.columns:
        groups = pd.factorize(d[cluster_col])[0]
        res = model.fit(cov_type="cluster", cov_kwds={"groups": groups})
    else:
        res = model.fit(cov_type="HC1")

    beta = float(res.params[1])
    se = float(res.bse[1])
    ci = res.conf_int()
    return ElasticityFit(
        name=name,
        price_elasticity=beta,
        std_error=se,
        ci_low=float(ci[1][0]),
        ci_high=float(ci[1][1]),
        n_obs=int(res.nobs),
        r_squared=float(res.rsquared),
        controls=", ".join(x_cols[1:] + [f"FE:{c}" for c in (absorb or [])]) or "none",
        zero_handling=f"{zero_handling} ({note})",
    )


def _demean(X: np.ndarray, codes: np.ndarray) -> np.ndarray:
    """Subtract group means column-wise (within transformation)."""
    out = np.empty_like(X, dtype="float64")
    n_groups = codes.max() + 1
    counts = np.bincount(codes, minlength=n_groups).astype("float64")
    for j in range(X.shape[1]):
        sums = np.bincount(codes, weights=X[:, j], minlength=n_groups)
        means = sums / np.maximum(counts, 1.0)
        out[:, j] = X[:, j] - means[codes]
    return out


def elasticity_by_group(
    df: pd.DataFrame,
    group_cols: list[str],
    *,
    min_obs: int = 40,
    min_distinct_prices: int = 5,
    zero_handling: str = "drop",
) -> pd.DataFrame:
    """Per-series log-log elasticity, for stability analysis across products."""
    import statsmodels.api as sm

    rows: list[dict[str, Any]] = []
    d, _ = prepare_loglog_frame(df, zero_handling=zero_handling)
    for keys, sub in d.groupby(group_cols, observed=True):
        if len(sub) < min_obs or sub["effective_unit_price"].nunique() < min_distinct_prices:
            continue
        X = sm.add_constant(sub[["log_p"]].to_numpy(dtype="float64"), has_constant="add")
        res = sm.OLS(sub["log_q"].to_numpy(dtype="float64"), X).fit(cov_type="HC1")
        key_tuple = keys if isinstance(keys, tuple) else (keys,)
        row = dict(zip(group_cols, key_tuple, strict=True))
        row.update(
            {
                "n_obs": int(len(sub)),
                "elasticity": float(res.params[1]),
                "std_error": float(res.bse[1]),
                "t_stat": float(res.tvalues[1]),
                "r_squared": float(res.rsquared),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)
