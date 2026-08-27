"""Phase O - recommendation review/approval workflow.

`recommendation_log.csv` stays append-only (its own docstring promises that);
every lifecycle transition instead lands in a separate, also append-only,
`RecommendationTransitions` log, and "current state" is a read-time join.
These tests use plain dicts as stand-ins for `Recommendation` objects, since
`RecommendationLog.to_frame` accepts anything with an `as_dict()` or that is
dict-like - no optimizer machinery is needed to exercise the state machine.
"""

from __future__ import annotations

import pytest

from pricing_engine.audit import RecommendationLog, RecommendationTransitions


def _rec(upc: int = 1000, store: int = 10) -> dict:
    """A minimal stand-in for `Recommendation.as_dict()`.

    `RecommendationLog.to_frame` builds each row as
    ``{**k: d.get(k) for k in LOG_COLUMNS if k in d}`` and then selects the
    full `LOG_COLUMNS` from the resulting frame, so every column must be
    present in `d` (a real `Recommendation.as_dict()` is a dataclass `asdict`
    and always has every field, even when None).
    """
    return {
        "upc": upc,
        "store": store,
        "decision_week": 399,
        "decision_week_start_date": "1997-05-01",
        "objective": "gross_profit",
        "policy_profile": "standard",
        "model_version": "test-model",
        "price_response_method": "shrunk",
        "elasticity_used": None,
        "elasticity_source": None,
        "current_price": 3.0,
        "proposed_candidate_price": 3.2,
        "final_recommended_price": 3.0,
        "proposed_price_change_pct": 0.067,
        "price_change_pct": 0.0,
        "decision": "REVIEW_REQUIRED",
        "actionable": False,
        "predicted_units_current": 10.0,
        "predicted_units_recommended": 9.0,
        "expected_revenue_current": 30.0,
        "expected_revenue_recommended": 28.8,
        "expected_gross_profit_current": 7.5,
        "expected_gross_profit_recommended": 7.2,
        "model_internal_estimated_profit_uplift_pct": None,
        "model_internal_estimated_revenue_uplift_pct": None,
        "realisable_profit_uplift_pct": 0.0,
        "unit_cost_used": 2.0,
        "risk_level": "HIGH",
        "product_description": "TEST CEREAL",
        "reason_codes": ["HIGH_RISK_REVIEW_REQUIRED"],
        "risk_notes": [],
        "constraints": {},
    }


def test_rec_id_is_unique_per_row(tmp_path):
    log = RecommendationLog(tmp_path / "log.csv")
    frame = log.append([_rec(upc=1), _rec(upc=2), _rec(upc=3)])
    assert frame["rec_id"].nunique() == 3
    assert frame["rec_id"].str.len().eq(12).all()


def test_valid_transition_sequence_reaches_published(tmp_path):
    log = RecommendationLog(tmp_path / "log.csv")
    transitions = RecommendationTransitions(tmp_path / "transitions.csv")
    frame = log.append([_rec()])
    rec_id = frame["rec_id"].iloc[0]

    for to_state in ("REVIEWED", "APPROVED", "PUBLISHED"):
        transitions.append_transition(rec_id, to_state=to_state, actor="alice")

    assert transitions.current_states()[rec_id] == "PUBLISHED"
    joined = log.read_with_state(transitions)
    assert joined.loc[joined["rec_id"] == rec_id, "lifecycle_state_current"].iloc[0] == "PUBLISHED"
    # the original append-time column is untouched
    assert joined.loc[joined["rec_id"] == rec_id, "lifecycle_state"].iloc[0] == "GENERATED"


def test_invalid_transition_is_rejected(tmp_path):
    log = RecommendationLog(tmp_path / "log.csv")
    transitions = RecommendationTransitions(tmp_path / "transitions.csv")
    rec_id = log.append([_rec()])["rec_id"].iloc[0]

    with pytest.raises(ValueError):
        transitions.append_transition(rec_id, to_state="PUBLISHED")  # GENERATED -> PUBLISHED is illegal
    assert transitions.current_states() == {}


def test_reject_is_terminal(tmp_path):
    log = RecommendationLog(tmp_path / "log.csv")
    transitions = RecommendationTransitions(tmp_path / "transitions.csv")
    rec_id = log.append([_rec()])["rec_id"].iloc[0]

    transitions.append_transition(rec_id, to_state="REVIEWED")
    transitions.append_transition(rec_id, to_state="REJECTED", note="price too aggressive")
    assert transitions.current_states()[rec_id] == "REJECTED"

    with pytest.raises(ValueError):
        transitions.append_transition(rec_id, to_state="APPROVED")


def test_transition_only_affects_target_row(tmp_path):
    log = RecommendationLog(tmp_path / "log.csv")
    transitions = RecommendationTransitions(tmp_path / "transitions.csv")
    frame = log.append([_rec(upc=1), _rec(upc=2)])
    rec_a, rec_b = frame["rec_id"].tolist()

    transitions.append_transition(rec_a, to_state="REVIEWED")
    states = transitions.current_states()
    assert states[rec_a] == "REVIEWED"
    assert rec_b not in states

    joined = log.read_with_state(transitions)
    assert joined.loc[joined["rec_id"] == rec_b, "lifecycle_state_current"].iloc[0] == "GENERATED"


def test_transition_history_is_recorded(tmp_path):
    transitions = RecommendationTransitions(tmp_path / "transitions.csv")
    transitions.append_transition("abc123", to_state="REVIEWED", actor="alice", note="looks fine")
    transitions.append_transition("abc123", to_state="APPROVED", actor="bob")

    hist = transitions.read()
    assert len(hist) == 2
    assert hist["from_state"].tolist() == ["GENERATED", "REVIEWED"]
    assert hist["to_state"].tolist() == ["REVIEWED", "APPROVED"]
    assert hist["actor"].tolist() == ["alice", "bob"]
    assert hist["transitioned_at_utc"].notna().all()


def test_unknown_rec_id_raises_when_validated_against_the_log(tmp_path):
    log = RecommendationLog(tmp_path / "log.csv")
    transitions = RecommendationTransitions(tmp_path / "transitions.csv")
    log.append([_rec()])
    known_ids = set(log.read()["rec_id"])

    with pytest.raises(ValueError):
        transitions.append_transition(
            "does-not-exist", to_state="REVIEWED", valid_rec_ids=known_ids
        )


def test_read_with_state_tolerates_a_pre_rec_id_log_on_disk(tmp_path):
    """A log written before rec_id existed has no rec_id column until the
    next append() rotates it; reading it must not crash."""
    import pandas as pd

    path = tmp_path / "log.csv"
    pd.DataFrame([{"upc": 1, "store": 10, "decision": "KEEP_CURRENT"}]).to_csv(path, index=False)

    log = RecommendationLog(path)
    transitions = RecommendationTransitions(tmp_path / "transitions.csv")
    joined = log.read_with_state(transitions)
    assert (joined["lifecycle_state_current"] == "GENERATED").all()
    assert joined["rec_id"].isna().all()


def test_read_with_state_defaults_to_generated_when_no_transitions_exist(tmp_path):
    log = RecommendationLog(tmp_path / "log.csv")
    transitions = RecommendationTransitions(tmp_path / "transitions.csv")
    log.append([_rec()])

    joined = log.read_with_state(transitions)
    assert (joined["lifecycle_state_current"] == "GENERATED").all()
