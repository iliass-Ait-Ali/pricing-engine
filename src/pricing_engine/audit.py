"""Recommendation audit log.

Human-in-the-loop lifecycle:

    GENERATED -> REVIEWED -> APPROVED / REJECTED -> PUBLISHED

This demo does not integrate with a live commerce system, but every
recommendation it produces is written to an append-only log with its inputs,
outputs, model version, constraints, risk level and reason codes, so any
recommendation can be reconstructed and questioned after the fact.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from pricing_engine.utils.io import ensure_dir, utc_now

LIFECYCLE_STATES = ("GENERATED", "REVIEWED", "APPROVED", "REJECTED", "PUBLISHED")

LOG_COLUMNS = [
    "logged_at_utc",
    "lifecycle_state",
    "upc",
    "store",
    "product_description",
    "decision_week",
    "decision_week_start_date",
    "objective",
    "policy_profile",
    "model_version",
    "price_response_method",
    "elasticity_used",
    "elasticity_source",
    "current_price",
    "proposed_candidate_price",
    "final_recommended_price",
    "proposed_price_change_pct",
    "price_change_pct",
    "decision",
    "actionable",
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
    "risk_level",
    "reason_codes",
    "risk_notes",
    "constraints_json",
]


@dataclass
class RecommendationLog:
    """Append-only CSV log of every recommendation produced."""

    path: Path

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        ensure_dir(self.path.parent)

    def to_frame(self, recommendations: Iterable[Any], state: str = "GENERATED") -> pd.DataFrame:
        if state not in LIFECYCLE_STATES:
            raise ValueError(f"Unknown lifecycle state {state!r}; expected one of {LIFECYCLE_STATES}")
        now = utc_now()
        rows = []
        for rec in recommendations:
            d = rec.as_dict() if hasattr(rec, "as_dict") else dict(rec)
            rows.append(
                {
                    "logged_at_utc": now,
                    "lifecycle_state": state,
                    "product_description": d.get("product_description"),
                    "reason_codes": "|".join(d.get("reason_codes", [])),
                    "risk_notes": "|".join(d.get("risk_notes", [])),
                    "constraints_json": json.dumps(d.get("constraints", {}), default=str),
                    **{k: d.get(k) for k in LOG_COLUMNS if k in d},
                }
            )
        return pd.DataFrame(rows)[LOG_COLUMNS]

    def current_columns(self) -> list[str] | None:
        """Columns of the log on disk, or None when there is no log yet."""
        if not self.path.exists():
            return None
        try:
            return pd.read_csv(self.path, nrows=0).columns.tolist()
        except (pd.errors.EmptyDataError, pd.errors.ParserError):
            return []

    def _rotate_if_schema_changed(self) -> Path | None:
        """Archive the log when the recommendation schema has changed.

        An append-only CSV cannot hold two schemas: appending wider rows under a
        narrower header produces a file that no reader can parse, and the
        failure surfaces far away from its cause (Phase M found it in the
        dashboard). When the columns change, the old log is renamed rather than
        deleted - it is an audit log, and past recommendations stay readable
        under the schema they were written with.
        """
        existing = self.current_columns()
        if existing is None or existing == LOG_COLUMNS:
            return None
        stamp = utc_now().replace(":", "").replace("-", "").replace(" ", "T")[:15]
        archived = self.path.with_name(f"{self.path.stem}__schema_{stamp}{self.path.suffix}")
        self.path.rename(archived)
        return archived

    def append(self, recommendations: Iterable[Any], state: str = "GENERATED") -> pd.DataFrame:
        frame = self.to_frame(recommendations, state=state)
        archived = self._rotate_if_schema_changed()
        if archived is not None:
            print(
                f"recommendation schema changed; previous log archived to {archived.name}"
            )
        header = not self.path.exists()
        frame.to_csv(self.path, mode="a", header=header, index=False)
        return frame

    def read(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame(columns=LOG_COLUMNS)
        return pd.read_csv(self.path)
