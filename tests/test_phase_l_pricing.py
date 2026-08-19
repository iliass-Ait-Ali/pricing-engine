"""Phase L tests: hybrid price response, elasticity shrinkage and risk gating."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pricing_engine.economics.elasticity_store import (
    ElasticityTable,
    build_elasticity_table,
    empirical_bayes_shrinkage,
    estimate_per_upc,
    fixed_elasticity_table,
)
from pricing_engine.models.hybrid import (
    HybridModelError,
    HybridPricingModel,
    PriceResponseMethod,
    attach_reference_price,
)
from pricing_engine.optimization.optimizer import DecisionState, ReasonCode, optimize_price
from pricing_engine.simulation.counterfactual import simulate_price_grid


class ConstantBaseline:
    """Baseline forecaster that ignores price entirely: Q_hat = 100 always.

    Perfect for testing the hybrid, because every price effect visible in the
    output must come from the elasticity term.
    """

    metadata = None
    prediction_cap_ = None

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.full(len(frame), 100.0)


class SteepMLModel:
    """A price-aware baseline whose own response is deliberately very steep."""

    metadata = None
    prediction_cap_ = None

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        p = frame["effective_unit_price"].to_numpy(dtype="float64")
        return 100.0 * np.power(p / 4.0, -5.0)


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


def table_with(upc: int, epsilon: float, pooled: float = -2.0) -> ElasticityTable:
    return ElasticityTable(
        pooled=pooled,
        pooled_se=0.05,
        products=pd.DataFrame(
            {"upc": [upc], "elasticity_final": [epsilon], "elasticity_source": ["shrunk_product"]}
        ),
        metadata={"kind": "test"},
    )


def wide_stats(price: float = 4.00) -> dict:
    return {"n_obs": 300, "n_distinct_prices": 30, "price_cv": 0.2,
            "price_min": price * 0.5, "price_max": price * 1.6}


def thin_stats(price: float = 4.00) -> dict:
    """Eligible, but too thin to act on automatically -> HIGH risk."""
    return {"n_obs": 45, "n_distinct_prices": 6, "price_cv": 0.2,
            "price_min": price * 0.5, "price_max": price * 1.6}


# ---------------------------------------------------------------------------
# hybrid mechanics
# ---------------------------------------------------------------------------
def test_hybrid_reproduces_the_baseline_at_the_reference_price(cfg):
    ctx = attach_reference_price(make_context(price=4.0))
    model = HybridPricingModel(ConstantBaseline(), method="shrunk",
                               elasticity_table=table_with(111, -2.5), cfg=cfg)
    assert model.predict(ctx)[0] == pytest.approx(100.0)


@pytest.mark.parametrize("epsilon", [-0.4, -1.0, -2.029, -3.5, -6.0])
@pytest.mark.parametrize("p0", [1.29, 4.00, 9.99])
def test_hybrid_equals_the_base_model_at_the_reference_price(cfg, epsilon, p0):
    """Q_hybrid(p0) == Q_ML(p0) for a genuinely price-sensitive base model.

    Phase M regression test. The docs used to summarise this property as "no
    forecast skill is lost", which overstated it: the equality holds AT the
    reference price and says nothing about any other price. This test pins the
    part that is actually true, across elasticities and price levels, using a
    base model whose own prediction depends on price (so the equality cannot
    hold by accident).
    """
    base = SteepMLModel()
    ctx = make_context(price=p0)
    hybrid = HybridPricingModel(
        base, method="shrunk", elasticity_table=table_with(111, epsilon), cfg=cfg
    )
    anchored = attach_reference_price(ctx, p0)
    assert hybrid.predict(anchored)[0] == pytest.approx(float(base.predict(ctx)[0]), rel=1e-12)


def test_hybrid_away_from_the_reference_price_follows_the_elasticity_not_the_base_model(cfg):
    """The complement of the test above: away from p0 the two disagree.

    This is why the "no forecast skill is lost" wording was wrong. The base
    model here has an implied elasticity of -5.0; the hybrid is given -2.0, and
    at a price 25% away from the reference the two predictions must differ
    substantially.
    """
    base = SteepMLModel()
    ctx = attach_reference_price(make_context(price=4.0), 4.0)
    hybrid = HybridPricingModel(
        base, method="shrunk", elasticity_table=table_with(111, -2.0), cfg=cfg
    )
    sim = simulate_price_grid(hybrid, ctx, np.array([5.0]))
    native = float(base.predict(make_context(price=5.0))[0])
    hybrid_units = float(sim["predicted_units"].iloc[0])
    assert hybrid_units == pytest.approx(100.0 * (5.0 / 4.0) ** -2.0)
    assert abs(hybrid_units - native) / native > 0.5


def test_hybrid_applies_the_constant_elasticity_formula(cfg):
    eps = -2.5
    model = HybridPricingModel(ConstantBaseline(), method="shrunk",
                               elasticity_table=table_with(111, eps), cfg=cfg)
    ctx = make_context(price=4.0)
    prices = np.array([3.0, 4.0, 5.0])
    sim = simulate_price_grid(model, ctx, prices, unit_cost=2.5)
    expected = 100.0 * np.power(prices / 4.0, eps)
    assert np.allclose(sim["predicted_units"].to_numpy(), expected)


def test_hybrid_uses_the_pooled_elasticity_when_the_product_is_unknown(cfg):
    model = HybridPricingModel(ConstantBaseline(), method="shrunk",
                               elasticity_table=table_with(999, -3.0, pooled=-1.5), cfg=cfg)
    ctx = make_context(price=4.0, upc=111)  # not in the table
    assert model.elasticity_for(ctx)[0] == pytest.approx(-1.5)
    assert model.elasticity_source(ctx)[0] == "pooled_fallback"


def test_hybrid_is_less_steep_than_a_steep_ml_model(cfg):
    """The whole point of Phase L: the price response stops being the forecaster's."""
    ctx = make_context(price=4.0)
    ml = HybridPricingModel(SteepMLModel(), method="ml", cfg=cfg)
    hybrid = HybridPricingModel(SteepMLModel(), method="pooled",
                                elasticity_table=fixed_elasticity_table(-2.0), cfg=cfg)
    prices = np.array([3.0, 4.0, 5.0])
    ml_units = simulate_price_grid(ml, ctx, prices)["predicted_units"].to_numpy()
    hy_units = simulate_price_grid(hybrid, ctx, prices)["predicted_units"].to_numpy()
    assert ml_units[1] == pytest.approx(hy_units[1])          # identical at p0
    assert ml_units[0] > hy_units[0]                          # ML reacts more to a cut
    assert ml_units[2] < hy_units[2]                          # and more to an increase


