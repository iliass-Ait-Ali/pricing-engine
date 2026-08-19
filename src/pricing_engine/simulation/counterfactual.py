"""Counterfactual price simulation.

Given one decision context (a UPC x store x week row with its history) and a
grid of candidate prices, produce predicted demand and the resulting expected
economics for every candidate - in a single batched model call.

Two invariants define correctness here, and both are covered by tests:

1. **Only price-dependent features change.** All context features (lags,
   rolling means, calendar, promotion, cost) are held fixed.
2. **Cost is held fixed.** The historical accounting margin is *not* reused at
   a new price - doing so would mechanically fix the margin percentage and
   fabricate profit. Expected gross profit uses a fixed decision-time cost:

       expected_gross_profit(p) = (p - c) * predicted_units(p)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from pricing_engine.features.build import recompute_price_features
from pricing_engine.models.hybrid import REFERENCE_PRICE_COLUMN, attach_reference_price
from pricing_engine.simulation.price_grid import PriceGrid


class SimulationError(ValueError):
    """Raised when a counterfactual simulation cannot be performed."""


def _context_row(context: pd.DataFrame | pd.Series) -> pd.DataFrame:
    if isinstance(context, pd.Series):
        return context.to_frame().T
    if len(context) != 1:
        raise SimulationError(
            f"simulate_price_grid expects exactly one context row, got {len(context)}. "
            "Use simulate_many for batches."
        )
    return context


def simulate_price_grid(
    model,
    context: pd.DataFrame | pd.Series,
    prices: np.ndarray | PriceGrid,
    *,
    unit_cost: float | None = None,
) -> pd.DataFrame:
    """Score every candidate price for a single decision context.

    Returns one row per candidate with predicted units, expected revenue and -
    when a cost is available - expected gross profit.
    """
    row = _context_row(context)
    grid = prices.prices if isinstance(prices, PriceGrid) else np.asarray(prices, dtype="float64")
    if grid.size == 0:
        raise SimulationError("candidate price grid is empty")

    # Stamp the reference price from the OBSERVED context before any candidate
    # is substituted: an elasticity response is measured relative to p0.
    if REFERENCE_PRICE_COLUMN not in row.columns:
        row = attach_reference_price(row)

    frame = pd.concat([row] * grid.size, ignore_index=True)
    frame = recompute_price_features(frame, grid)

    units = np.asarray(model.predict(frame), dtype="float64")
    if np.any(units < 0):
        raise SimulationError("demand model returned negative predicted units")

    out = pd.DataFrame(
        {
            "candidate_price": grid,
            "predicted_units": units,
            "expected_revenue": grid * units,
        }
    )
    if unit_cost is not None and np.isfinite(unit_cost):
        out["unit_cost"] = float(unit_cost)
        out["expected_gross_profit"] = (grid - float(unit_cost)) * units
        out["expected_margin_rate"] = np.where(grid > 0, (grid - float(unit_cost)) / grid, np.nan)
    else:
        out["unit_cost"] = np.nan
        out["expected_gross_profit"] = np.nan
        out["expected_margin_rate"] = np.nan
    return out


def simulate_many(
    model,
    contexts: pd.DataFrame,
    price_matrix: np.ndarray,
    *,
    unit_costs: np.ndarray | None = None,
) -> pd.DataFrame:
    """Vectorised simulation for many contexts at once.

    ``price_matrix`` has shape ``(n_contexts, n_candidates)``. One model call is
    made for all ``n_contexts * n_candidates`` rows, which is what makes batch
    recommendation over tens of thousands of series tractable.
    """
    n_ctx, n_cand = price_matrix.shape
    if n_ctx != len(contexts):
        raise SimulationError(
            f"price matrix has {n_ctx} rows but {len(contexts)} contexts were supplied"
        )
    if REFERENCE_PRICE_COLUMN not in contexts.columns:
        contexts = attach_reference_price(contexts)
    repeated = contexts.loc[contexts.index.repeat(n_cand)].reset_index(drop=True)
    flat_prices = price_matrix.reshape(-1)
    frame = recompute_price_features(repeated, flat_prices)
    units = np.asarray(model.predict(frame), dtype="float64")

    ctx_id = np.repeat(np.arange(n_ctx), n_cand)
    out = pd.DataFrame(
        {
            "context_id": ctx_id,
            "candidate_price": flat_prices,
            "predicted_units": units,
            "expected_revenue": flat_prices * units,
        }
    )
    if unit_costs is not None:
        cost_flat = np.repeat(np.asarray(unit_costs, dtype="float64"), n_cand)
        out["unit_cost"] = cost_flat
        out["expected_gross_profit"] = (flat_prices - cost_flat) * units
        out["expected_margin_rate"] = np.where(
            flat_prices > 0, (flat_prices - cost_flat) / flat_prices, np.nan
        )
    return out


def demand_curve_diagnostics(sim: pd.DataFrame) -> dict:
    """Sanity statistics for one simulated demand curve.

    Flags the failure modes that make an accurate model dangerous for pricing:
    demand rising with price, non-monotone kinks and flat (price-insensitive)
    response.
    """
    p = sim["candidate_price"].to_numpy(dtype="float64")
    q = sim["predicted_units"].to_numpy(dtype="float64")
    order = np.argsort(p)
    p, q = p[order], q[order]
    dq = np.diff(q)
    increasing = float(np.mean(dq > 0)) if dq.size else float("nan")
    span = float(q.max() - q.min())
    rel_span = span / q.mean() if q.mean() > 0 else float("nan")
    with np.errstate(divide="ignore", invalid="ignore"):
        arc = np.divide(np.diff(np.log(q + 1e-9)), np.diff(np.log(p)))
    return {
        "n_candidates": int(p.size),
        "share_segments_increasing": increasing,
        "monotone_decreasing": bool(np.all(dq <= 1e-9)),
        "relative_demand_span": float(rel_span),
        "median_local_elasticity": float(np.nanmedian(arc)) if arc.size else float("nan"),
        "min_predicted_units": float(q.min()),
        "max_predicted_units": float(q.max()),
        "flat_response": bool(rel_span < 0.01),
    }
