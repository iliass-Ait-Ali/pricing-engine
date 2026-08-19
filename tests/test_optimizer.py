"""Phase G tests: the optimizer must recover the analytical optimum and obey
every constraint, using deterministic mathematical fixtures.

Ground truth for linear demand Q(p) = a - b p and constant unit cost c:

    Pi(p)  = (p - c)(a - b p)      ->  p* = (a + b c) / (2 b)
    R(p)   = p (a - b p)           ->  p* = a / (2 b)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pricing_engine.optimization.constraints import ConstraintError, build_bounds
from pricing_engine.optimization.objective import (
    ObjectiveError,
    analytical_linear_optimum,
    analytical_linear_revenue_optimum,
    objective_values,
)
from pricing_engine.optimization.optimizer import ReasonCode, optimize_price
from pricing_engine.optimization.risk import RiskLevel, assess_risk, extrapolation_distance


class LinearDemandModel:
    """Deterministic Q(p) = a - b p demand, clipped at zero."""

    def __init__(self, a: float = 100.0, b: float = 10.0) -> None:
        self.a, self.b = a, b
        self.metadata = None

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        p = frame["effective_unit_price"].to_numpy(dtype="float64")
        return np.clip(self.a - self.b * p, 0.0, None)


class ConstantElasticityModel:
    """Q(p) = k * p^e, the textbook constant-elasticity fixture."""

    def __init__(self, k: float = 1000.0, e: float = -2.0) -> None:
        self.k, self.e = k, e
        self.metadata = None

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        p = frame["effective_unit_price"].to_numpy(dtype="float64")
        return self.k * np.power(p, self.e)


def make_context(price: float = 4.00, cost: float = 2.50) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "upc": 111,
                "store": 2,
                "week": 300,
                "week_start_date": pd.Timestamp("1995-06-01"),
                "effective_unit_price": price,
                "lag_price_1": price,
                "lag_price_2": price,
                "roll_mean_price_4": price,
                "series_reference_price": price,
                "decision_time_unit_cost": cost,
                "lag_move_1": 30.0,
                "roll_mean_move_4": 28.0,
                "recorded_promotion_flag": 0,
                "descrip": "TEST FLAKES",
            }
        ]
    )


def wide_stats(price: float = 4.00) -> dict:
    """Series history that comfortably passes eligibility AND is LOW risk."""
    return {
        "n_obs": 200,
        "n_distinct_prices": 25,
        "price_cv": 0.20,
        "price_min": price * 0.5,
        "price_max": price * 1.6,
    }


def thin_stats(price: float = 4.00) -> dict:
    """Eligible (>= 40 obs, >= 5 prices) but HIGH risk under the risk bands."""
    return {
        "n_obs": 45,
        "n_distinct_prices": 6,
        "price_cv": 0.20,
        "price_min": price * 0.5,
        "price_max": price * 1.6,
    }


# ---------------------------------------------------------------------------
# analytical optimum
# ---------------------------------------------------------------------------
def test_analytical_formulas():
    assert analytical_linear_optimum(100.0, 10.0, 2.5) == pytest.approx(6.25)
    assert analytical_linear_revenue_optimum(100.0, 10.0) == pytest.approx(5.0)
    with pytest.raises(ObjectiveError):
        analytical_linear_optimum(100.0, 0.0, 2.5)


def test_optimizer_recovers_linear_profit_optimum(cfg):
    a, b, c = 100.0, 10.0, 2.50
    target = analytical_linear_optimum(a, b, c)  # 6.25
    rec = optimize_price(
        LinearDemandModel(a, b),
        make_context(price=5.40, cost=c),
        cfg=cfg,
        policy_profile="aggressive",  # +/-20% around 5.40 covers 6.25
        objective="gross_profit",
        series_stats=wide_stats(5.40),
    )
    step = float(cfg.get("optimization.price_step", 0.05))
    assert rec.proposed_candidate_price == pytest.approx(target, abs=step)
    assert rec.decision == "RECOMMEND_CHANGE"
    assert ReasonCode.PROFIT_UPLIFT_POSITIVE.value in rec.reason_codes


def test_optimizer_recovers_linear_revenue_optimum(cfg):
    a, b = 100.0, 10.0
    target = analytical_linear_revenue_optimum(a, b)  # 5.00
    rec = optimize_price(
        LinearDemandModel(a, b),
        make_context(price=4.60, cost=2.0),
        cfg=cfg,
        policy_profile="aggressive",
        objective="revenue",
        series_stats=wide_stats(4.60),
    )
    step = float(cfg.get("optimization.price_step", 0.05))
    assert rec.proposed_candidate_price == pytest.approx(target, abs=step + 1e-6)
    assert ReasonCode.REVENUE_UPLIFT_POSITIVE.value in rec.reason_codes


def test_constant_elasticity_fixture_pushes_price_to_the_upper_bound(cfg):
    """With |e| < 1 demand is inelastic, so profit rises with price up to the cap."""
    rec = optimize_price(
        ConstantElasticityModel(k=1000.0, e=-0.5),
        make_context(price=4.00, cost=2.0),
        cfg=cfg,
        policy_profile="standard",
        objective="gross_profit",
        series_stats=wide_stats(4.00),
    )
    assert rec.proposed_candidate_price > rec.current_price
    assert rec.proposed_candidate_price == pytest.approx(rec.constraints["bounds"][1], abs=0.06)
    assert ReasonCode.PRICE_CHANGE_LIMIT.value in rec.reason_codes


# ---------------------------------------------------------------------------
# constraints
# ---------------------------------------------------------------------------
def test_max_price_change_constraint_binds(cfg):
    rec = optimize_price(
        LinearDemandModel(100.0, 10.0),
        make_context(price=4.00, cost=2.50),
        cfg=cfg,
        policy_profile="conservative",  # +/-5%
        objective="gross_profit",
        series_stats=wide_stats(4.00),
    )
    assert abs(rec.price_change_pct) <= 0.05 + 1e-9
    assert ReasonCode.PRICE_CHANGE_LIMIT.value in rec.reason_codes


def test_minimum_margin_constraint_raises_the_floor():
    bounds = build_bounds(
        4.00,
        policy={"max_price_change_pct": 0.50, "min_gross_margin_rate": 0.30, "allow_below_cost": False},
        unit_cost=3.00,
    )
    assert bounds.low == pytest.approx(3.00 / 0.70)
    assert "MARGIN_CONSTRAINT" in bounds.binding


def test_price_never_goes_below_cost_unless_allowed():
    bounds = build_bounds(
        4.00, policy={"max_price_change_pct": 0.90, "allow_below_cost": False}, unit_cost=3.50
    )
    assert bounds.low >= 3.50
    permissive = build_bounds(
        4.00, policy={"max_price_change_pct": 0.90, "allow_below_cost": True}, unit_cost=3.50
    )
    assert permissive.low < 3.50


def test_extrapolation_guardrail_limits_the_range():
    bounds = build_bounds(
        4.00,
        policy={"max_price_change_pct": 0.50, "extrapolation_tolerance_pct": 0.05},
        unit_cost=1.0,
        hist_price_min=3.80,
        hist_price_max=4.20,
    )
    assert bounds.low == pytest.approx(3.80 * 0.95)
    assert bounds.high == pytest.approx(4.20 * 1.05)
    assert "OUTSIDE_EXTRAPOLATION_RANGE" in bounds.binding


def test_invalid_margin_configuration_is_rejected():
    with pytest.raises(ConstraintError):
        build_bounds(4.0, policy={"min_gross_margin_rate": 1.0}, unit_cost=2.0)


def test_invalid_current_price_is_rejected():
    with pytest.raises(ConstraintError):
        build_bounds(0.0, policy={}, unit_cost=2.0)


# ---------------------------------------------------------------------------
# keep-current behaviour
# ---------------------------------------------------------------------------
def test_price_already_at_the_optimum_returns_keep_current(cfg):
    """Starting exactly at the analytical optimum, nothing should move."""
    rec = optimize_price(
        LinearDemandModel(100.0, 10.0),
        make_context(price=6.25, cost=2.50),  # p* = (a + b c) / (2 b) = 6.25
        cfg=cfg,
        policy_profile="aggressive",
        objective="gross_profit",
        series_stats=wide_stats(6.25),
    )
    assert rec.decision == "KEEP_CURRENT"
    assert rec.final_recommended_price == rec.current_price
    assert ReasonCode.KEEP_CURRENT_OPTIMAL.value in rec.reason_codes


def test_materiality_threshold_suppresses_a_tiny_improvement(cfg):
    """A real but immaterial model-internal estimated gain must not trigger a change.

    At p = 6.00 the analytical optimum 6.25 is only ~0.45% better, which is
    below the standard profile's 1% materiality threshold.
    """
    rec = optimize_price(
        LinearDemandModel(100.0, 10.0),
        make_context(price=6.00, cost=2.50),
        cfg=cfg,
        policy_profile="standard",
        objective="gross_profit",
        series_stats=wide_stats(6.00),
    )
    assert rec.decision == "KEEP_CURRENT"
    assert ReasonCode.NON_MATERIAL_UPLIFT.value in rec.reason_codes
    assert rec.model_internal_estimated_profit_uplift_pct == 0.0


def test_insufficient_price_variation_forces_keep_current(cfg):
    rec = optimize_price(
        LinearDemandModel(),
        make_context(price=4.00, cost=2.50),
        cfg=cfg,
        series_stats={"n_obs": 200, "n_distinct_prices": 2, "price_cv": 0.001,
                      "price_min": 3.99, "price_max": 4.01},
    )
    assert rec.decision == "KEEP_CURRENT"
    assert ReasonCode.INSUFFICIENT_PRICE_VARIATION.value in rec.reason_codes


def test_insufficient_history_forces_keep_current(cfg):
    rec = optimize_price(
        LinearDemandModel(),
        make_context(price=4.00, cost=2.50),
        cfg=cfg,
        series_stats={"n_obs": 5, "n_distinct_prices": 20, "price_cv": 0.3,
                      "price_min": 2.0, "price_max": 6.0},
    )
    assert rec.decision == "KEEP_CURRENT"
    assert ReasonCode.INSUFFICIENT_HISTORY.value in rec.reason_codes


def test_missing_cost_blocks_gross_profit_optimization(cfg):
    ctx = make_context(price=4.00)
    ctx["decision_time_unit_cost"] = np.nan
    rec = optimize_price(
        LinearDemandModel(), ctx, cfg=cfg, objective="gross_profit", series_stats=wide_stats(4.00)
    )
    assert rec.decision == "KEEP_CURRENT"
    assert ReasonCode.COST_UNAVAILABLE.value in rec.reason_codes


def test_revenue_objective_still_works_without_cost(cfg):
    ctx = make_context(price=4.60)
    ctx["decision_time_unit_cost"] = np.nan
    rec = optimize_price(
        LinearDemandModel(), ctx, cfg=cfg, objective="revenue",
        policy_profile="aggressive", series_stats=wide_stats(4.60)
    )
    assert rec.expected_gross_profit_current is None
    assert rec.decision in {"RECOMMEND_CHANGE", "KEEP_CURRENT", "REVIEW_REQUIRED"}


# ---------------------------------------------------------------------------
# objective / numerical safety
# ---------------------------------------------------------------------------
def test_objective_rejects_unknown_name():
    with pytest.raises(ObjectiveError):
        objective_values(np.array([1.0]), np.array([1.0]), objective="market_share")


def test_objective_rejects_negative_demand():
    with pytest.raises(ObjectiveError):
        objective_values(np.array([1.0]), np.array([-1.0]), objective="revenue")


def test_gross_profit_objective_requires_cost():
    with pytest.raises(ObjectiveError):
        objective_values(np.array([1.0]), np.array([1.0]), objective="gross_profit", unit_cost=None)


def test_proposed_price_is_rounded(cfg):
    rec = optimize_price(
        LinearDemandModel(100.0, 10.0),
        make_context(price=6.00, cost=2.50),
        cfg=cfg,
        policy_profile="aggressive",
        series_stats=wide_stats(6.00),
    )
    rounding = float(cfg.get("optimization.price_rounding", 0.01))
    assert abs(round(rec.proposed_candidate_price / rounding) * rounding - rec.proposed_candidate_price) < 1e-9


# ---------------------------------------------------------------------------
# risk layer
# ---------------------------------------------------------------------------
def test_extrapolation_distance_is_zero_inside_support():
    assert extrapolation_distance(4.0, 3.5, 4.5) == 0.0
    assert extrapolation_distance(5.0, 3.5, 4.5) == pytest.approx(0.1111, abs=1e-3)
    assert extrapolation_distance(3.0, 3.5, 4.5) == pytest.approx(0.1428, abs=1e-3)


def test_risk_levels_follow_history_and_extrapolation(cfg):
    """HIGH means risky. Plenty of evidence -> LOW; thin/extrapolating -> HIGH."""
    risk_cfg = cfg.require("risk")
    well_evidenced = assess_risk(
        n_obs=200, n_distinct_prices=20, price_cv=0.2, recommended_price=4.0,
        hist_price_min=3.0, hist_price_max=5.0, price_change_pct=0.02, cfg_risk=risk_cfg
    )
    assert well_evidenced.level is RiskLevel.LOW

    thin = assess_risk(
        n_obs=10, n_distinct_prices=2, price_cv=0.01, recommended_price=9.0,
        hist_price_min=3.0, hist_price_max=5.0, price_change_pct=0.5, cfg_risk=risk_cfg
    )
    assert thin.level is RiskLevel.HIGH

    middling = assess_risk(
        n_obs=100, n_distinct_prices=10, price_cv=0.2, recommended_price=4.0,
        hist_price_min=3.0, hist_price_max=5.0, price_change_pct=0.02, cfg_risk=risk_cfg
    )
    assert middling.level is RiskLevel.MEDIUM

    extrapolating = assess_risk(
        n_obs=500, n_distinct_prices=50, price_cv=0.3, recommended_price=7.0,
        hist_price_min=3.0, hist_price_max=5.0, price_change_pct=0.4, cfg_risk=risk_cfg
    )
    assert extrapolating.level is RiskLevel.HIGH


def test_promotion_heavy_series_is_downgraded(cfg):
    """A promotion-saturated series cannot be LOW risk: the price effect is entangled."""
    r = assess_risk(
        n_obs=200, n_distinct_prices=20, price_cv=0.2, recommended_price=4.0,
        hist_price_min=3.0, hist_price_max=5.0, price_change_pct=0.02,
        cfg_risk=cfg.require("risk"), promotion_share=0.8,
    )
    assert r.level is RiskLevel.MEDIUM
    assert any("promotion" in n for n in r.notes)
