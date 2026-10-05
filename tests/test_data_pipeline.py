"""Phase A tests: derived formulas, validity rules, joins and week decoding."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pricing_engine.data.cleaning import (
    ExclusionLog,
    add_derived_economics,
    add_promotion_fields,
    add_time_fields,
    build_canonical,
    decode_week,
    merge_metadata,
)
from pricing_engine.data.schema import GRAIN, SchemaError, assert_required_columns


# ---------------------------------------------------------------------------
# derived formulas
# ---------------------------------------------------------------------------
def test_effective_unit_price_is_price_over_qty(raw_movement):
    out = add_derived_economics(raw_movement)
    valid = out[out["qty"] > 0]
    expected = valid["price"] / valid["qty"]
    assert np.allclose(valid["effective_unit_price"], expected)


def test_revenue_is_price_times_move_over_qty(raw_movement):
    out = add_derived_economics(raw_movement)
    valid = out[out["qty"] > 0]
    expected = valid["price"] * valid["move"] / valid["qty"]
    assert np.allclose(valid["revenue"], expected)


def test_gross_margin_rate_is_profit_over_100(raw_movement):
    out = add_derived_economics(raw_movement)
    assert np.allclose(out["gross_margin_rate"], raw_movement["profit"] / 100.0)


def test_estimated_unit_aac_formula(raw_movement):
    out = add_derived_economics(raw_movement)
    valid = out[out["qty"] > 0]
    expected = (valid["price"] / valid["qty"]) * (1.0 - valid["profit"] / 100.0)
    assert np.allclose(valid["estimated_unit_aac"], expected)


def test_gross_profit_formula(raw_movement):
    out = add_derived_economics(raw_movement)
    valid = out[out["qty"] > 0]
    expected = valid["revenue"] * valid["gross_margin_rate"]
    assert np.allclose(valid["gross_profit"], expected)


def test_zero_qty_does_not_divide_silently(raw_movement):
    """qty = 0 must produce NaN (never inf, never a fabricated unit price)."""
    out = add_derived_economics(raw_movement)
    zero_qty = out[raw_movement["qty"] == 0]
    assert len(zero_qty) == 1
    assert zero_qty["effective_unit_price"].isna().all()
    assert not np.isinf(out["effective_unit_price"].to_numpy(dtype="float64")).any()


# ---------------------------------------------------------------------------
# promotion coding
# ---------------------------------------------------------------------------
def test_promotion_flag_only_for_documented_codes(raw_movement):
    out = add_promotion_fields(raw_movement)
    # Documented codes: B, C, S. 'G' is undocumented -> flagged UNKNOWN_CODE
    # but not counted as a recorded promotion type we can interpret.
    assert out.loc[out["sale"] == "B", "recorded_promotion_flag"].eq(1).all()
    assert out.loc[out["sale"] == "C", "recorded_promotion_type"].eq("Coupon").all()
    assert out.loc[out["sale"] == "G", "recorded_promotion_type"].eq("UNKNOWN_CODE").all()
    assert out.loc[out["sale"].isna(), "recorded_promotion_type"].eq("NONE_RECORDED").all()


# ---------------------------------------------------------------------------
# time decoding
# ---------------------------------------------------------------------------
def test_week_decoding_matches_official_anchor():
    weeks = pd.Series([1, 2, 399], dtype="int32")
    dates = decode_week(weeks, "1989-09-14")
    assert list(dates.dt.date.astype(str)) == ["1989-09-14", "1989-09-21", "1997-05-01"]


def test_week_decoding_is_strictly_increasing_and_weekly(raw_movement):
    out = add_time_fields(raw_movement, "1989-09-14")
    diffs = out.sort_values("week").drop_duplicates("week")["week_start_date"].diff().dropna()
    assert (diffs == pd.Timedelta(days=7)).all()
    assert out["year"].between(1989, 1998).all()


# ---------------------------------------------------------------------------
# validity rules / canonical build
# ---------------------------------------------------------------------------
def test_ok_zero_rows_are_excluded(raw_movement, raw_upc_meta, cfg):
    df, audit = build_canonical(raw_movement, raw_upc_meta, cfg=cfg)
    assert (df["ok"] == 1).all()
    removed = {e["rule"]: e["rows_removed"] for e in audit["exclusions"]}
    assert removed["ok_flag_zero"] == 1
    assert not ((df["upc"] == 111) & (df["store"] == 2) & (df["week"] == 13)).any()


def test_invalid_qty_row_is_excluded_and_documented(raw_movement, raw_upc_meta, cfg):
    df, audit = build_canonical(raw_movement, raw_upc_meta, cfg=cfg)
    removed = {e["rule"]: e["rows_removed"] for e in audit["exclusions"]}
    assert removed["non_positive_qty"] == 1
    assert not (df["upc"] == 333).any()
    assert audit["raw_rows"] - audit["processed_rows"] == sum(removed.values())


def test_canonical_grain_is_unique(raw_movement, raw_upc_meta, cfg):
    df, _ = build_canonical(raw_movement, raw_upc_meta, cfg=cfg)
    assert not df.duplicated(subset=list(GRAIN)).any()


def test_zero_move_rows_are_kept_and_flagged(raw_movement, raw_upc_meta, cfg):
    """Zero sales weeks are real demand information; they must not be dropped."""
    df, _ = build_canonical(raw_movement, raw_upc_meta, cfg=cfg)
    zero_rows = df[df["move"] == 0]
    assert len(zero_rows) == 1
    assert zero_rows["zero_move_flag"].eq(1).all()


# ---------------------------------------------------------------------------
# metadata join
# ---------------------------------------------------------------------------
def test_metadata_join_does_not_multiply_rows(raw_movement, raw_upc_meta):
    log = ExclusionLog(n_start=len(raw_movement))
    merged, report = merge_metadata(raw_movement, raw_upc_meta, log)
    assert len(merged) == len(raw_movement)
    assert report["metadata_upcs_without_movement"] == 1  # upc 999 never sold


def test_duplicated_metadata_upc_is_reported_not_silently_expanded(raw_movement, raw_upc_meta):
    dup_meta = pd.concat([raw_upc_meta, raw_upc_meta.iloc[[0]]], ignore_index=True)
    log = ExclusionLog(n_start=len(raw_movement))
    merged, report = merge_metadata(raw_movement, dup_meta, log)
    assert report["duplicate_metadata_upcs"] == 1
    assert len(merged) == len(raw_movement)


def test_unmatched_movement_upc_is_kept_and_flagged(raw_movement, raw_upc_meta):
    meta = raw_upc_meta[raw_upc_meta["upc"] != 333]
    log = ExclusionLog(n_start=len(raw_movement))
    merged, report = merge_metadata(raw_movement, meta, log)
    assert report["movement_upcs_without_metadata"] == 1
    assert len(merged) == len(raw_movement)
    assert merged.loc[merged["upc"] == 333, "has_metadata_flag"].eq(0).all()


# ---------------------------------------------------------------------------
# schema guard
# ---------------------------------------------------------------------------
def test_missing_required_column_raises_domain_error():
    with pytest.raises(SchemaError) as excinfo:
        assert_required_columns(["store", "upc"], ("store", "upc", "week"), "wcer.csv")
    assert "week" in str(excinfo.value)


def test_blank_export_rows_are_counted_and_dropped(raw_movement, raw_upc_meta, cfg):
    """A row with only a UPC (as in the official Crackers file) must not break the build."""

    from pricing_engine.data.cleaning import build_canonical

    clean, _ = build_canonical(raw_movement, raw_upc_meta, cfg=cfg)
    with_blank = raw_movement.reset_index(drop=True).reindex(range(len(raw_movement) + 1))
    with_blank.loc[len(raw_movement), "upc"] = int(raw_movement["upc"].iloc[0])
    for col, dtype in (("store", "Int32"), ("week", "Int32"), ("ok", "Int8"), ("upc", "Int64")):
        with_blank[col] = with_blank[col].astype(dtype)   # what the loader produces

    out, audit = build_canonical(with_blank, raw_upc_meta, cfg=cfg)
    rules = {e["rule"]: e["rows_removed"] for e in audit["exclusions"]}
    assert rules["blank_key_rows"] == 1
    assert len(out) == len(clean)
    assert str(out["store"].dtype) == "int32" and str(out["week"].dtype) == "int32"
    assert "blank_key_rows" not in {e["rule"] for e in build_canonical(
        raw_movement, raw_upc_meta, cfg=cfg)[1]["exclusions"]}