def test_hybrid_requires_a_reference_price(cfg):
    model = HybridPricingModel(ConstantBaseline(), method="pooled",
                               elasticity_table=fixed_elasticity_table(-2.0), cfg=cfg)
    with pytest.raises(HybridModelError):
        model.predict(make_context())  # no reference_price stamped


def test_hybrid_rejects_a_missing_elasticity_table(cfg):
    with pytest.raises(HybridModelError):
        HybridPricingModel(ConstantBaseline(), method="shrunk", elasticity_table=None, cfg=cfg)


def test_ml_method_needs_no_elasticity_table(cfg):
    model = HybridPricingModel(SteepMLModel(), method="ml", cfg=cfg)
    assert model.method is PriceResponseMethod.ML
    assert np.isnan(model.elasticity_for(make_context())[0])


# ---------------------------------------------------------------------------
# shrinkage
# ---------------------------------------------------------------------------
def test_shrinkage_pulls_imprecise_estimates_toward_pooled():
    estimates = np.array([-1.0, -3.0, -2.0, -2.5])
    precise = np.array([0.05, 0.05, 0.05, 0.05])
    noisy = np.array([2.0, 2.0, 2.0, 2.0])
    pooled = -2.0

    r_precise = empirical_bayes_shrinkage(estimates, precise, pooled)
    r_noisy = empirical_bayes_shrinkage(estimates, noisy, pooled)

    assert (r_precise.weights > r_noisy.weights).all()
    assert np.allclose(r_precise.shrunk, estimates, atol=0.05)
    assert np.abs(r_noisy.shrunk - pooled).max() < np.abs(estimates - pooled).max()


def test_shrinkage_weight_formula_matches_the_documented_definition():
    estimates = np.array([-1.0, -3.0, -2.0, -2.6, -1.4])
    se = np.array([0.1, 0.4, 0.2, 0.8, 0.3])
    pooled = -2.0
    res = empirical_bayes_shrinkage(estimates, se, pooled)
    assert np.allclose(res.weights, res.tau2 / (res.tau2 + se**2))
    assert np.allclose(res.shrunk, res.weights * estimates + (1 - res.weights) * res.prior_mean)
    assert res.prior_mean == pytest.approx(pooled)


