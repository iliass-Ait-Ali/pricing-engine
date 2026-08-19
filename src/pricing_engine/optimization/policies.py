"""Pricing policies compared in the offline backtest.

A *policy* maps a decision context to a price. Comparing policies is a
different exercise from comparing demand models: the demand model is held
fixed, and what changes is the decision rule applied on top of it.

Policies implemented:

* ``HistoricalPricePolicy`` - charge what Dominick's actually charged. This is
  the reference: it is the only policy whose outcome we actually observed.
* ``SimpleMarginPolicy`` - cost-plus: price = cost / (1 - target margin).
* ``ElasticityBaselinePolicy`` - textbook constant-elasticity markup
  p* = c * e / (1 + e) for e < -1, clipped to the feasible range.
* ``MLPricingPolicy`` - the constrained optimizer of this project.

Every policy is scored with the same demand model and the same economics, so
the comparison isolates the decision rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from pricing_engine.config import Config
from pricing_engine.optimization.constraints import build_bounds
from pricing_engine.optimization.optimizer import optimize_price


@dataclass
class PolicyResult:
    """Prices chosen by a policy plus per-context reason codes."""

    name: str
    prices: np.ndarray
    reason_codes: list[list[str]]
    notes: dict[str, Any]


class Policy:
    name = "policy"
    causal_claim = False

    def recommend(self, contexts: pd.DataFrame, **kwargs) -> PolicyResult:
        raise NotImplementedError


def _clip_to_bounds(
    prices: np.ndarray, contexts: pd.DataFrame, policy_cfg: dict, stats: pd.DataFrame
) -> tuple[np.ndarray, list[list[str]]]:
    """Apply the same guardrails every policy must respect."""
    out = np.empty(len(contexts), dtype="float64")
    reasons: list[list[str]] = []
    current = contexts["effective_unit_price"].to_numpy(dtype="float64")
    costs = contexts["decision_time_unit_cost"].to_numpy(dtype="float64")
    pmin = stats["price_min"].to_numpy(dtype="float64")
    pmax = stats["price_max"].to_numpy(dtype="float64")
    for i in range(len(contexts)):
        cost = float(costs[i]) if np.isfinite(costs[i]) and costs[i] > 0 else None
        bounds = build_bounds(
            float(current[i]),
            policy=policy_cfg,
            unit_cost=cost,
            hist_price_min=float(pmin[i]) if np.isfinite(pmin[i]) else None,
            hist_price_max=float(pmax[i]) if np.isfinite(pmax[i]) else None,
        )
        clipped = float(np.clip(prices[i], bounds.low, bounds.high))
        out[i] = clipped
        r = list(bounds.binding)
        if abs(clipped - prices[i]) > 1e-9:
            r.append("CLIPPED_TO_GUARDRAILS")
        reasons.append(r)
    return out, reasons


class HistoricalPricePolicy(Policy):
    name = "HistoricalPricePolicy"

    def recommend(self, contexts: pd.DataFrame, **kwargs) -> PolicyResult:
        prices = contexts["effective_unit_price"].to_numpy(dtype="float64")
        return PolicyResult(
            name=self.name,
            prices=prices,
            reason_codes=[["HISTORICAL_PRICE"] for _ in range(len(contexts))],
            notes={"description": "the price actually charged; the only observed outcome"},
        )


class SimpleMarginPolicy(Policy):
    name = "SimpleMarginPolicy"

    def __init__(self, target_margin: float = 0.20) -> None:
        self.target_margin = float(target_margin)

    def recommend(
        self, contexts: pd.DataFrame, *, policy_cfg: dict, stats: pd.DataFrame, **kwargs
    ) -> PolicyResult:
        cost = contexts["decision_time_unit_cost"].to_numpy(dtype="float64")
        raw = np.where(np.isfinite(cost) & (cost > 0), cost / (1.0 - self.target_margin), np.nan)
        current = contexts["effective_unit_price"].to_numpy(dtype="float64")
        raw = np.where(np.isfinite(raw), raw, current)
        prices, reasons = _clip_to_bounds(raw, contexts, policy_cfg, stats)
        return PolicyResult(
            name=self.name,
            prices=prices,
            reason_codes=reasons,
            notes={"target_margin": self.target_margin, "description": "cost-plus pricing"},
        )


class ElasticityBaselinePolicy(Policy):
    name = "ElasticityBaselinePolicy"

    def __init__(self, elasticity_by_upc: pd.DataFrame, default_elasticity: float = -2.0) -> None:
        self.table = elasticity_by_upc.set_index("upc")["elasticity"].to_dict()
        self.default = float(default_elasticity)

    def recommend(
        self, contexts: pd.DataFrame, *, policy_cfg: dict, stats: pd.DataFrame, **kwargs
    ) -> PolicyResult:
        cost = contexts["decision_time_unit_cost"].to_numpy(dtype="float64")
        current = contexts["effective_unit_price"].to_numpy(dtype="float64")
        e = contexts["upc"].map(self.table).fillna(self.default).to_numpy(dtype="float64")
        # Constant-elasticity monopoly markup; only defined for elastic demand.
        with np.errstate(divide="ignore", invalid="ignore"):
            markup = np.where(e < -1.0, e / (1.0 + e), np.nan)
        raw = np.where(np.isfinite(markup) & np.isfinite(cost) & (cost > 0), cost * markup, current)
        prices, reasons = _clip_to_bounds(raw, contexts, policy_cfg, stats)
        return PolicyResult(
            name=self.name,
            prices=prices,
            reason_codes=reasons,
            notes={
                "default_elasticity": self.default,
                "description": "p = c * e / (1 + e) using observational per-UPC elasticities",
            },
        )


class MLPricingPolicy(Policy):
    name = "MLPricingPolicy"

    def __init__(self, model, cfg: Config, policy_profile: str = "standard", objective: str = "gross_profit") -> None:
        self.model = model
        self.cfg = cfg
        self.policy_profile = policy_profile
        self.objective = objective

    def recommend(self, contexts: pd.DataFrame, *, stats: pd.DataFrame, **kwargs) -> PolicyResult:
        prices = np.empty(len(contexts), dtype="float64")
        reasons: list[list[str]] = []
        n_keep = 0
        for i in range(len(contexts)):
            row = contexts.iloc[[i]]
            rec = optimize_price(
                self.model,
                row,
                cfg=self.cfg,
                policy_profile=self.policy_profile,
                objective=self.objective,
                series_stats=stats.iloc[i].to_dict(),
            )
            # Only ACTIONABLE recommendations move a price. REVIEW_REQUIRED
            # proposals are held at the current price, exactly as the human-in-
            # the-loop policy requires.
            prices[i] = rec.final_recommended_price
            reasons.append(rec.reason_codes)
            n_keep += int(not rec.actionable)
        return PolicyResult(
            name=self.name,
            prices=prices,
            reason_codes=reasons,
            notes={
                "policy_profile": self.policy_profile,
                "objective": self.objective,
                "non_actionable_share": n_keep / max(len(contexts), 1),
            },
        )
