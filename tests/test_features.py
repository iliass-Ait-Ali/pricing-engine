"""Phase C tests: lag alignment, leakage protection, price-feature recomputation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pricing_engine.features.build import (
    CONTEXT_FEATURES,
    PRICE_DEPENDENT_FEATURES,
    FeatureError,
    build_feature_table,
    parse_package_size,
    recompute_price_features,
    temporal_split,
    training_frame,
)


@pytest.fixture
def feats(panel, cfg):
    return build_feature_table(panel, cfg=cfg)


# ---------------------------------------------------------------------------
# lag alignment
# ---------------------------------------------------------------------------
def test_lag_move_is_previous_week_of_same_series(feats):
    one = feats[(feats["upc"] == 111) & (feats["store"] == 2)].sort_values("week")
    assert np.allclose(
        one["lag_move_1"].to_numpy()[1:], one["move"].to_numpy()[:-1], equal_nan=False
    )
    assert np.isnan(one["lag_move_1"].to_numpy()[0])


def test_lag_features_never_use_the_current_week(panel, cfg):
    """Corrupting week t's outcome must not change any feature at week <= t.

    This is the leakage test that actually bites: if any rolling window were
    unshifted, poisoning week t would move week t's own feature values.
    """
    clean = build_feature_table(panel, cfg=cfg).sort_values(["upc", "store", "week"])
    poisoned_panel = panel.copy()
    target_week = 30
    mask = (poisoned_panel["upc"] == 111) & (poisoned_panel["store"] == 2) & (
        poisoned_panel["week"] == target_week
    )
    assert mask.sum() == 1
    poisoned_panel.loc[mask, "move"] = 1e9
    poisoned_panel.loc[mask, "revenue"] = 1e9
    poisoned_panel.loc[mask, "gross_profit"] = 1e9
    poisoned = build_feature_table(poisoned_panel, cfg=cfg).sort_values(["upc", "store", "week"])

    feature_cols = list(PRICE_DEPENDENT_FEATURES) + [
        c for c in CONTEXT_FEATURES if c in clean.columns
    ]
    sel = (clean["upc"] == 111) & (clean["store"] == 2) & (clean["week"] <= target_week)
    pd.testing.assert_frame_equal(
        clean.loc[sel.to_numpy(), feature_cols].reset_index(drop=True),
        poisoned.loc[sel.to_numpy(), feature_cols].reset_index(drop=True),
    )
    # ... and week t+1 SHOULD see it (proving the test has teeth).
    after = (clean["upc"] == 111) & (clean["store"] == 2) & (clean["week"] == target_week + 1)
    assert clean.loc[after.to_numpy(), "lag_move_1"].iloc[0] != poisoned.loc[
        after.to_numpy(), "lag_move_1"
    ].iloc[0]


def test_rolling_windows_are_shifted(panel, cfg):
    one = panel[(panel["upc"] == 111) & (panel["store"] == 2)].sort_values("week")
    feats = build_feature_table(one, cfg=cfg).sort_values("week")
    manual = one["move"].shift(1).rolling(4, min_periods=2).mean()
    assert np.allclose(
        feats["roll_mean_move_4"].to_numpy(dtype="float64"),
        manual.to_numpy(dtype="float64"),
        equal_nan=True,
    )


def test_decision_time_cost_is_lagged_not_contemporaneous(feats):
    one = feats[(feats["upc"] == 111) & (feats["store"] == 2)].sort_values("week")
    assert np.allclose(
        one["decision_time_unit_cost"].to_numpy(dtype="float64")[1:],
        one["estimated_unit_aac"].to_numpy(dtype="float64")[:-1],
        equal_nan=True,
    )


def test_series_reference_price_uses_only_past_prices(feats):
    one = feats[(feats["upc"] == 222) & (feats["store"] == 5)].sort_values("week")
    manual = one["effective_unit_price"].shift(1).expanding(min_periods=1).median()
    assert np.allclose(
        one["series_reference_price"].to_numpy(dtype="float64"),
        manual.to_numpy(dtype="float64"),
        equal_nan=True,
    )


def test_no_outcome_columns_are_features():
    forbidden = {"revenue", "gross_profit", "gross_margin_rate", "profit", "estimated_unit_aac", "move"}
    assert forbidden.isdisjoint(set(PRICE_DEPENDENT_FEATURES) | set(CONTEXT_FEATURES))


# ---------------------------------------------------------------------------
# price-feature recomputation (the simulator contract)
# ---------------------------------------------------------------------------
def test_recompute_price_features_changes_only_price_features(feats):
    base = feats.dropna(subset=["lag_price_1", "series_reference_price"]).head(20).copy()
    bumped = recompute_price_features(base, base["effective_unit_price"] * 1.10)
    for col in PRICE_DEPENDENT_FEATURES:
        assert not np.allclose(
            base[col].to_numpy(dtype="float64"), bumped[col].to_numpy(dtype="float64")
        ), col
    for col in CONTEXT_FEATURES:
        if col in base.columns and pd.api.types.is_numeric_dtype(base[col]):
            assert np.allclose(
                base[col].to_numpy(dtype="float64"),
                bumped[col].to_numpy(dtype="float64"),
                equal_nan=True,
            ), col


def test_recompute_price_features_math(feats):
    base = feats.dropna(subset=["lag_price_1", "series_reference_price"]).head(5).copy()
    price = np.full(len(base), 4.44)
    out = recompute_price_features(base, price)
    assert np.allclose(out["effective_unit_price"], 4.44)
    assert np.allclose(out["log_price"], np.log(4.44))
    assert np.allclose(
        out["price_vs_last_week"].to_numpy(dtype="float64"),
        4.44 / base["lag_price_1"].to_numpy(dtype="float64") - 1.0,
    )


def test_recompute_price_features_rejects_non_positive_price(feats):
    base = feats.head(3).copy()
    with pytest.raises(FeatureError):
        recompute_price_features(base, np.array([1.0, -1.0, 2.0]))


def test_recompute_price_features_rejects_length_mismatch(feats):
    base = feats.head(3).copy()
    with pytest.raises(FeatureError):
        recompute_price_features(base, np.array([1.0, 2.0]))


# ---------------------------------------------------------------------------
# split
# ---------------------------------------------------------------------------
def test_temporal_split_is_chronological_and_disjoint(feats, cfg):
    usable = training_frame(feats)
    train, valid, test, info = temporal_split(usable, cfg=cfg)
    assert train["week"].max() < valid["week"].min() < test["week"].min()
    assert set(train["week"]).isdisjoint(valid["week"])
    assert set(valid["week"]).isdisjoint(test["week"])
    assert info["n_train"] + info["n_valid"] + info["n_test"] == len(usable)


def test_training_frame_drops_rows_without_history(feats):
    usable = training_frame(feats)
    assert usable["lag_move_1"].notna().all()
    assert usable["lag_price_1"].notna().all()


# ---------------------------------------------------------------------------
# metadata parsing
# ---------------------------------------------------------------------------
def test_parse_package_size_handles_oz_and_lb():
    sizes = pd.Series(["15.5 OZ", "1 LB", "10 OZ", None])
    parsed = parse_package_size(sizes)
    assert parsed[0] == pytest.approx(15.5)
    assert parsed[1] == pytest.approx(16.0)
    assert np.isnan(parsed[3])
