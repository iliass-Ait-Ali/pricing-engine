"""Phase L - unit-level proof that cost information is never read from the future.

The real-data version of this audit is `scripts/audit_cost_leakage.py`
(reports/10_COST_LEAKAGE_AUDIT.md). These tests run on a deterministic
synthetic panel so they execute in CI without the licensed dataset.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pricing_engine.economics.elasticity_store import fixed_elasticity_table
from pricing_engine.features.build import build_feature_table
from pricing_engine.models.hybrid import HybridPricingModel
from pricing_engine.optimization.optimizer import optimize_price

POISON_WEEK = 40
COMPARE_WEEK = 35


@pytest.fixture
def canonical_panel() -> pd.DataFrame:
    """A canonical-shaped panel with a drifting cost, so poisoning is detectable."""
    rng = np.random.default_rng(11)
    rows = []
    for upc in (111, 222):
        for store in (2, 5):
            for week in range(1, 61):
                price = 3.0 * (1 + 0.1 * np.sin(week / 4.0))
                margin = 0.25 + 0.02 * np.sin(week / 7.0)
                units = max(1.0, round(np.exp(3.5 - 2.0 * np.log(price) + rng.normal(0, 0.05))))
                rows.append(
                    {
                        "upc": upc,
                        "store": store,
                        "week": week,
                        "week_start_date": pd.Timestamp("1990-01-04") + pd.Timedelta(weeks=week - 1),
                        "move": float(units),
                        "qty": 1.0,
                        "price": round(price, 2),
                        "effective_unit_price": round(price, 2),
                        "profit": 100 * margin,
                        "gross_margin_rate": margin,
                        "estimated_unit_aac": round(price * (1 - margin), 4),
                        "revenue": round(price, 2) * units,
                        "gross_profit": round(price, 2) * units * margin,
                        "sale": pd.NA,
                        "recorded_promotion_flag": 0,
                        "recorded_promotion_type": "NONE_RECORDED",
                        "ok": 1,
                        "descrip": "TEST FLAKES",
                        "size": "12 OZ",
                        "com_code": 311,
                        "year": 1990,
                        "month": 1,
                        "quarter": 1,
                        "week_of_year": (week % 52) + 1,
                    }
                )
    return pd.DataFrame(rows)


def poison(panel: pd.DataFrame, from_week: int = POISON_WEEK) -> pd.DataFrame:
    """Corrupt every cost-bearing column from ``from_week`` onward."""
    out = panel.copy()
    mask = out["week"] >= from_week
    out.loc[mask, "estimated_unit_aac"] = 999.0
    out.loc[mask, "gross_margin_rate"] = -9.0
    out.loc[mask, "profit"] = -900.0
    out.loc[mask, "gross_profit"] = -12345.0
    return out


def test_decision_cost_equals_the_previous_weeks_aac(canonical_panel, cfg):
    feats = build_feature_table(canonical_panel, cfg=cfg).sort_values(["upc", "store", "week"])
    one = feats[(feats["upc"] == 111) & (feats["store"] == 2)]
    assert np.allclose(
        one["decision_time_unit_cost"].to_numpy(dtype="float64")[1:],
        one["estimated_unit_aac"].to_numpy(dtype="float64")[:-1],
        equal_nan=True,
    )
    # and it is NEVER the same week's value (the panel's cost actually moves)
    same_week = np.isclose(
        one["decision_time_unit_cost"].to_numpy(dtype="float64")[1:],
        one["estimated_unit_aac"].to_numpy(dtype="float64")[1:],
    )
    assert not same_week.all()


def test_future_cost_poisoning_does_not_change_earlier_features(canonical_panel, cfg):
    clean = build_feature_table(canonical_panel, cfg=cfg).sort_values(["upc", "store", "week"])
    dirty = build_feature_table(poison(canonical_panel), cfg=cfg).sort_values(["upc", "store", "week"])

    cols = ["decision_time_unit_cost", "lag_price_1", "lag_move_1", "roll_mean_move_4",
            "series_reference_price", "effective_unit_price"]
    before_clean = clean[clean["week"] <= POISON_WEEK][cols].reset_index(drop=True)
    before_dirty = dirty[dirty["week"] <= POISON_WEEK][cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(before_clean, before_dirty)


def test_the_poisoning_test_has_teeth(canonical_panel, cfg):
    """Week POISON_WEEK + 1 MUST see the corrupted cost, or the test proves nothing."""
    clean = build_feature_table(canonical_panel, cfg=cfg)
    dirty = build_feature_table(poison(canonical_panel), cfg=cfg)
    after_clean = clean[clean["week"] == POISON_WEEK + 1]["decision_time_unit_cost"].to_numpy()
    after_dirty = dirty[dirty["week"] == POISON_WEEK + 1]["decision_time_unit_cost"].to_numpy()
    assert not np.allclose(after_clean, after_dirty)
    assert np.allclose(after_dirty, 999.0)


def test_future_cost_poisoning_does_not_change_earlier_recommendations(canonical_panel, cfg):
    class Baseline:
        metadata = None
        prediction_cap_ = None

        def predict(self, frame):
            return np.full(len(frame), 50.0)

    model = HybridPricingModel(
        Baseline(), method="pooled", elasticity_table=fixed_elasticity_table(-2.0), cfg=cfg
    )
    stats = {"n_obs": 300, "n_distinct_prices": 30, "price_cv": 0.2,
             "price_min": 2.0, "price_max": 4.5}

    clean = build_feature_table(canonical_panel, cfg=cfg)
    dirty = build_feature_table(poison(canonical_panel), cfg=cfg)

    def rec_for(frame):
        row = frame[(frame["upc"] == 111) & (frame["store"] == 2) & (frame["week"] == COMPARE_WEEK)]
        return optimize_price(model, row.head(1), cfg=cfg, series_stats=stats)

    a, b = rec_for(clean).as_dict(), rec_for(dirty).as_dict()
    for field in (
        "proposed_candidate_price", "final_recommended_price", "decision", "actionable", "unit_cost_used",
        "predicted_units_recommended", "expected_gross_profit_recommended",
        "model_internal_estimated_profit_uplift_pct", "risk_level",
    ):
        assert a[field] == b[field], field


def test_missing_prior_cost_blocks_gross_profit_optimization(canonical_panel, cfg):
    """The first week of a series has no prior AAC: it must refuse, not guess."""
    feats = build_feature_table(canonical_panel, cfg=cfg)
    first = feats[(feats["upc"] == 111) & (feats["store"] == 2) & (feats["week"] == 1)]
    assert first["decision_time_unit_cost"].isna().all()

    class Baseline:
        metadata = None
        prediction_cap_ = None

        def predict(self, frame):
            return np.full(len(frame), 50.0)

    model = HybridPricingModel(
        Baseline(), method="pooled", elasticity_table=fixed_elasticity_table(-2.0), cfg=cfg
    )
    rec = optimize_price(
        model, first.head(1), cfg=cfg,
        series_stats={"n_obs": 300, "n_distinct_prices": 30, "price_cv": 0.2,
                      "price_min": 2.0, "price_max": 4.5},
    )
    assert rec.decision == "KEEP_CURRENT"
    assert "COST_UNAVAILABLE" in rec.reason_codes
    assert rec.unit_cost_used is None
