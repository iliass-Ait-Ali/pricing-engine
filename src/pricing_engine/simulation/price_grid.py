"""Candidate price grids.

The optimizer scores a discrete grid rather than calling a continuous solver:
the demand model is a step function (boosted trees), so gradients are
meaningless, and a grid makes every constraint check and every reason code
explicit and testable.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


class PriceGridError(ValueError):
    """Raised when a candidate price grid cannot be constructed."""


@dataclass(frozen=True)
class PriceGrid:
    """A candidate grid plus the bounds that produced it."""

    prices: np.ndarray
    low: float
    high: float
    step: float
    current_price: float

    def __len__(self) -> int:
        return int(self.prices.size)


def round_to(values: np.ndarray, increment: float) -> np.ndarray:
    """Round to a configured price increment (e.g. 0.01 or 0.05)."""
    if increment <= 0:
        return values
    return np.round(values / increment) * increment


def apply_charm_pricing(values: np.ndarray) -> np.ndarray:
    """Snap prices to the nearest x.x9 ending (a common grocery convention)."""
    return np.floor(values * 10.0) / 10.0 + 0.09


def build_price_grid(
    current_price: float,
    *,
    low: float,
    high: float,
    step: float,
    rounding: float = 0.01,
    charm: bool = False,
    include_current: bool = True,
) -> PriceGrid:
    """Build the candidate price grid inside ``[low, high]``.

    The current price is always included (unless explicitly disabled), because
    "keep the current price" must be a scoreable option, not a special case.
    """
    if not np.isfinite(current_price) or current_price <= 0:
        raise PriceGridError(f"current price must be positive and finite, got {current_price!r}")
    if step <= 0:
        raise PriceGridError(f"price step must be positive, got {step!r}")
    if not np.isfinite(low) or not np.isfinite(high):
        raise PriceGridError(f"price bounds must be finite, got [{low}, {high}]")
    if high < low:
        raise PriceGridError(
            f"empty feasible price range [{low:.4f}, {high:.4f}]: the constraints "
            "leave no candidate price."
        )

    n = int(np.floor((high - low) / step)) + 1
    prices = low + step * np.arange(n, dtype="float64")
    if include_current:
        prices = np.append(prices, current_price)
    prices = round_to(prices, rounding)
    if charm:
        prices = np.unique(np.append(apply_charm_pricing(prices), round_to(np.array([current_price]), rounding)))
    prices = prices[(prices >= round_to(np.array([low]), rounding)[0] - 1e-9)]
    prices = prices[(prices <= round_to(np.array([high]), rounding)[0] + 1e-9)]
    prices = np.unique(prices[prices > 0])
    if prices.size == 0:
        raise PriceGridError(
            f"no candidate prices survived rounding in [{low:.4f}, {high:.4f}] with step {step}"
        )
    return PriceGrid(prices=prices, low=float(low), high=float(high), step=float(step),
                     current_price=float(current_price))