def test_moment_estimator_reproduces_the_phase_l_formula():
    """The legacy method-of-moments option must still be exactly what it says."""
    estimates = np.array([-1.0, -3.0, -2.0, -2.6, -1.4])
    se = np.array([0.1, 0.4, 0.2, 0.8, 0.3])
    pooled = -2.0
    res = empirical_bayes_shrinkage(estimates, se, pooled, method="moment", prior_mean="estimated")
    expected = max(np.var(estimates, ddof=1) - np.mean(se**2), 0.0)
    assert res.tau2 == pytest.approx(expected)


def test_shrinkage_handles_all_missing_estimates():
    res = empirical_bayes_shrinkage(np.array([np.nan, np.nan]), np.array([np.nan, np.nan]), -2.0)
    assert np.allclose(res.shrunk, -2.0)
    assert np.allclose(res.weights, 0.0)
    assert res.tau2 == 0.0


def test_fixed_scenario_table_applies_one_elasticity_everywhere():
    t = fixed_elasticity_table(-1.9)
    assert np.allclose(t.epsilon_for([1, 2, 3]), -1.9)
    assert (t.source_for([1, 2]) == "pooled_fallback").all()


# ---------------------------------------------------------------------------
# estimator
# ---------------------------------------------------------------------------
@pytest.fixture
def elasticity_panel() -> pd.DataFrame:
    """Two products with known, different elasticities, plus one thin product."""
    rng = np.random.default_rng(3)
    rows = []
    for upc, true_e in ((1, -1.2), (2, -3.0)):
        for store in (2, 5):
            for week in range(1, 161):
                price = 3.0 * np.exp(rng.normal(0, 0.12))
                units = np.exp(4.0 + true_e * np.log(price) + rng.normal(0, 0.05))
                rows.append({"upc": upc, "store": store, "week": week,
                             "effective_unit_price": price, "move": units,
                             "recorded_promotion_flag": 0, "week_of_year": (week % 52) + 1})
    for week in range(1, 12):  # thin product, must be rejected
        rows.append({"upc": 3, "store": 2, "week": week, "effective_unit_price": 2.0 + 0.01 * week,
                     "move": 10.0, "recorded_promotion_flag": 0, "week_of_year": week})
    return pd.DataFrame(rows)


def test_estimate_per_upc_recovers_known_elasticities(elasticity_panel):
    out = estimate_per_upc(elasticity_panel, min_obs=100, min_distinct_prices=10)
    usable = out[out["usable"]].set_index("upc")
    assert usable.loc[1, "elasticity_raw"] == pytest.approx(-1.2, abs=0.15)
    assert usable.loc[2, "elasticity_raw"] == pytest.approx(-3.0, abs=0.15)
    assert set(["std_error", "t_stat", "ci_low", "ci_high", "n_obs", "n_distinct_prices"]).issubset(
        out.columns
    )


def test_estimate_per_upc_rejects_thin_products(elasticity_panel):
    out = estimate_per_upc(elasticity_panel, min_obs=100, min_distinct_prices=10)
    thin = out[out["upc"] == 3].iloc[0]
    assert not thin["usable"]
    assert thin["reject_reason"] == "insufficient_observations_or_price_variation"


def test_build_elasticity_table_marks_sources_and_records_the_window(elasticity_panel, cfg):
    table = build_elasticity_table(elasticity_panel, cfg=cfg, training_weeks=(1, 160))
    assert table.pooled < 0
    assert table.metadata["training_weeks_low"] == 1
    assert table.metadata["training_weeks_high"] == 160
    prods = table.products.set_index("upc")
    assert prods.loc[3, "elasticity_source"] == "pooled_fallback"
    assert table.epsilon_for([3])[0] == pytest.approx(table.pooled)
    assert (table.products["elasticity_final"] < 0).all()


def test_elasticity_table_roundtrips_through_csv(elasticity_panel, cfg, tmp_path):
    table = build_elasticity_table(elasticity_panel, cfg=cfg, training_weeks=(1, 160))
    path = table.save(tmp_path / "elasticity_table.csv")
    reloaded = ElasticityTable.load(path)
    assert reloaded.pooled == pytest.approx(table.pooled)
    assert np.allclose(reloaded.epsilon_for([1, 2, 3]), table.epsilon_for([1, 2, 3]))


