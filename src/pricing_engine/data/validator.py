"""Raw-data auditing and canonical-table validation.

Two responsibilities:

1. :func:`raw_audit` profiles the raw Dominick's files *before* any cleaning,
   so the data-quality report describes the source, not our own output.
2. :func:`validate_processed` re-derives every documented formula on the
   canonical table and returns a list of pass/fail checks. This is what
   scripts/validate_data.py executes and what reports/01_DATA_AUDIT.md quotes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from pricing_engine.data.schema import GRAIN

TOL = 1e-6


@dataclass
class Check:
    """One validation result."""

    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _numeric_profile(series: pd.Series) -> dict[str, Any]:
    s = pd.to_numeric(series, errors="coerce")
    valid = s.dropna()
    if valid.empty:
        return {"count": 0}
    q = valid.quantile([0.01, 0.25, 0.5, 0.75, 0.99])
    return {
        "count": int(valid.size),
        "missing": int(s.isna().sum()),
        "mean": float(valid.mean()),
        "std": float(valid.std()),
        "min": float(valid.min()),
        "p01": float(q.loc[0.01]),
        "p25": float(q.loc[0.25]),
        "median": float(q.loc[0.5]),
        "p75": float(q.loc[0.75]),
        "p99": float(q.loc[0.99]),
        "max": float(valid.max()),
        "n_zero": int((valid == 0).sum()),
        "n_negative": int((valid < 0).sum()),
    }


def raw_audit(movement: pd.DataFrame, upc_meta: pd.DataFrame) -> dict[str, Any]:
    """Profile the raw movement and UPC metadata tables before cleaning."""
    mv = movement
    audit: dict[str, Any] = {
        "movement": {
            "rows": int(len(mv)),
            "columns": list(mv.columns),
            "dtypes": {c: str(t) for c, t in mv.dtypes.items()},
            "null_counts": {c: int(mv[c].isna().sum()) for c in mv.columns},
            "exact_duplicate_rows": int(mv.duplicated().sum()),
            "duplicate_grain_rows": int(mv.duplicated(subset=list(GRAIN)).sum()),
            "n_upc": int(mv["upc"].nunique()),
            "n_store": int(mv["store"].nunique()),
            "n_week": int(mv["week"].nunique()),
            "week_min": int(mv["week"].min()),
            "week_max": int(mv["week"].max()),
            "ok_value_counts": {
                str(k): int(v) for k, v in mv["ok"].value_counts(dropna=False).items()
            },
            "sale_code_counts": {
                str(k): int(v) for k, v in mv["sale"].value_counts(dropna=False).items()
            },
            "profile": {
                col: _numeric_profile(mv[col])
                for col in ("price", "qty", "move", "profit")
                if col in mv.columns
            },
        },
        "upc_metadata": {
            "rows": int(len(upc_meta)),
            "columns": list(upc_meta.columns),
            "n_upc": int(upc_meta["upc"].nunique()),
            "duplicate_upc": int(upc_meta.duplicated(subset=["upc"]).sum()),
            "null_counts": {c: int(upc_meta[c].isna().sum()) for c in upc_meta.columns},
        },
    }
    return audit


def validate_processed(df: pd.DataFrame) -> list[Check]:
    """Re-derive documented formulas and structural invariants on the canonical table."""
    checks: list[Check] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append(Check(name=name, passed=bool(passed), detail=detail))

    add("non_empty", len(df) > 0, f"{len(df):,} rows")

    dupes = int(df.duplicated(subset=list(GRAIN)).sum())
    add("grain_unique", dupes == 0, f"{dupes} duplicated upc x store x week rows")

    if "ok" in df.columns:
        bad_ok = int((df["ok"] != 1).sum())
        add("only_ok_rows", bad_ok == 0, f"{bad_ok} rows with ok != 1")

    price = df["price"].to_numpy(dtype="float64")
    qty = df["qty"].to_numpy(dtype="float64")
    move = df["move"].to_numpy(dtype="float64")
    profit = df["profit"].to_numpy(dtype="float64")
    eup = df["effective_unit_price"].to_numpy(dtype="float64")
    rev = df["revenue"].to_numpy(dtype="float64")
    gmr = df["gross_margin_rate"].to_numpy(dtype="float64")
    aac = df["estimated_unit_aac"].to_numpy(dtype="float64")
    gp = df["gross_profit"].to_numpy(dtype="float64")

    def close(a: np.ndarray, b: np.ndarray) -> tuple[bool, float]:
        diff = np.abs(a - b)
        finite = np.isfinite(diff)
        worst = float(np.nanmax(diff[finite])) if finite.any() else 0.0
        return bool(worst <= TOL), worst

    ok, worst = close(eup, price / qty)
    add("formula_effective_unit_price", ok, f"max abs deviation {worst:.3e}")

    ok, worst = close(rev, price * move / qty)
    add("formula_revenue", ok, f"max abs deviation {worst:.3e}")

    ok, worst = close(gmr, profit / 100.0)
    add("formula_gross_margin_rate", ok, f"max abs deviation {worst:.3e}")

    ok, worst = close(aac, eup * (1.0 - gmr))
    add("formula_estimated_unit_aac", ok, f"max abs deviation {worst:.3e}")

    ok, worst = close(gp, rev * gmr)
    add("formula_gross_profit", ok, f"max abs deviation {worst:.3e}")

    add("positive_price", bool((price > 0).all()), "all bundle prices are strictly positive")
    add("positive_qty", bool((qty > 0).all()), "all bundle quantities are strictly positive")
    add("non_negative_move", bool((move >= 0).all()), "no negative unit movement")
    add(
        "no_infinite_unit_price",
        bool(np.isfinite(eup).all()),
        "effective_unit_price is finite everywhere (no silent divide-by-zero)",
    )

    dates = pd.to_datetime(df["week_start_date"])
    monotone_map = df.groupby("week")["week_start_date"].nunique().max()
    add(
        "week_date_mapping_unique",
        int(monotone_map) == 1,
        "each Dominick's week index maps to exactly one calendar date",
    )
    add(
        "date_range_plausible",
        bool(dates.min() >= pd.Timestamp("1989-01-01") and dates.max() <= pd.Timestamp("1998-12-31")),
        f"{dates.min().date()} .. {dates.max().date()}",
    )

    promo = df["recorded_promotion_flag"]
    add(
        "promotion_flag_binary",
        bool(promo.isin([0, 1]).all()),
        f"recorded promotion share = {float(promo.mean()):.4f}",
    )
    return checks


def checks_to_frame(checks: list[Check]) -> pd.DataFrame:
    return pd.DataFrame([c.as_dict() for c in checks])
