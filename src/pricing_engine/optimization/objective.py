"""Optimization objectives.

    revenue(p)      = p * Q_hat(p)
    gross_profit(p) = (p - c) * Q_hat(p)

The primary objective is expected gross profit; expected revenue is supported
as a secondary objective. Both are computed from the *same* predicted demand,
so the only difference between them is the economics applied on top.
"""

from __future__ import annotations

import numpy as np

VALID_OBJECTIVES = ("gross_profit", "revenue")


class ObjectiveError(ValueError):
    """Raised when an objective cannot be evaluated with the given inputs."""


def objective_values(
    prices: np.ndarray,
    predicted_units: np.ndarray,
    *,
    objective: str = "gross_profit",
    unit_cost: float | None = None,
) -> np.ndarray:
    """Objective value for every candidate price."""
    if objective not in VALID_OBJECTIVES:
        raise ObjectiveError(
            f"Unknown objective {objective!r}. Supported objectives: {list(VALID_OBJECTIVES)}."
        )
    p = np.asarray(prices, dtype="float64")
    q = np.asarray(predicted_units, dtype="float64")
    if p.shape != q.shape:
        raise ObjectiveError(f"prices and predicted units have different shapes: {p.shape} vs {q.shape}")
    if np.any(q < 0):
        raise ObjectiveError("predicted units must be non-negative")

    if objective == "revenue":
        return p * q

    if unit_cost is None or not np.isfinite(unit_cost):
        raise ObjectiveError(
            "Gross-profit optimization requires an available unit cost (estimated AAC) "
            "for this UPC and store. Use objective='revenue' or supply a cost."
        )
    return (p - float(unit_cost)) * q


def analytical_linear_optimum(a: float, b: float, c: float) -> float:
    """Profit-maximising price for the textbook linear demand Q(p) = a - b p.

    Pi(p) = (p - c)(a - b p)  ->  p* = (a + b c) / (2 b)

    Used by the analytical optimizer tests as ground truth.
    """
    if b <= 0:
        raise ObjectiveError("linear demand slope b must be positive")
    return (a + b * c) / (2.0 * b)


def analytical_linear_revenue_optimum(a: float, b: float) -> float:
    """Revenue-maximising price for Q(p) = a - b p: p* = a / (2 b)."""
    if b <= 0:
        raise ObjectiveError("linear demand slope b must be positive")
    return a / (2.0 * b)