# ---------------------------------------------------------------------------
# risk gating (Task 6)
# ---------------------------------------------------------------------------
def _hybrid(cfg):
    return HybridPricingModel(ConstantBaseline(), method="pooled",
                              elasticity_table=fixed_elasticity_table(-2.0), cfg=cfg)


def test_high_risk_is_never_actionable_under_the_standard_profile(cfg):
    rec = optimize_price(_hybrid(cfg), make_context(), cfg=cfg, policy_profile="standard",
                         series_stats=thin_stats())
    assert rec.risk_level == "HIGH"
    assert rec.decision == DecisionState.REVIEW_REQUIRED.value
    assert rec.actionable is False
    assert ReasonCode.HIGH_RISK_REVIEW_REQUIRED.value in rec.reason_codes


def test_high_risk_keeps_current_under_the_conservative_profile(cfg):
    rec = optimize_price(_hybrid(cfg), make_context(), cfg=cfg, policy_profile="conservative",
                         series_stats=thin_stats())
    assert rec.risk_level == "HIGH"
    assert rec.decision == DecisionState.KEEP_CURRENT.value
    assert rec.final_recommended_price == rec.current_price
    assert ReasonCode.HIGH_RISK_KEEP_CURRENT.value in rec.reason_codes


def test_only_the_demo_aggressive_profile_can_act_on_high_risk(cfg):
    rec = optimize_price(_hybrid(cfg), make_context(), cfg=cfg, policy_profile="aggressive",
                         series_stats=thin_stats())
    assert rec.risk_level == "HIGH"
    assert rec.decision == DecisionState.RECOMMEND_CHANGE.value
    assert rec.actionable is True


def test_medium_risk_gets_a_tighter_price_change_cap(cfg):
    medium = {"n_obs": 100, "n_distinct_prices": 10, "price_cv": 0.2,
              "price_min": 2.0, "price_max": 6.4}
    rec = optimize_price(_hybrid(cfg), make_context(), cfg=cfg, policy_profile="standard",
                         series_stats=medium)
    assert rec.risk_level == "MEDIUM"
    cap = float(cfg.policy("standard")["medium_risk_max_price_change_pct"])
    assert abs(rec.price_change_pct) <= cap + 1e-9
    assert ReasonCode.MEDIUM_RISK_CONSERVATIVE.value in rec.reason_codes


def test_low_risk_recommendation_is_actionable(cfg):
    rec = optimize_price(_hybrid(cfg), make_context(), cfg=cfg, policy_profile="standard",
                         series_stats=wide_stats())
    assert rec.risk_level == "LOW"
    assert rec.decision == DecisionState.RECOMMEND_CHANGE.value
    assert rec.actionable is True


def test_pooled_fallback_is_flagged_and_downgrades_risk(cfg):
    model = HybridPricingModel(ConstantBaseline(), method="shrunk",
                               elasticity_table=table_with(999, -2.5, pooled=-2.0), cfg=cfg)
    rec = optimize_price(model, make_context(upc=111), cfg=cfg, policy_profile="standard",
                         series_stats=wide_stats())
    assert rec.elasticity_source == "pooled_fallback"
    assert ReasonCode.POOLED_ELASTICITY_FALLBACK.value in rec.reason_codes
    assert rec.risk_level == "MEDIUM"  # never LOW on a borrowed elasticity


# ---------------------------------------------------------------------------
# language (Task 7)
# ---------------------------------------------------------------------------
def test_recommendation_uses_model_internal_uplift_naming(cfg):
    rec = optimize_price(_hybrid(cfg), make_context(), cfg=cfg, series_stats=wide_stats())
    d = rec.as_dict()
    assert "model_internal_estimated_profit_uplift_pct" in d
    assert "model_internal_estimated_revenue_uplift_pct" in d
    assert not any(k.startswith("model_estimated_") for k in d)


def test_recommendation_records_its_price_response_provenance(cfg):
    rec = optimize_price(_hybrid(cfg), make_context(), cfg=cfg, series_stats=wide_stats())
    assert rec.price_response_method == "pooled"
    assert rec.elasticity_used == pytest.approx(-2.0)
    assert rec.elasticity_source in {"pooled", "pooled_fallback", "shrunk_product"}
