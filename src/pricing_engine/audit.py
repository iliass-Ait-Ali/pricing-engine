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
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from pricing_engine.utils.io import ensure_dir, utc_now

LIFECYCLE_STATES = ("GENERATED", "REVIEWED", "APPROVED", "REJECTED", "PUBLISHED")

#: Legal transitions out of each lifecycle state. REJECTED and PUBLISHED are
#: terminal. A rejection or a re-review always starts a fresh REVIEWED cycle,
#: not a jump straight to APPROVED/PUBLISHED.
TRANSITIONS: dict[str, frozenset[str]] = {
    "GENERATED": frozenset({"REVIEWED"}),
    "REVIEWED": frozenset({"APPROVED", "REJECTED"}),
    "APPROVED": frozenset({"PUBLISHED"}),
    "REJECTED": frozenset(),
    "PUBLISHED": frozenset(),
}

TRANSITION_COLUMNS = [
    "transitioned_at_utc",
    "rec_id",
    "from_state",
    "to_state",
    "actor",
    "note",
]

#: ``rec_id`` is 12 hex characters, so roughly 0.6% of ids look like numbers
#: to a CSV reader ("659826115e98" parses as 6.6e106). Every read must force it
#: to stay text, or a transition recorded for that id is silently lost.
CSV_DTYPES = {"rec_id": str}

LOG_COLUMNS = [
    "rec_id",
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
                    "rec_id": uuid.uuid4().hex[:12],
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
        return pd.read_csv(self.path, dtype=CSV_DTYPES)

    def read_with_state(self, transitions: RecommendationTransitions) -> pd.DataFrame:
        """The log joined with the live lifecycle state from ``transitions``.

        ``lifecycle_state`` stays exactly what it always meant: the state a
        row was logged with (always ``GENERATED``, since ``append()`` never
        writes anything else). ``lifecycle_state_current`` is the read-time
        projection a human review workflow actually needs: the latest
        recorded transition for that ``rec_id``, or ``GENERATED`` when none
        exists yet.
        """
        log = self.read()
        if log.empty:
            log["lifecycle_state_current"] = pd.Series(dtype="object")
            return log
        if "rec_id" not in log.columns:
            # Rows logged before rec_id existed (an older, narrower schema on
            # disk that append() has not yet rotated): they predate the
            # review workflow and cannot be individually transitioned, but
            # they must still be readable rather than crashing the caller.
            log["rec_id"] = pd.NA
            log["lifecycle_state_current"] = "GENERATED"
            return log
        current = transitions.current_states()
        log["lifecycle_state_current"] = log["rec_id"].map(current).fillna("GENERATED")
        return log


@dataclass
class RecommendationTransitions:
    """Append-only companion log of lifecycle-state transitions.

    ``recommendation_log.csv`` is never rewritten after it is written - its
    own docstring promises "append-only". Every REVIEWED / APPROVED /
    REJECTED / PUBLISHED transition is instead recorded here as its own
    event, and "current state" is read back as a join (see
    :meth:`RecommendationLog.read_with_state`).
    """

    path: Path

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        ensure_dir(self.path.parent)

    def read(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame(columns=TRANSITION_COLUMNS)
        return pd.read_csv(self.path, dtype=CSV_DTYPES)

    def current_states(self) -> dict[str, str]:
        """``rec_id`` -> latest ``to_state``, taking the last transition per id."""
        frame = self.read()
        if frame.empty:
            return {}
        latest = frame.drop_duplicates(subset="rec_id", keep="last")
        return dict(zip(latest["rec_id"], latest["to_state"], strict=True))

    def append_transition(
        self,
        rec_id: str,
        *,
        to_state: str,
        actor: str | None = None,
        note: str | None = None,
        valid_rec_ids: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Validate and record one lifecycle transition for ``rec_id``.

        Raises ``ValueError`` on an unknown ``rec_id`` (when ``valid_rec_ids``
        is supplied, typically ``set(log.read()["rec_id"])``) or an illegal
        state-machine edge - it never silently no-ops, since a review
        decision must either take effect or be rejected loudly.
        """
        if valid_rec_ids is not None and rec_id not in valid_rec_ids:
            raise ValueError(f"Unknown rec_id {rec_id!r}: no such recommendation in the log")
        from_state = self.current_states().get(rec_id, "GENERATED")
        if to_state not in TRANSITIONS.get(from_state, frozenset()):
            raise ValueError(
                f"Illegal transition {from_state!r} -> {to_state!r} for rec_id {rec_id!r}; "
                f"allowed from {from_state!r}: {sorted(TRANSITIONS.get(from_state, ()))}"
            )
        row = {
            "transitioned_at_utc": utc_now(),
            "rec_id": rec_id,
            "from_state": from_state,
            "to_state": to_state,
            "actor": actor,
            "note": note,
        }
        frame = pd.DataFrame([row])[TRANSITION_COLUMNS]
        header = not self.path.exists()
        frame.to_csv(self.path, mode="a", header=header, index=False)
        return row
