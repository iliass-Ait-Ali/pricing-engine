"""Business and scientific guardrails on the candidate price range.

Constraints are intersected into a single feasible interval, and every binding
constraint is recorded so the recommendation can explain itself.

Implemented constraints
-----------------------
1. absolute minimum / maximum price (configuration)
2. price >= unit cost, unless loss-leading is explicitly allowed
3. minimum gross margin rate:  p >= c / (1 - m)
4. maximum change from the current price:  p in [p0 (1-d), p0 (1+d)]
5. historical extrapolation guardrail: stay inside the observed price support
   of that UPC x store series, expanded by a small configured tolerance
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

#: Constraint name -> the reason code published on the recommendation.
_REASON_FOR = {
    "COST_FLOOR": "MARGIN_CONSTRAINT",
    "MIN_MARGIN": "MARGIN_CONSTRAINT",
    "ABSOLUTE_PRICE_FLOOR": "ABSOLUTE_PRICE_FLOOR",
    "ABSOLUTE_PRICE_CEILING": "ABSOLUTE_PRICE_CEILING",
}


class ConstraintError(ValueError):
    """Raised when the constraint set is inconsistent or unsatisfiable."""


@dataclass
class PriceBounds:
    """Feasible price interval plus its provenance."""

    low: float
    high: float
    binding: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not np.isfinite(self.low) or not np.isfinite(self.high) or self.high < self.low


@dataclass
class ConstraintResult:
    """Outcome of applying constraints to a candidate grid."""

    feasible_mask: np.ndarray
    bounds: PriceBounds
    reasons: list[str]


def build_bounds(
    current_price: float,
    *,
    policy: dict[str, Any],
    unit_cost: float | None,
    hist_price_min: float | None = None,
    hist_price_max: float | None = None,
    abs_min_price: float | None = None,
    abs_max_price: float | None = None,
) -> PriceBounds:
    """Intersect all guardrails into one feasible price interval.

    ``binding`` lists the constraints that are **actually active at the edges**
    of the final interval, not every constraint that happened to tighten a
    running bound along the way. That distinction matters: an earlier version
    appended ``PRICE_CHANGE_LIMIT`` whenever the change window was narrower
    than the absolute floor, even when the extrapolation guardrail later
    superseded it, which over-reported how often the change cap binds
    (Phase M constraint-attribution audit).
    """
    if not np.isfinite(current_price) or current_price <= 0:
        raise ConstraintError(f"current price must be positive, got {current_price!r}")

    details: dict[str, Any] = {}
    lower: dict[str, float] = {}
    upper: dict[str, float] = {}

    # 1. absolute price floor / ceiling
    lower["ABSOLUTE_PRICE_FLOOR"] = 0.01 if abs_min_price is None else float(abs_min_price)
    if abs_max_price is not None:
        upper["ABSOLUTE_PRICE_CEILING"] = float(abs_max_price)

    # 4. maximum change from the current price
    max_change = float(policy.get("max_price_change_pct", 0.10))
    lower["PRICE_CHANGE_LIMIT"] = current_price * (1.0 - max_change)
    upper["PRICE_CHANGE_LIMIT"] = current_price * (1.0 + max_change)
    details["max_price_change_pct"] = max_change
    details["price_change_window"] = [lower["PRICE_CHANGE_LIMIT"], upper["PRICE_CHANGE_LIMIT"]]

    # 5. historical extrapolation guardrail
    tol = float(policy.get("extrapolation_tolerance_pct", 0.05))
    details["extrapolation_tolerance_pct"] = tol
    if hist_price_min is not None and np.isfinite(hist_price_min):
        lower["OUTSIDE_EXTRAPOLATION_RANGE"] = float(hist_price_min) * (1.0 - tol)
        details["support_low"] = lower["OUTSIDE_EXTRAPOLATION_RANGE"]
    if hist_price_max is not None and np.isfinite(hist_price_max):
        upper["OUTSIDE_EXTRAPOLATION_RANGE"] = float(hist_price_max) * (1.0 + tol)
        details["support_high"] = upper["OUTSIDE_EXTRAPOLATION_RANGE"]

    # 2 + 3. cost floor and minimum margin
    allow_below_cost = bool(policy.get("allow_below_cost", False))
    min_margin = float(policy.get("min_gross_margin_rate", 0.0))
    if unit_cost is not None and np.isfinite(unit_cost):
        details["unit_cost"] = float(unit_cost)
        if not allow_below_cost:
            lower["COST_FLOOR"] = float(unit_cost)
        if min_margin > 0:
            if min_margin >= 1.0:
                raise ConstraintError("min_gross_margin_rate must be < 1")
            margin_floor = float(unit_cost) / (1.0 - min_margin)
            details["margin_floor"] = margin_floor
            lower["MIN_MARGIN"] = margin_floor

    low = max(lower.values())
    high = min(upper.values()) if upper else float("inf")

    binding: list[str] = []
    for name, value in lower.items():
        if abs(value - low) <= 1e-9 * max(1.0, abs(low)):
            binding.append(_REASON_FOR.get(name, name))
    for name, value in upper.items():
        if abs(value - high) <= 1e-9 * max(1.0, abs(high)):
            binding.append(_REASON_FOR.get(name, name))

    details["lower_bounds"] = {k: float(v) for k, v in lower.items()}
    details["upper_bounds"] = {k: float(v) for k, v in upper.items()}
    return PriceBounds(
        low=float(low), high=float(high), binding=sorted(set(binding)), details=details
    )


def apply_constraints(prices: np.ndarray, bounds: PriceBounds) -> ConstraintResult:
    """Mask the candidate grid with the feasible interval."""
    p = np.asarray(prices, dtype="float64")
    mask = (p >= bounds.low - 1e-9) & (p <= bounds.high + 1e-9)
    reasons = list(bounds.binding) if mask.any() else [*bounds.binding, "NO_FEASIBLE_PRICE"]
    return ConstraintResult(feasible_mask=mask, bounds=bounds, reasons=reasons)
