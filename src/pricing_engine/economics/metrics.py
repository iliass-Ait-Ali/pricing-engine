"""Reusable pricing-economics primitives.

Every function is vectorised (numpy / pandas friendly) and pure, so the same
code path serves the EDA, the counterfactual simulator, the optimizer, the API
and the dashboard. This matters: revenue must mean exactly one thing project
wide.

Definitions
-----------
    revenue(p, q)                 = p * q
    unit_gross_margin(p, c)       = p - c
    gross_margin_rate(p, c)       = (p - c) / p
    gross_profit(p, q, c)         = (p - c) * q
    price_change_pct(p_new, p_old) = p_new / p_old - 1

``p`` is always an **effective unit price** and ``c`` an **estimated unit AAC**
(see docs/DATA_DICTIONARY.md).
"""

from __future__ import annotations

from typing import TypeVar

import numpy as np
import pandas as pd

ArrayLike = TypeVar("ArrayLike", float, np.ndarray, pd.Series)


class EconomicsError(ValueError):
    """Raised when an economic quantity is mathematically undefined."""


def _as_array(x):
    if isinstance(x, pd.Series):
        return x.to_numpy(dtype="float64")
    return np.asarray(x, dtype="float64")


def revenue(price, units):
    """Revenue = unit price x units sold."""
    return _as_array(price) * _as_array(units)


def unit_gross_margin(price, unit_cost):
    """Absolute gross margin per unit (price - cost)."""
    return _as_array(price) - _as_array(unit_cost)


def gross_margin_rate(price, unit_cost):
    """Relative gross margin (p - c) / p.

    Returns NaN where price is zero: the rate is undefined, and returning a
    silent 0 or inf would corrupt downstream constraint checks.
    """
    p = _as_array(price)
    c = _as_array(unit_cost)
    with np.errstate(divide="ignore", invalid="ignore"):
        rate = np.where(p != 0, (p - c) / p, np.nan)
    return rate


def gross_profit(price, units, unit_cost):
    """Gross profit = (price - cost) x units."""
    return unit_gross_margin(price, unit_cost) * _as_array(units)


def price_change_pct(new_price, old_price):
    """Relative price change; NaN when the reference price is zero."""
    new = _as_array(new_price)
    old = _as_array(old_price)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(old != 0, new / old - 1.0, np.nan)


def price_index(price, reference_price):
    """Price relative to a reference level (1.0 = at reference)."""
    p = _as_array(price)
    ref = _as_array(reference_price)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(ref != 0, p / ref, np.nan)


# ---------------------------------------------------------------------------
# series-level descriptive statistics
# ---------------------------------------------------------------------------
def price_variation_summary(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    """Summarise within-group price variation.

    This is the eligibility screen for elasticity / optimization work: a series
    whose price never moves carries no information about price response, no
    matter how good the demand model is.
    """
    required = {"effective_unit_price", "move", "week"}
    missing = required - set(df.columns)
    if missing:
        raise EconomicsError(f"price_variation_summary requires columns {sorted(missing)}")

    grouped = df.sort_values(group_cols + ["week"]).groupby(group_cols, observed=True)
    out = grouped.agg(
        n_obs=("effective_unit_price", "size"),
        n_weeks=("week", "nunique"),
        n_distinct_prices=("effective_unit_price", "nunique"),
        price_min=("effective_unit_price", "min"),
        price_max=("effective_unit_price", "max"),
        price_mean=("effective_unit_price", "mean"),
        price_std=("effective_unit_price", "std"),
        total_units=("move", "sum"),
    )
    out["price_cv"] = out["price_std"] / out["price_mean"].replace(0, np.nan)
    out["price_range_pct"] = (out["price_max"] - out["price_min"]) / out["price_mean"].replace(
        0, np.nan
    )

    changes = grouped["effective_unit_price"].apply(
        lambda s: float((s.diff().abs() > 1e-9).sum())
    )
    out["n_price_changes"] = changes
    out["price_change_rate"] = out["n_price_changes"] / out["n_obs"].clip(lower=1)
    return out.reset_index()


def eligible_series(summary: pd.DataFrame, cfg_eligibility: dict) -> pd.Series:
    """Boolean mask of series with enough history and price variation."""
    return (
        (summary["n_obs"] >= int(cfg_eligibility["min_observations"]))
        & (summary["n_distinct_prices"] >= int(cfg_eligibility["min_distinct_prices"]))
        & (summary["price_cv"] >= float(cfg_eligibility["min_price_cv"]))
    )
