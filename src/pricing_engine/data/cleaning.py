"""Build the canonical Dominick's Cereals table.

Pipeline (each step is explicit and auditable):

    raw movement + raw UPC metadata
      -> time decoding (Dominick's week index -> calendar dates)
      -> promotion coding (recorded_* names: absence of a code is NOT proof
         that no promotion occurred)
      -> derived pricing economics (see docs/DATA_DICTIONARY.md)
      -> documented validity filters (every exclusion is counted)
      -> metadata join (verified not to multiply rows)
      -> canonical UPC x store x week table

Nothing is silently dropped: :func:`build_canonical` returns an audit record
listing every exclusion rule, the number of rows it removed and its share of
the raw table.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from pricing_engine.config import Config, load_config
from pricing_engine.data.schema import CANONICAL_COLUMNS, GRAIN, PROMOTION_CODES


@dataclass
class ExclusionLog:
    """Accumulates documented row-exclusion rules."""

    n_start: int
    entries: list[dict[str, Any]] = field(default_factory=list)

    def record(self, rule: str, removed: int, reason: str) -> None:
        self.entries.append(
            {
                "rule": rule,
                "rows_removed": int(removed),
                "pct_of_raw": round(100.0 * removed / self.n_start, 4) if self.n_start else 0.0,
                "reason": reason,
            }
        )

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.entries)


# ---------------------------------------------------------------------------
# time
# ---------------------------------------------------------------------------
def decode_week(week: pd.Series, week1_start: str) -> pd.Series:
    """Map the Dominick's integer week index to the week's start date.

    Dominick's store weeks run Thursday -> Wednesday and week 1 starts on
    week1_start (1989-09-14 per the Kilts Center documentation), so the mapping
    is week1_start + (week - 1) * 7 days.
    """
    base = pd.Timestamp(week1_start)
    return base + pd.to_timedelta((week.astype("int64") - 1) * 7, unit="D")


def add_time_fields(df: pd.DataFrame, week1_start: str) -> pd.DataFrame:
    """Add calendar fields derived from the decoded week start date."""
    out = df.copy()
    out["week_start_date"] = decode_week(out["week"], week1_start)
    out["year"] = out["week_start_date"].dt.year.astype("int16")
    out["month"] = out["week_start_date"].dt.month.astype("int8")
    out["quarter"] = out["week_start_date"].dt.quarter.astype("int8")
    out["week_of_year"] = out["week_start_date"].dt.isocalendar().week.astype("int8")
    return out


# ---------------------------------------------------------------------------
# promotions
# ---------------------------------------------------------------------------
def add_promotion_fields(df: pd.DataFrame) -> pd.DataFrame:
    """Add recorded_promotion_flag / recorded_promotion_type.

    Naming is deliberate: the manual states promotion coding is incomplete, so
    a missing code does NOT prove that no promotion occurred.
    """
    out = df.copy()
    code = out["sale"].astype("string").str.strip().str.upper()
    code = code.replace({"": pd.NA})
    out["sale"] = code
    out["recorded_promotion_flag"] = code.isin(list(PROMOTION_CODES)).astype("int8")
    out["recorded_promotion_type"] = (
        code.map(PROMOTION_CODES).fillna("NONE_RECORDED").astype("string")
    )
    unknown = code.notna() & ~code.isin(list(PROMOTION_CODES))
    out.loc[unknown, "recorded_promotion_type"] = "UNKNOWN_CODE"
    return out


# ---------------------------------------------------------------------------
# economics
# ---------------------------------------------------------------------------
def add_derived_economics(df: pd.DataFrame) -> pd.DataFrame:
    """Add the documented derived pricing quantities.

    estimated_unit_aac is an *Average Acquisition Cost* estimate implied by the
    accounting gross-margin percentage. It is NOT necessarily the economically
    relevant replacement cost (see docs/DATA_DICTIONARY.md).
    """
    out = df.copy()
    qty = out["qty"].where(out["qty"] > 0)  # never divide by zero silently
    out["effective_unit_price"] = out["price"] / qty
    out["revenue"] = out["effective_unit_price"] * out["move"]
    out["gross_margin_rate"] = out["profit"] / 100.0
    out["estimated_unit_aac"] = out["effective_unit_price"] * (1.0 - out["gross_margin_rate"])
    out["gross_profit"] = out["revenue"] * out["gross_margin_rate"]
    return out


# ---------------------------------------------------------------------------
# validity
# ---------------------------------------------------------------------------
def apply_validity_filters(df: pd.DataFrame, cfg: Config, log: ExclusionLog) -> pd.DataFrame:
    """Apply documented validity rules, recording the cost of each one."""
    out = df
    min_price = float(cfg.get("data.min_price", 0.01))
    min_qty = float(cfg.get("data.min_qty", 1))
    keep_only_ok = bool(cfg.get("data.keep_only_ok_rows", True))

    # Masks are built lazily: each rule is evaluated on the frame that survived
    # the previous rules, so the counts are "additional rows removed by this
    # rule" and never index-misaligned.
    rules: list[tuple[str, Callable[[pd.DataFrame], pd.Series], str]] = [
        (
            "missing_key",
            lambda d: d[list(GRAIN)].isna().any(axis=1),
            "upc / store / week must be present to identify an observation",
        ),
        (
            "missing_core_measure",
            lambda d: d[["price", "move", "qty", "profit"]].isna().any(axis=1),
            "price, move, qty and profit are required for pricing economics",
        ),
        (
            "ok_flag_zero",
            (lambda d: d["ok"] != 1) if keep_only_ok else (lambda d: pd.Series(False, index=d.index)),
            "the manual marks ok = 0 rows as suspect / trash",
        ),
        (
            "non_positive_price",
            lambda d: d["price"] < min_price,
            "bundle price must be at least " + str(min_price),
        ),
        (
            "non_positive_qty",
            lambda d: d["qty"] < min_qty,
            "bundle quantity must be at least " + str(min_qty) + " to compute a unit price",
        ),
        (
            "negative_move",
            lambda d: d["move"] < 0,
            "negative unit movement cannot be interpreted as demand",
        ),
    ]

    for rule, mask_fn, reason in rules:
        bool_mask = mask_fn(out).astype("boolean").fillna(True).to_numpy(dtype=bool)
        removed = int(bool_mask.sum())
        log.record(rule, removed, reason)
        if removed:
            out = out.loc[~bool_mask]
    return out.copy()


#: Plain numpy dtype for each nullable integer dtype the loader reads.
_PLAIN_INT = {"Int8": "int8", "Int16": "int16", "Int32": "int32", "Int64": "int64"}


def drop_blank_key_rows(df: pd.DataFrame, log: ExclusionLog) -> pd.DataFrame:
    """Drop rows with no store or week, then restore plain integer dtypes.

    Some official movement files carry blank export rows (a UPC and nothing
    else). They identify no observation, so they are removed first and counted
    in the exclusion log under their own rule. A file without such rows passes
    through unchanged apart from the dtype restoration, which is a no-op for
    frames that never used nullable integers.
    """
    keys = [c for c in GRAIN if c in df.columns]
    blank = df[keys].isna().any(axis=1) if keys else pd.Series(False, index=df.index)
    removed = int(blank.sum())
    if removed:
        log.record("blank_key_rows", removed,
                   "blank export rows with no store or week; they identify no observation")
        df = df.loc[~blank].copy()
    for col in df.columns:
        plain = _PLAIN_INT.get(str(df[col].dtype))
        if plain and not df[col].isna().any():
            df[col] = df[col].astype(plain)
    return df


def flag_suspicious(df: pd.DataFrame) -> pd.DataFrame:
    """Flag (but do not drop) observations whose accounting values look odd."""
    out = df.copy()
    rate = out["gross_margin_rate"]
    out["margin_implausible_flag"] = ((rate >= 1.0) | (rate <= -1.0) | rate.isna()).astype("int8")
    out["aac_nonpositive_flag"] = (out["estimated_unit_aac"] <= 0).astype("int8")
    out["zero_move_flag"] = (out["move"] == 0).astype("int8")
    out["bundle_flag"] = (out["qty"] > 1).astype("int8")
    return out


# ---------------------------------------------------------------------------
# metadata join
# ---------------------------------------------------------------------------
def merge_metadata(
    movement: pd.DataFrame, upc_meta: pd.DataFrame, log: ExclusionLog
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Left-join UPC metadata, verifying the join does not multiply rows."""
    meta = upc_meta.copy()
    dup_meta = int(meta.duplicated(subset=["upc"]).sum())
    if dup_meta:
        # Keep the first record per UPC, but never silently: it is reported.
        meta = meta.drop_duplicates(subset=["upc"], keep="first")

    keep_cols = [
        c for c in ("upc", "com_code", "descrip", "size", "case", "nitem") if c in meta.columns
    ]
    n_before = len(movement)
    merged = movement.merge(meta[keep_cols], on="upc", how="left", validate="many_to_one")
    if len(merged) != n_before:
        raise AssertionError(
            f"UPC metadata join changed the row count: {n_before} -> {len(merged)}."
        )

    movement_upcs = set(movement["upc"].unique())
    meta_upcs = set(meta["upc"].unique())
    unmatched_mask = merged["descrip"].isna()
    report = {
        "duplicate_metadata_upcs": dup_meta,
        "movement_upcs": len(movement_upcs),
        "metadata_upcs": len(meta_upcs),
        "movement_upcs_without_metadata": len(movement_upcs - meta_upcs),
        "metadata_upcs_without_movement": len(meta_upcs - movement_upcs),
        "rows_without_metadata": int(unmatched_mask.sum()),
        "pct_rows_without_metadata": round(100.0 * unmatched_mask.mean(), 4) if n_before else 0.0,
    }
    log.record(
        "metadata_unmatched_kept",
        0,
        str(report["rows_without_metadata"])
        + " rows have no UPC metadata; they are kept and flagged, not dropped",
    )
    merged["has_metadata_flag"] = (~unmatched_mask).astype("int8")
    return merged, report


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------
def build_canonical(
    movement: pd.DataFrame,
    upc_meta: pd.DataFrame,
    cfg: Config | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return (canonical_table, audit_record)."""
    cfg = cfg or load_config()
    log = ExclusionLog(n_start=len(movement))
    audit: dict[str, Any] = {"raw_rows": int(len(movement))}

    df = movement.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    df = drop_blank_key_rows(df, log)

    exact_dupes = int(df.duplicated().sum())
    audit["raw_exact_duplicate_rows"] = exact_dupes
    if exact_dupes:
        df = df.drop_duplicates()
        log.record(
            "exact_duplicate_rows", exact_dupes, "byte-identical repeated rows in the raw file"
        )

    audit["raw_duplicate_grain_rows"] = int(df.duplicated(subset=list(GRAIN)).sum())

    df = add_promotion_fields(df)
    df = add_time_fields(df, str(cfg.get("data.week1_start_date", "1989-09-14")))
    df = add_derived_economics(df)
    df = apply_validity_filters(df, cfg, log)
    df = flag_suspicious(df)

    # Grain enforcement AFTER validity filtering: keep the first observation per
    # (upc, store, week) and report how many collisions remained.
    remaining_key_dupes = int(df.duplicated(subset=list(GRAIN)).sum())
    audit["valid_duplicate_grain_rows"] = remaining_key_dupes
    if remaining_key_dupes:
        df = df.sort_values(list(GRAIN)).drop_duplicates(subset=list(GRAIN), keep="first")
        log.record(
            "duplicate_grain_rows",
            remaining_key_dupes,
            "more than one valid row for the same upc x store x week; kept the first",
        )

    df, meta_report = merge_metadata(df, upc_meta, log)
    audit["metadata_join"] = meta_report

    ordered = [c for c in CANONICAL_COLUMNS if c in df.columns]
    extras = [c for c in df.columns if c not in ordered]
    df = df[ordered + extras].sort_values(["upc", "store", "week"]).reset_index(drop=True)

    audit["processed_rows"] = int(len(df))
    audit["rows_removed"] = int(audit["raw_rows"] - len(df))
    audit["exclusions"] = log.entries
    audit["upcs"] = int(df["upc"].nunique())
    audit["stores"] = int(df["store"].nunique())
    audit["weeks"] = int(df["week"].nunique())
    audit["week_min"] = int(df["week"].min())
    audit["week_max"] = int(df["week"].max())
    audit["date_min"] = str(df["week_start_date"].min().date())
    audit["date_max"] = str(df["week_start_date"].max().date())
    audit["total_units"] = float(df["move"].sum())
    audit["total_revenue"] = float(np.nansum(df["revenue"].to_numpy()))
    audit["total_gross_profit"] = float(np.nansum(df["gross_profit"].to_numpy()))
    return df, audit
