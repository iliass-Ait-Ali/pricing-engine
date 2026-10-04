"""Shared helpers for the commercial analyses (value sizing, product roles).

Both analyses re-score exactly the decision-week sample that
``scripts/optimize.py --batch N`` scores (same seed, same contexts, same
decisions) without appending to the audit log, so their numbers reconcile
with ``artifacts/metrics/recommendations.json``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (REPO_ROOT / "src", REPO_ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from optimize import STAT_COLUMNS, load_context_pool  # noqa: E402

from pricing_engine.models.demand_model import load_model  # noqa: E402
from pricing_engine.models.hybrid import load_pricing_model  # noqa: E402
from pricing_engine.optimization.optimizer import optimize_price_batch  # noqa: E402


def score_week(cfg, *, batch: int, profile: str, week: int | None = None) -> tuple[pd.DataFrame, int]:
    """Recommendations for the optimize.py sample of one decision week.

    Returns one row per context (``Recommendation.as_dict()``) restricted to
    contexts with a usable cost and baseline volume, and the decision week.
    """
    base_model = load_model(cfg.path("models_dir") / "demand_model.joblib", cfg=cfg)
    model = load_pricing_model(base_model, cfg=cfg)
    usable, stats = load_context_pool(cfg)
    week = week or int(usable["week"].max())
    pool = usable[usable["week"] == week].merge(stats, on=["upc", "store"], how="left")
    take = pool.sample(min(batch, len(pool)), random_state=cfg.seed).reset_index(drop=True)
    recs = optimize_price_batch(
        model, take, cfg=cfg, policy_profile=profile,
        series_stats=take[STAT_COLUMNS].to_dict(orient="records"),
    )
    df = pd.DataFrame([r.as_dict() for r in recs])
    keep = df["expected_gross_profit_current"].notna() & df["unit_cost_used"].notna()
    return df[keep].reset_index(drop=True), week


def applied_prices(df: pd.DataFrame) -> np.ndarray:
    """Prices that would actually move: only RECOMMEND_CHANGE changes a price."""
    return np.where(df["actionable"], df["final_recommended_price"], df["current_price"]).astype(float)


def rescore(df: pd.DataFrame, elasticity: float | np.ndarray, prices: np.ndarray) -> np.ndarray:
    """Per-context gross profit at ``prices`` under a constant-elasticity response.

    ``Q(p) = Q0 * (p / p0) ** e`` around each context's own predicted volume
    at the current price, with unit cost held fixed (the engine's convention).
    """
    p0 = df["current_price"].to_numpy(dtype=float)
    q0 = df["predicted_units_current"].to_numpy(dtype=float)
    c = df["unit_cost_used"].to_numpy(dtype=float)
    return (prices - c) * q0 * np.power(prices / p0, elasticity)


def context_elasticity(df: pd.DataFrame) -> np.ndarray:
    """The elasticity each context was priced with (median where missing)."""
    eps = df["elasticity_used"].astype(float)
    return eps.fillna(eps.median()).to_numpy(dtype=float)
