"""Phase F tests: the counterfactual simulator's two invariants.

1. only price-dependent features move with the candidate price;
2. cost is held fixed across the grid.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pricing_engine.features.build import PRICE_DEPENDENT_FEATURES
from pricing_engine.simulation.counterfactual import (
    SimulationError,
    demand_curve_diagnostics,
    simulate_many,
    simulate_price_grid,
)
from pricing_engine.simulation.price_grid import PriceGridError, build_price_grid


class RecordingLinearModel:
    """Q(p) = a - b p, recording every frame it was asked to score."""

    def __init__(self, a: float = 100.0, b: float = 10.0) -> None:
        self.a, self.b = a, b
        self.seen: list[pd.DataFrame] = []
        self.metadata = None

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        self.seen.append(frame.copy())
        return np.clip(self.a - self.b * frame["effective_unit_price"].to_numpy(dtype="float64"), 0, None)


@pytest.fixture
def context() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "upc": 111,
                "store": 2,
                "week": 300,
                "week_start_date": pd.Timestamp("1995-06-01"),
                "effective_unit_price": 4.00,
                "lag_price_1": 4.20,
                "lag_price_2": 4.20,
                "roll_mean_price_4": 4.10,
                "series_reference_price": 4.15,
                "decision_time_unit_cost": 2.50,
                "lag_move_1": 30.0,
                "roll_mean_move_4": 28.0,
                "recorded_promotion_flag": 0,
                "descrip": "TEST FLAKES",
            }
        ]
    )


# ---------------------------------------------------------------------------
# price grid
# ---------------------------------------------------------------------------
def test_grid_includes_current_price_and_respects_bounds():
    grid = build_price_grid(3.99, low=3.60, high=4.40, step=0.05, rounding=0.01)
    assert grid.prices.min() >= 3.60 - 1e-9
    assert grid.prices.max() <= 4.40 + 1e-9
    assert np.isclose(grid.prices, 3.99).any()


def test_grid_rounding_is_applied():
    grid = build_price_grid(3.99, low=3.60, high=4.40, step=0.07, rounding=0.05)
    remainder = np.abs(np.round(grid.prices / 0.05) * 0.05 - grid.prices)
    assert np.all(remainder < 1e-9)


def test_grid_rejects_empty_range():
    with pytest.raises(PriceGridError):
        build_price_grid(4.0, low=5.0, high=4.0, step=0.05)


def test_grid_rejects_invalid_current_price():
    with pytest.raises(PriceGridError):
        build_price_grid(0.0, low=1.0, high=2.0, step=0.05)


# ---------------------------------------------------------------------------
# simulation invariants
# ---------------------------------------------------------------------------
def test_price_features_recomputed_for_each_candidate(context):
    model = RecordingLinearModel()
    prices = np.array([3.50, 4.00, 4.50])
    simulate_price_grid(model, context, prices, unit_cost=2.5)
    scored = model.seen[0]
    assert np.allclose(scored["effective_unit_price"], prices)
    assert np.allclose(scored["log_price"], np.log(prices))
    assert np.allclose(scored["price_vs_last_week"], prices / 4.20 - 1.0)
    assert np.allclose(scored["price_vs_series_reference"], prices / 4.15 - 1.0)


def test_context_features_are_held_fixed(context):
    model = RecordingLinearModel()
    simulate_price_grid(model, context, np.array([3.0, 4.0, 5.0]), unit_cost=2.5)
    scored = model.seen[0]
    for col in ("lag_move_1", "roll_mean_move_4", "lag_price_1", "series_reference_price",
                "recorded_promotion_flag", "decision_time_unit_cost"):
        assert scored[col].nunique() == 1, col


def test_cost_is_held_fixed_across_price_grid(context):
    """The historical margin percentage must NOT be re-applied at a new price."""
    model = RecordingLinearModel()
    sim = simulate_price_grid(model, context, np.array([3.0, 4.0, 5.0]), unit_cost=2.5)
    assert sim["unit_cost"].nunique() == 1
    assert np.allclose(sim["unit_cost"], 2.5)
    # profit must follow (p - c) * q, so margin RATE varies with price
    expected = (sim["candidate_price"] - 2.5) * sim["predicted_units"]
    assert np.allclose(sim["expected_gross_profit"], expected)
    assert sim["expected_margin_rate"].nunique() > 1


def test_revenue_and_profit_formulas(context):
    model = RecordingLinearModel()
    sim = simulate_price_grid(model, context, np.array([3.0, 4.0]), unit_cost=2.0)
    assert np.allclose(sim["expected_revenue"], sim["candidate_price"] * sim["predicted_units"])
    assert np.allclose(
        sim["expected_gross_profit"], (sim["candidate_price"] - 2.0) * sim["predicted_units"]
    )


def test_missing_cost_yields_nan_profit_not_a_guess(context):
    model = RecordingLinearModel()
    sim = simulate_price_grid(model, context, np.array([3.0, 4.0]), unit_cost=None)
    assert sim["expected_gross_profit"].isna().all()
    assert sim["expected_revenue"].notna().all()


def test_single_batched_prediction_call(context):
    """Vectorisation contract: one model call for the whole grid."""
    model = RecordingLinearModel()
    simulate_price_grid(model, context, np.linspace(3, 5, 40), unit_cost=2.5)
    assert len(model.seen) == 1
    assert len(model.seen[0]) == 40


def test_simulate_many_scores_all_contexts_in_one_call(context):
    model = RecordingLinearModel()
    contexts = pd.concat([context, context.assign(store=5, effective_unit_price=3.5)], ignore_index=True)
    matrix = np.array([[3.5, 4.0, 4.5], [3.0, 3.5, 4.0]])
    out = simulate_many(model, contexts, matrix, unit_costs=np.array([2.5, 2.0]))
    assert len(model.seen) == 1
    assert len(out) == 6
    assert set(out["context_id"]) == {0, 1}
    assert np.allclose(out.loc[out["context_id"] == 1, "unit_cost"], 2.0)


def test_simulation_rejects_multi_row_context(context):
    model = RecordingLinearModel()
    with pytest.raises(SimulationError):
        simulate_price_grid(model, pd.concat([context, context]), np.array([4.0]))


def test_simulation_rejects_empty_grid(context):
    with pytest.raises(SimulationError):
        simulate_price_grid(RecordingLinearModel(), context, np.array([]))


# ---------------------------------------------------------------------------
# curve diagnostics
# ---------------------------------------------------------------------------
def test_diagnostics_detect_monotone_decreasing_curve(context):
    sim = simulate_price_grid(RecordingLinearModel(), context, np.linspace(3, 5, 20), unit_cost=2.5)
    d = demand_curve_diagnostics(sim)
    assert d["monotone_decreasing"] is True
    assert d["share_segments_increasing"] == 0.0
    assert d["median_local_elasticity"] < 0


def test_diagnostics_detect_wrong_sloped_curve():
    sim = pd.DataFrame(
        {
            "candidate_price": [3.0, 3.5, 4.0],
            "predicted_units": [10.0, 12.0, 15.0],
            "expected_revenue": [30.0, 42.0, 60.0],
        }
    )
    d = demand_curve_diagnostics(sim)
    assert d["monotone_decreasing"] is False
    assert d["share_segments_increasing"] == 1.0
    assert d["median_local_elasticity"] > 0


def test_price_dependent_feature_list_is_complete(context):
    """Every declared price-dependent feature must actually change with price."""
    model = RecordingLinearModel()
    simulate_price_grid(model, context, np.array([3.0, 5.0]), unit_cost=2.5)
    scored = model.seen[0]
    for col in PRICE_DEPENDENT_FEATURES:
        assert scored[col].nunique() == 2, col
