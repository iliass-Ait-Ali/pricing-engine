"""Phase B/D tests: economics primitives and elasticity mathematics."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pricing_engine.economics.elasticity import (
    arc_elasticity,
    fit_loglog,
    pairwise_arc_elasticities,
    prepare_loglog_frame,
)
from pricing_engine.economics.metrics import (
    EconomicsError,
    eligible_series,
    gross_margin_rate,
    gross_profit,
    price_change_pct,
    price_index,
    price_variation_summary,
    revenue,
    unit_gross_margin,
)


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------
def test_revenue_and_profit_identities():
    p = np.array([2.0, 4.0])
    q = np.array([10.0, 5.0])
    c = np.array([1.0, 3.0])
    assert np.allclose(revenue(p, q), [20.0, 20.0])
    assert np.allclose(unit_gross_margin(p, c), [1.0, 1.0])
    assert np.allclose(gross_profit(p, q, c), [10.0, 5.0])
    assert np.allclose(gross_margin_rate(p, c), [0.5, 0.25])


def test_gross_profit_equals_revenue_times_margin_rate():
    p, q, c = np.array([3.0]), np.array([7.0]), np.array([2.1])
    assert np.allclose(gross_profit(p, q, c), revenue(p, q) * gross_margin_rate(p, c))


def test_margin_rate_is_nan_at_zero_price_not_infinite():
    rate = gross_margin_rate(np.array([0.0, 2.0]), np.array([1.0, 1.0]))
    assert np.isnan(rate[0])
    assert rate[1] == pytest.approx(0.5)


def test_price_change_and_index():
    assert price_change_pct(np.array([1.10]), np.array([1.00]))[0] == pytest.approx(0.10)
    assert np.isnan(price_change_pct(np.array([1.10]), np.array([0.0]))[0])
    assert price_index(np.array([4.0]), np.array([2.0]))[0] == pytest.approx(2.0)


def test_price_variation_summary_counts_changes(panel):
    summary = price_variation_summary(panel, ["upc", "store"])
    assert len(summary) == 4
    assert (summary["n_obs"] == 60).all()
    assert (summary["n_price_changes"] > 0).all()
    assert (summary["price_cv"] > 0).all()
    assert np.allclose(
        summary["price_range_pct"],
        (summary["price_max"] - summary["price_min"]) / summary["price_mean"],
    )


def test_price_variation_summary_requires_columns():
    with pytest.raises(EconomicsError):
        price_variation_summary(pd.DataFrame({"upc": [1]}), ["upc"])


def test_eligibility_screen(panel):
    summary = price_variation_summary(panel, ["upc", "store"])
    strict = eligible_series(summary, {"min_observations": 40, "min_distinct_prices": 5, "min_price_cv": 0.05})
    impossible = eligible_series(summary, {"min_observations": 10_000, "min_distinct_prices": 5, "min_price_cv": 0.05})
    assert strict.all()
    assert not impossible.any()


# ---------------------------------------------------------------------------
# arc elasticity
# ---------------------------------------------------------------------------
def test_arc_elasticity_matches_hand_computation():
    # Q: 100 -> 80, P: 1.00 -> 1.20
    e = arc_elasticity(100.0, 80.0, 1.00, 1.20)
    expected = ((80 - 100) / 90) / ((1.20 - 1.00) / 1.10)
    assert float(e) == pytest.approx(expected)
    assert float(e) < 0


def test_arc_elasticity_is_nan_when_price_does_not_move():
    assert np.isnan(arc_elasticity(100.0, 80.0, 2.0, 2.0))


def test_arc_elasticity_is_nan_for_zero_midpoints_and_invalid_quantities():
    assert np.isnan(arc_elasticity(0.0, 0.0, 1.0, 1.2))
    assert np.isnan(arc_elasticity(10.0, -5.0, 1.0, 1.2))
    assert np.isnan(arc_elasticity(10.0, 8.0, 0.0, 0.0))


def test_arc_elasticity_ignores_negligible_price_changes():
    assert np.isnan(arc_elasticity(100.0, 99.0, 1.0, 1.0 + 1e-9))


def test_pairwise_elasticities_use_consecutive_weeks_only():
    df = pd.DataFrame(
        {
            "upc": [1, 1, 1],
            "store": [1, 1, 1],
            "week": [1, 2, 10],  # week 10 is not consecutive with week 2
            "effective_unit_price": [2.0, 2.2, 2.4],
            "move": [100.0, 80.0, 70.0],
        }
    )
    out = pairwise_arc_elasticities(df, ["upc", "store"])
    assert len(out) == 1
    assert out["week"].iloc[0] == 2


# ---------------------------------------------------------------------------
# log-log regression
# ---------------------------------------------------------------------------
def test_prepare_loglog_drops_or_transforms_zeros():
    df = pd.DataFrame({"effective_unit_price": [1.0, 2.0, 3.0], "move": [10.0, 0.0, 5.0]})
    dropped, note = prepare_loglog_frame(df, zero_handling="drop")
    assert len(dropped) == 2 and "dropped 1" in note
    kept, note2 = prepare_loglog_frame(df, zero_handling="plus1")
    assert len(kept) == 3 and "log(1 + units)" in note2


def test_prepare_loglog_rejects_unknown_handling():
    df = pd.DataFrame({"effective_unit_price": [1.0], "move": [1.0]})
    with pytest.raises(ValueError):
        prepare_loglog_frame(df, zero_handling="magic")


def test_fit_loglog_recovers_a_known_elasticity():
    """Simulate Q = k * P^e exactly and check the estimate returns e."""
    rng = np.random.default_rng(0)
    n = 4000
    price = np.exp(rng.normal(1.0, 0.25, n))
    true_e = -1.8
    units = np.exp(4.0 + true_e * np.log(price) + rng.normal(0, 0.05, n))
    df = pd.DataFrame({"effective_unit_price": price, "move": units})
    fit = fit_loglog(df, name="synthetic")
    assert fit.price_elasticity == pytest.approx(true_e, abs=0.05)
    assert fit.ci_low < true_e < fit.ci_high


def test_fit_loglog_with_absorbed_fixed_effects_removes_group_level_confounding():
    """Two products with different price levels and demand levels.

    Pooled, the association is flat or wrong-signed; within products it is the
    true negative slope. This is the mechanism behind the naive-vs-fixed-effects
    gap reported in reports/03_ELASTICITY_ANALYSIS.md.
    """
    rng = np.random.default_rng(1)
    rows = []
    for upc, (base_p, base_q) in enumerate([(1.0, 3.0), (2.0, 6.0)]):
        p = base_p * np.exp(rng.normal(0, 0.10, 2000))
        q = np.exp(base_q - 1.5 * np.log(p / base_p) + rng.normal(0, 0.05, 2000))
        rows.append(pd.DataFrame({"upc": upc, "effective_unit_price": p, "move": q}))
    df = pd.concat(rows, ignore_index=True)

    naive = fit_loglog(df, name="naive")
    within = fit_loglog(df, name="fixed effects", absorb=["upc"])
    assert within.price_elasticity == pytest.approx(-1.5, abs=0.1)
    assert naive.price_elasticity > within.price_elasticity  # confounded upward
