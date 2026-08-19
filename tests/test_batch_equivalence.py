"""Equivalence of the per-context and vectorised batch recommendation paths.

`optimize_price_batch` exists purely for speed: it must never change a proposed
price, a final price, a decision state, an actionable flag, an elasticity
source, a risk level, a reason code or the expected economics. These tests
compare the two paths field by field - exact equality for every discrete field,
tight tolerance for the floating-point economics.

The fixtures are deterministic and cover the branch structure on purpose:
series that fail the history screen, series that fail the price-variation
screen, contexts without a usable cost, LOW / MEDIUM / HIGH risk, pooled and
per-product elasticity provenance, and all three policy profiles.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pricing_engine.economics.elasticity_store import ElasticityTable
from pricing_engine.models.hybrid import HybridPricingModel
from pricing_engine.optimization.optimizer import (
    Recommendation,
    optimize_price,
    optimize_price_batch,
)


# ---------------------------------------------------------------------------
# deterministic fixtures
# ---------------------------------------------------------------------------
class ConstantElasticityModel:
    """Q(p) = k * p^e - a closed-form, row-independent demand fixture."""

    def __init__(self, k: float = 1000.0, e: float = -2.0) -> None:
        self.k, self.e = k, e
        self.metadata = None

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        p = frame["effective_unit_price"].to_numpy(dtype="float64")
        return self.k * np.power(p, self.e)


class BaselineWithContext(ConstantElasticityModel):
    """Baseline demand that also depends on non-price context columns.

    Row-independent, but it makes an accidental context mix-up between the two
    paths (wrong row repeated, wrong ordering) visible in the numbers.
    """

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        p = frame["effective_unit_price"].to_numpy(dtype="float64")
        lag = frame["lag_move_1"].to_numpy(dtype="float64")
        return self.k * np.power(p, self.e) * (1.0 + lag / 100.0)


def elasticity_table(product_upcs: list[int]) -> ElasticityTable:
    """Per-product elasticities for some UPCs; the rest fall back to pooled."""
    return ElasticityTable(
        pooled=-2.0,
        pooled_se=0.05,
        products=pd.DataFrame(
            {
                "upc": product_upcs,
                "elasticity_final": [-1.6 - 0.2 * i for i in range(len(product_upcs))],
                "elasticity_source": ["shrunk_product"] * len(product_upcs),
            }
        ),
        metadata={"kind": "test"},
    )


def make_contexts(n: int = 24) -> tuple[pd.DataFrame, list[dict]]:
    """A pool of contexts that exercises every pre-simulation branch."""
    rows: list[dict] = []
    stats: list[dict] = []
    for i in range(n):
        price = round(2.0 + 0.35 * (i % 7), 2)
        cost = round(price * (0.55 + 0.05 * (i % 3)), 4)
        rows.append(
            {
                "upc": 1000 + (i % 6),
                "store": 10 + (i % 4),
                "week": 399,
                "week_start_date": pd.Timestamp("1997-05-01"),
                "effective_unit_price": price,
                "lag_price_1": round(price * (1.0 + 0.02 * ((i % 5) - 2)), 4),
                "lag_price_2": price,
                "roll_mean_price_4": round(price * 0.99, 4),
                "series_reference_price": round(price * 1.02, 4),
                # every 8th context has no usable decision-time cost
                "decision_time_unit_cost": np.nan if i % 8 == 0 else cost,
                "lag_move_1": 20.0 + 3.0 * (i % 9),
                "roll_mean_move_4": 25.0 + (i % 4),
                "recorded_promotion_flag": i % 2,
                "descrip": f"TEST CEREAL {i % 6}",
            }
        )
        stats.append(
            {
                # i % 5 == 0 fails the history screen, i % 6 == 0 fails the
                # price-variation screen, i % 7 == 0 fails the CV screen.
                "n_obs": 12 if i % 5 == 0 else (45 if i % 3 == 0 else 400),
                "n_distinct_prices": 2 if i % 6 == 0 else (6 if i % 3 == 0 else 25),
                "price_cv": 0.001 if i % 7 == 0 else 0.20,
                "price_min": round(price * 0.7, 4),
                "price_max": round(price * 1.3, 4),
            }
        )
    return pd.DataFrame(rows), stats


@pytest.fixture
def contexts_and_stats() -> tuple[pd.DataFrame, list[dict]]:
    return make_contexts()


@pytest.fixture
def hybrid_model(cfg) -> HybridPricingModel:
    """The production-shaped model: ML baseline + explicit elasticity response."""
    return HybridPricingModel(
        BaselineWithContext(),
        method="shrunk",
        elasticity_table=elasticity_table([1000, 1001, 1002]),
        cfg=cfg,
    )


# ---------------------------------------------------------------------------
# field-by-field comparison
# ---------------------------------------------------------------------------
DISCRETE_FIELDS = [
    "upc",
    "store",
    "decision_week",
    "decision_week_start_date",
    "objective",
    "policy_profile",
    "model_version",
    "price_response_method",
    "elasticity_source",
    "decision",
    "actionable",
    "risk_level",
    "reason_codes",
    "risk_notes",
    "product_description",
]

FLOAT_FIELDS = [
    "elasticity_used",
    "current_price",
    "proposed_candidate_price",
    "final_recommended_price",
    "proposed_price_change_pct",
    "price_change_pct",
    "predicted_units_current",
    "predicted_units_recommended",
    "expected_revenue_current",
    "expected_revenue_recommended",
    "expected_gross_profit_current",
    "expected_gross_profit_recommended",
    "model_internal_estimated_profit_uplift_pct",
    "model_internal_estimated_revenue_uplift_pct",
    "realisable_profit_uplift_pct",
    "unit_cost_used",
]

# Both paths do float64 arithmetic on the same inputs and differ only in how
# many rows are handed to the model at once.
RTOL = 1e-12
ATOL = 1e-12


def _approx_equal(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) and isinstance(b, float) and (np.isnan(a) or np.isnan(b)):
        return np.isnan(a) and np.isnan(b)
    return a == pytest.approx(b, rel=RTOL, abs=ATOL)


def assert_recommendations_equal(a: Recommendation, b: Recommendation, *, where: str) -> None:
    da, db = a.as_dict(), b.as_dict()
    for name in DISCRETE_FIELDS:
        assert da[name] == db[name], f"{where}: discrete field {name!r} differs"
    for name in FLOAT_FIELDS:
        assert _approx_equal(da[name], db[name]), (
            f"{where}: field {name!r} differs ({da[name]} vs {db[name]})"
        )
    assert da["constraints"] == db["constraints"], f"{where}: constraints differ"
    ka, kb = da["diagnostics"], db["diagnostics"]
    assert set(ka) == set(kb), f"{where}: diagnostics keys differ"
    for key in ka:
        va, vb = ka[key], kb[key]
        if isinstance(va, float) and isinstance(vb, float):
            assert _approx_equal(va, vb), f"{where}: diagnostics {key!r} differs"
        else:
            assert va == vb, f"{where}: diagnostics {key!r} differs"


def _loop(model, contexts, stats, cfg, **kwargs) -> list[Recommendation]:
    return [
        optimize_price(model, contexts.iloc[[i]], cfg=cfg, series_stats=stats[i], **kwargs)
        for i in range(len(contexts))
    ]


# ---------------------------------------------------------------------------
# equivalence
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("profile", ["conservative", "standard", "aggressive"])
def test_batch_matches_the_loop_for_every_policy_profile(
    hybrid_model, contexts_and_stats, cfg, profile
):
    contexts, stats = contexts_and_stats
    expected = _loop(hybrid_model, contexts, stats, cfg, policy_profile=profile)
    got = optimize_price_batch(
        hybrid_model, contexts, cfg=cfg, series_stats=stats, policy_profile=profile
    )
    assert len(got) == len(expected)
    for i, (a, b) in enumerate(zip(expected, got, strict=True)):
        assert_recommendations_equal(a, b, where=f"{profile} row {i}")


def test_batch_matches_the_loop_for_the_revenue_objective(hybrid_model, contexts_and_stats, cfg):
    contexts, stats = contexts_and_stats
    kwargs = {"policy_profile": "standard", "objective": "revenue"}
    expected = _loop(hybrid_model, contexts, stats, cfg, **kwargs)
    got = optimize_price_batch(hybrid_model, contexts, cfg=cfg, series_stats=stats, **kwargs)
    for i, (a, b) in enumerate(zip(expected, got, strict=True)):
        assert_recommendations_equal(a, b, where=f"revenue row {i}")


def test_batch_matches_the_loop_for_the_native_ml_response(contexts_and_stats, cfg):
    contexts, stats = contexts_and_stats
    model = HybridPricingModel(BaselineWithContext(), method="ml", cfg=cfg)
    expected = _loop(model, contexts, stats, cfg, policy_profile="standard")
    got = optimize_price_batch(
        model, contexts, cfg=cfg, series_stats=stats, policy_profile="standard"
    )
    for i, (a, b) in enumerate(zip(expected, got, strict=True)):
        assert_recommendations_equal(a, b, where=f"ml row {i}")


def test_batch_matches_the_loop_without_series_statistics(hybrid_model, contexts_and_stats, cfg):
    """No eligibility statistics: every context reaches the simulation stage."""
    contexts, _ = contexts_and_stats
    expected = [
        optimize_price(hybrid_model, contexts.iloc[[i]], cfg=cfg, policy_profile="standard")
        for i in range(len(contexts))
    ]
    got = optimize_price_batch(hybrid_model, contexts, cfg=cfg, policy_profile="standard")
    for i, (a, b) in enumerate(zip(expected, got, strict=True)):
        assert_recommendations_equal(a, b, where=f"no-stats row {i}")


def test_batch_covers_the_decision_states_it_claims_to(hybrid_model, contexts_and_stats, cfg):
    """The fixture must actually exercise the branches, not just agree trivially."""
    contexts, stats = contexts_and_stats
    got = optimize_price_batch(
        hybrid_model, contexts, cfg=cfg, series_stats=stats, policy_profile="standard"
    )
    decisions = {r.decision for r in got}
    sources = {r.elasticity_source for r in got}
    codes = {c for r in got for c in r.reason_codes}
    assert {"RECOMMEND_CHANGE", "KEEP_CURRENT"} <= decisions
    assert {"shrunk_product", "pooled_fallback"} <= sources
    assert {"INSUFFICIENT_HISTORY", "INSUFFICIENT_PRICE_VARIATION"} <= codes


def test_batch_preserves_input_order(hybrid_model, contexts_and_stats, cfg):
    contexts, stats = contexts_and_stats
    got = optimize_price_batch(hybrid_model, contexts, cfg=cfg, series_stats=stats)
    assert [r.upc for r in got] == [int(u) for u in contexts["upc"]]
    assert [r.store for r in got] == [int(s) for s in contexts["store"]]


def test_batch_ignores_the_input_index(hybrid_model, contexts_and_stats, cfg):
    """A non-default index must not change a single recommendation."""
    contexts, stats = contexts_and_stats
    shifted = contexts.set_index(np.arange(500, 500 + len(contexts)))
    expected = optimize_price_batch(hybrid_model, contexts, cfg=cfg, series_stats=stats)
    got = optimize_price_batch(hybrid_model, shifted, cfg=cfg, series_stats=stats)
    for i, (a, b) in enumerate(zip(expected, got, strict=True)):
        assert_recommendations_equal(a, b, where=f"reindexed row {i}")


def test_chunking_does_not_change_results(hybrid_model, contexts_and_stats, cfg):
    """The simulation-row budget only changes how many model calls happen."""
    contexts, stats = contexts_and_stats
    big = optimize_price_batch(hybrid_model, contexts, cfg=cfg, series_stats=stats)
    small = optimize_price_batch(
        hybrid_model, contexts, cfg=cfg, series_stats=stats, max_simulation_rows=1
    )
    for i, (a, b) in enumerate(zip(big, small, strict=True)):
        assert_recommendations_equal(a, b, where=f"chunked row {i}")


def test_batch_uses_fewer_model_calls_than_the_loop(hybrid_model, contexts_and_stats, cfg):
    """The whole point of the batch path: fewer, larger model calls."""
    contexts, stats = contexts_and_stats
    calls = {"loop": 0, "batch": 0}

    class Counting:
        def __init__(self, inner, key):
            self._inner, self._key = inner, key

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def predict(self, frame):
            calls[self._key] += 1
            return self._inner.predict(frame)

    _loop(Counting(hybrid_model, "loop"), contexts, stats, cfg, policy_profile="standard")
    optimize_price_batch(
        Counting(hybrid_model, "batch"),
        contexts,
        cfg=cfg,
        series_stats=stats,
        policy_profile="standard",
    )
    assert calls["batch"] < calls["loop"]
    assert calls["batch"] <= len(contexts)


def test_empty_batch_returns_an_empty_list(hybrid_model, contexts_and_stats, cfg):
    contexts, _ = contexts_and_stats
    assert optimize_price_batch(hybrid_model, contexts.iloc[:0], cfg=cfg) == []


def test_batch_rejects_a_non_positive_current_price(hybrid_model, contexts_and_stats, cfg):
    from pricing_engine.optimization.optimizer import OptimizerError

    contexts, stats = contexts_and_stats
    broken = contexts.copy()
    broken.loc[3, "effective_unit_price"] = 0.0
    with pytest.raises(OptimizerError):
        optimize_price_batch(hybrid_model, broken, cfg=cfg, series_stats=stats)
