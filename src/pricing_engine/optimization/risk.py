"""Heuristic recommendation-risk layer.

This is **not** a calibrated statistical confidence interval, and it is never
described as one. It is a transparent rule-based score over the factors that
actually make a price recommendation unreliable in this dataset:

* how much history the series has,
* how much its price has actually moved,
* how far the recommendation sits from the observed price support,
* whether promotion coding makes the price effect ambiguous.

Direction of the scale (Phase L fix - it used to read the other way round):

    LOW RISK    plenty of history and price variation, recommendation inside
                the observed price support -> may be actionable
    MEDIUM RISK usable but thinner evidence -> conservative recommendation
                under a tighter price-change cap
    HIGH RISK   thin evidence and/or extrapolation -> never an automatic price
                change under the default policies; REVIEW_REQUIRED or
                KEEP_CURRENT
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RiskLevel(StrEnum):
    """Risk of acting on the recommendation. HIGH = risky, LOW = well evidenced."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass
class RiskAssessment:
    level: RiskLevel
    factors: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def is_high_risk(self) -> bool:
        return self.level is RiskLevel.HIGH

    @property
    def is_low_risk(self) -> bool:
        return self.level is RiskLevel.LOW


def extrapolation_distance(price: float, hist_min: float | None, hist_max: float | None) -> float:
    """Relative distance outside the observed price support (0 when inside)."""
    if hist_min is None or hist_max is None:
        return float("nan")
    if price < hist_min:
        return (hist_min - price) / hist_min if hist_min > 0 else float("nan")
    if price > hist_max:
        return (price - hist_max) / hist_max if hist_max > 0 else float("nan")
    return 0.0


def assess_risk(
    *,
    n_obs: int | None,
    n_distinct_prices: int | None,
    price_cv: float | None,
    recommended_price: float,
    hist_price_min: float | None,
    hist_price_max: float | None,
    price_change_pct: float,
    cfg_risk: dict[str, Any],
    promotion_share: float | None = None,
    elasticity_source: str | None = None,
) -> RiskAssessment:
    """Return a LOW / MEDIUM / HIGH risk judgement with the factors behind it."""
    low_band = cfg_risk.get("low_risk", {})
    medium_band = cfg_risk.get("medium_risk", {})

    dist = extrapolation_distance(recommended_price, hist_price_min, hist_price_max)

    factors: dict[str, Any] = {
        "n_obs": n_obs,
        "n_distinct_prices": n_distinct_prices,
        "price_cv": price_cv,
        "extrapolation_distance": dist,
        "abs_price_change_pct": abs(price_change_pct),
        "promotion_share": promotion_share,
        "elasticity_source": elasticity_source,
    }
    notes: list[str] = []
    no_support = dist != dist  # NaN
    if no_support:
        notes.append("no observed price support available for this series")

    def meets(band: dict[str, Any]) -> bool:
        ok = True
        if band.get("min_observations") is not None:
            ok &= (n_obs or 0) >= int(band["min_observations"])
        if band.get("min_distinct_prices") is not None:
            ok &= (n_distinct_prices or 0) >= int(band["min_distinct_prices"])
        if band.get("max_extrapolation_pct") is not None:
            ok &= (not no_support) and dist <= float(band["max_extrapolation_pct"])
        return ok

    if meets(low_band):
        level = RiskLevel.LOW
    elif meets(medium_band):
        level = RiskLevel.MEDIUM
    else:
        level = RiskLevel.HIGH
        notes.append("thin history, little price variation, or the price extrapolates")

    if no_support:
        level = RiskLevel.HIGH

    if promotion_share is not None and promotion_share > 0.5:
        notes.append(
            "more than half of this series' weeks carry a promotion code: the price "
            "effect is entangled with promotion activity"
        )
        if level is RiskLevel.LOW:
            level = RiskLevel.MEDIUM

    if elasticity_source == "pooled_fallback":
        notes.append(
            "no usable product-specific elasticity: the category-level (pooled) "
            "estimate is being applied to this product"
        )
        if level is RiskLevel.LOW:
            level = RiskLevel.MEDIUM

    return RiskAssessment(level=level, factors=factors, notes=notes)
