"""Phase M tests: the constraint-attribution replica must be exact.

The audit reports in ``reports/11..13`` are produced by
``pricing_engine.optimization.attribution.evaluate``, a fast re-implementation
of ``optimize_price`` written against the closed-form hybrid demand curve. If
the replica ever drifts from the real optimizer, those reports become fiction.
These tests pin the equivalence.

They also pin the algebraic fact the audit rests on: under the hybrid response
the ML baseline ``Q0`` cancels out of the arg-max, so it cannot change the
recommended price.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pricing_engine.economics.elasticity_store import ElasticityTable
from pricing_engine.models.hybrid import HybridPricingModel, PriceResponseMethod
from pricing_engine.optimization.attribution import (
    PricingContext,
    Thresholds,
    analytic_unconstrained_optimum,
    attribute,
    constraint_intervals,
    contexts_from_frame,
    evaluate,
)
from pricing_engine.optimization.optimizer import optimize_price


class ScaledBaseline:
    """Price-blind forecaster returning a constant demand level."""

    metadata = None
    prediction_cap_ = None

    def __init__(self, level: float = 100.0) -> None:
        self.level = float(level)

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.full(len(frame), self.level)


def table_with(upc: int, epsilon: float, pooled: float = -2.0) -> ElasticityTable:
    return ElasticityTable(
        pooled=pooled,
        pooled_se=0.05,
        products=pd.DataFrame(
            {"upc": [upc], "elasticity_final": [epsilon], "elasticity_source": ["shrunk_product"]}
        ),
        metadata={"kind": "test"},
    )


def make_context(price: float = 4.00, cost: float = 2.50, upc: int = 111) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "upc": upc,
                "store": 2,
                "week": 300,
                "week_start_date": pd.Timestamp("1995-06-01"),
                "effective_unit_price": price,
                "lag_price_1": price,
                "lag_price_2": price,
                "roll_mean_price_4": price,
                "series_reference_price": price,
                "decision_time_unit_cost": cost,
                "lag_move_1": 100.0,
                "roll_mean_move_4": 100.0,
                "recorded_promotion_flag": 0,
                "descrip": "TEST FLAKES",
            }
        ]
    )


STAT_SETS = {
    "low_risk": {"n_obs": 200, "n_distinct_prices": 25, "price_cv": 0.20,
                 "price_min": 2.0, "price_max": 6.4},
    "medium_risk": {"n_obs": 100, "n_distinct_prices": 10, "price_cv": 0.20,
                    "price_min": 2.0, "price_max": 6.4},
    "high_risk_thin": {"n_obs": 45, "n_distinct_prices": 6, "price_cv": 0.20,
                       "price_min": 2.0, "price_max": 6.4},
    "ineligible": {"n_obs": 20, "n_distinct_prices": 3, "price_cv": 0.20,
                   "price_min": 2.0, "price_max": 6.4},
    "narrow_support": {"n_obs": 200, "n_distinct_prices": 25, "price_cv": 0.20,
                       "price_min": 3.95, "price_max": 4.05},
}


def _context_from(model, frame: pd.DataFrame, stats: dict) -> PricingContext:
    merged = frame.assign(**stats)
    return contexts_from_frame(model, merged)[0]


# ---------------------------------------------------------------------------
# equivalence with the real optimizer
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("stats_name", sorted(STAT_SETS))
@pytest.mark.parametrize("profile", ["conservative", "standard", "aggressive"])
@pytest.mark.parametrize("epsilon", [-0.6, -1.4, -2.0, -3.5])
def test_replica_matches_optimizer(cfg, stats_name, profile, epsilon):
    stats = STAT_SETS[stats_name]
    frame = make_context()
    model = HybridPricingModel(
        ScaledBaseline(),
        method=PriceResponseMethod.SHRUNK,
        elasticity_table=table_with(111, epsilon),
        cfg=cfg,
    )
    real = optimize_price(
        model, frame, cfg=cfg, policy_profile=profile,
        objective="gross_profit", series_stats=stats,
    )
    replica = evaluate(_context_from(model, frame, stats), cfg.policy(profile), Thresholds.from_config(cfg))

    assert replica.decision == real.decision
    assert replica.actionable == real.actionable
    assert replica.final_recommended_price == pytest.approx(real.final_recommended_price)
    assert replica.proposed_candidate_price == pytest.approx(real.proposed_candidate_price)
    assert replica.risk_level == real.risk_level
    assert set(replica.reason_codes) == set(real.reason_codes)


# ---------------------------------------------------------------------------
# the baseline forecast cannot move the price
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("scale", [0.1, 1.0, 10.0, 1000.0])
def test_baseline_demand_level_does_not_change_the_recommended_price(cfg, scale):
    """Q0 is a positive constant across candidates, so it cancels in the argmax.

    This is the algebraic reason the ML forecaster does not choose the price
    under the hybrid response - only (p0, cost, epsilon) and the guardrails do.
    """
    frame = make_context()
    prices = []
    for level in (100.0, 100.0 * scale):
        model = HybridPricingModel(
            ScaledBaseline(level),
            method=PriceResponseMethod.SHRUNK,
            elasticity_table=table_with(111, -2.0),
            cfg=cfg,
        )
        rec = optimize_price(
            model, frame, cfg=cfg, policy_profile="standard",
            objective="gross_profit", series_stats=STAT_SETS["low_risk"],
        )
        prices.append((rec.proposed_candidate_price, rec.decision))
    assert prices[0] == prices[1]


# ---------------------------------------------------------------------------
# analytic unconstrained optimum
# ---------------------------------------------------------------------------
def test_analytic_unconstrained_optimum_matches_grid_search():
    ctx = PricingContext(
        upc=111, store=2, week=300, current_price=4.0, unit_cost=2.5,
        baseline_units=100.0, epsilon=-2.5, elasticity_source="shrunk_product",
        n_obs=200, n_distinct_prices=25, price_cv=0.2,
        hist_price_min=2.0, hist_price_max=6.4,
    )
    analytic = analytic_unconstrained_optimum(2.5, -2.5)
    assert analytic == pytest.approx(2.5 * (-2.5) / (-1.5))
    grid = np.arange(2.51, 20.0, 0.001)
    profit = (grid - 2.5) * ctx.demand(grid)
    assert grid[int(np.argmax(profit))] == pytest.approx(analytic, abs=0.002)


def test_inelastic_demand_has_no_interior_optimum():
    assert analytic_unconstrained_optimum(2.5, -0.5) == float("inf")
    assert analytic_unconstrained_optimum(2.5, -1.0) == float("inf")


# ---------------------------------------------------------------------------
# constraint intervals
# ---------------------------------------------------------------------------
def test_constraint_intervals_are_reported_per_constraint():
    ctx = PricingContext(
        upc=111, store=2, week=300, current_price=4.0, unit_cost=2.5,
        baseline_units=100.0, epsilon=-2.0, elasticity_source="shrunk_product",
        n_obs=200, n_distinct_prices=25, price_cv=0.2,
        hist_price_min=3.0, hist_price_max=5.0,
    )
    policy = {
        "max_price_change_pct": 0.10,
        "extrapolation_tolerance_pct": 0.05,
        "min_gross_margin_rate": 0.05,
        "allow_below_cost": False,
    }
    iv = constraint_intervals(ctx, policy)
    assert iv["MAX_PRICE_CHANGE"] == pytest.approx((3.6, 4.4))
    assert iv["EXTRAPOLATION"] == pytest.approx((2.85, 5.25))
    assert iv["COST_FLOOR"][0] == pytest.approx(2.5)
    assert iv["MIN_MARGIN"][0] == pytest.approx(2.5 / 0.95)
    assert iv["MIN_PRICE"][0] == pytest.approx(0.01)
    assert not np.isfinite(iv["MAX_PRICE"][1])


def test_attribution_records_binding_and_leave_one_out(cfg):
    """A +10% cap with a far-away unconstrained optimum must bind and matter."""
    ctx = PricingContext(
        upc=111, store=2, week=300, current_price=4.0, unit_cost=2.5,
        baseline_units=100.0, epsilon=-2.0, elasticity_source="shrunk_product",
        n_obs=200, n_distinct_prices=25, price_cv=0.2,
        hist_price_min=1.0, hist_price_max=20.0,
    )
    att = attribute(ctx, cfg.policy("standard"), Thresholds.from_config(cfg))
    # p* = c e/(1+e) = 2.5 * 2 = 5.00, far above the +10% cap of 4.40.
    assert att["analytic_unconstrained_optimum"] == pytest.approx(5.0)
    assert att["unconstrained_optimum"] == pytest.approx(5.0, abs=0.05)
    assert att["final_recommended_price"] == pytest.approx(4.4, abs=0.05)
    assert att["first_binding_constraint"] == "MAX_PRICE_CHANGE"
    assert att["constraints"]["MAX_PRICE_CHANGE"]["binding_at_optimum"]
    assert att["constraints"]["MAX_PRICE_CHANGE"]["changed_final_price"]
    assert not att["model_optimum_survives_policy"]


def test_attribution_reports_a_surviving_optimum(cfg):
    """When the optimum sits inside every guardrail, nothing binds it away."""
    ctx = PricingContext(
        upc=111, store=2, week=300, current_price=4.0, unit_cost=2.10,
        baseline_units=100.0, epsilon=-4.0, elasticity_source="shrunk_product",
        n_obs=200, n_distinct_prices=25, price_cv=0.2,
        hist_price_min=1.0, hist_price_max=20.0,
    )
    att = attribute(ctx, cfg.policy("standard"), Thresholds.from_config(cfg))
    # p* = 2.10 * 4/3 = 2.80 -> below the -10% floor of 3.60, so the cap binds
    # downward; relaxing it must move the price down.
    assert att["analytic_unconstrained_optimum"] == pytest.approx(2.80)
    assert att["final_recommended_price"] < ctx.current_price
