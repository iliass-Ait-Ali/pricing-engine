"""Phase M / Task 10 - decision-state invariants.

The dangerous failure mode of a pricing engine is not a wrong number, it is a
proposal that gets read as a decision. These tests pin the contract between
``risk_level``, ``decision``, ``actionable``, ``proposed_candidate_price`` and
``final_recommended_price`` so that no report, dashboard or API response can
present a non-actionable proposal as a business price.

Invariants under the DEFAULT policy profiles (`standard`, `conservative`):

    HIGH risk           -> actionable is False
    REVIEW_REQUIRED     -> final_recommended_price == current_price
    KEEP_CURRENT        -> final_recommended_price == current_price
    RECOMMEND_CHANGE    -> actionable is True
    not actionable      -> price_change_pct == 0 and realisable uplift == 0

Only the explicitly labelled DEMO `aggressive` profile may act on HIGH risk, and
that is asserted too so the exception stays visible.
"""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
import pytest

from pricing_engine.economics.elasticity_store import ElasticityTable
from pricing_engine.models.hybrid import HybridPricingModel
from pricing_engine.optimization.optimizer import DecisionState, optimize_price

DEFAULT_PROFILES = ["standard", "conservative"]
ALL_PROFILES = [*DEFAULT_PROFILES, "aggressive"]


class Baseline:
    metadata = None
    prediction_cap_ = None

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.full(len(frame), 120.0)


def table_with(upc: int, epsilon: float, pooled: float = -2.03) -> ElasticityTable:
    return ElasticityTable(
        pooled=pooled,
        pooled_se=0.11,
        products=pd.DataFrame(
            {"upc": [upc], "elasticity_final": [epsilon], "elasticity_source": ["shrunk_product"]}
        ),
        metadata={"kind": "test"},
    )


def make_context(price: float, cost: float, upc: int = 111) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "upc": upc,
                "store": 2,
                "week": 380,
                "week_start_date": pd.Timestamp("1996-06-01"),
                "effective_unit_price": price,
                "lag_price_1": price,
                "lag_price_2": price,
                "roll_mean_price_4": price,
                "series_reference_price": price,
                "decision_time_unit_cost": cost,
                "lag_move_1": 120.0,
                "roll_mean_move_4": 120.0,
                "recorded_promotion_flag": 0,
                "descrip": "TEST FLAKES",
            }
        ]
    )


#: Series histories spanning every branch of the risk layer.
STATS = {
    "low_risk_wide_support": {"n_obs": 400, "n_distinct_prices": 40, "price_cv": 0.25,
                              "price_min": 1.0, "price_max": 12.0},
    "low_risk_narrow_support": {"n_obs": 400, "n_distinct_prices": 40, "price_cv": 0.25,
                                "price_min": 3.90, "price_max": 4.10},
    "medium_risk": {"n_obs": 100, "n_distinct_prices": 10, "price_cv": 0.20,
                    "price_min": 1.0, "price_max": 12.0},
    "high_risk_thin": {"n_obs": 45, "n_distinct_prices": 6, "price_cv": 0.20,
                       "price_min": 1.0, "price_max": 12.0},
    "high_risk_no_support": {"n_obs": 400, "n_distinct_prices": 40, "price_cv": 0.25,
                             "price_min": None, "price_max": None},
    "ineligible": {"n_obs": 15, "n_distinct_prices": 2, "price_cv": 0.01,
                   "price_min": 1.0, "price_max": 12.0},
}

CASES = list(itertools.product(sorted(STATS), [-0.5, -1.5, -2.03, -4.0], [(4.00, 2.50), (4.00, 3.90)]))


def _recommend(cfg, profile, stats_name, epsilon, price, cost):
    model = HybridPricingModel(
        Baseline(), method="shrunk", elasticity_table=table_with(111, epsilon), cfg=cfg
    )
    return optimize_price(
        model,
        make_context(price, cost),
        cfg=cfg,
        policy_profile=profile,
        objective="gross_profit",
        series_stats=STATS[stats_name],
    )


# ---------------------------------------------------------------------------
# the invariants, over every combination
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("profile", DEFAULT_PROFILES)
@pytest.mark.parametrize(("stats_name", "epsilon", "economics"), CASES)
def test_decision_state_invariants_hold_under_default_policies(
    cfg, profile, stats_name, epsilon, economics
):
    price, cost = economics
    rec = _recommend(cfg, profile, stats_name, epsilon, price, cost)

    # 1. HIGH risk is never actionable under a default profile.
    if rec.risk_level == "HIGH":
        assert rec.actionable is False, "a HIGH-risk context became actionable"

    # 2. actionable is exactly RECOMMEND_CHANGE.
    assert rec.actionable == (rec.decision == DecisionState.RECOMMEND_CHANGE.value)

    # 3. a non-actionable decision ships the current price, whatever was proposed.
    if not rec.actionable:
        assert rec.final_recommended_price == pytest.approx(rec.current_price)
        assert rec.price_change_pct == pytest.approx(0.0)
        assert rec.realisable_profit_uplift_pct == pytest.approx(0.0)

    # 4. KEEP_CURRENT proposes nothing at all.
    if rec.decision == DecisionState.KEEP_CURRENT.value:
        assert rec.proposed_candidate_price == pytest.approx(rec.current_price)

    # 5. RECOMMEND_CHANGE ships the proposal, and it is a real change.
    if rec.decision == DecisionState.RECOMMEND_CHANGE.value:
        assert rec.final_recommended_price == pytest.approx(rec.proposed_candidate_price)
        assert rec.final_recommended_price != pytest.approx(rec.current_price)

    # 6. the price is always inside the feasible interval that was reported.
    bounds = rec.constraints.get("bounds")
    if bounds and rec.actionable:
        assert bounds[0] - 1e-9 <= rec.final_recommended_price <= bounds[1] + 1e-9


@pytest.mark.parametrize("profile", ALL_PROFILES)
@pytest.mark.parametrize(("stats_name", "epsilon", "economics"), CASES)
def test_review_required_never_moves_the_final_price(cfg, profile, stats_name, epsilon, economics):
    price, cost = economics
    rec = _recommend(cfg, profile, stats_name, epsilon, price, cost)
    if rec.decision == DecisionState.REVIEW_REQUIRED.value:
        assert rec.actionable is False
        assert rec.final_recommended_price == pytest.approx(rec.current_price)
        # ... but the proposal itself is preserved for the human reviewer.
        assert "HIGH_RISK_REVIEW_REQUIRED" in rec.reason_codes


def test_review_required_keeps_the_proposal_visible(cfg):
    """A REVIEW_REQUIRED row must still carry what the optimizer wanted to do."""
    rec = _recommend(cfg, "standard", "high_risk_thin", -2.03, 4.00, 2.50)
    assert rec.decision == DecisionState.REVIEW_REQUIRED.value
    assert rec.proposed_candidate_price != pytest.approx(rec.current_price)
    assert rec.final_recommended_price == pytest.approx(rec.current_price)


def test_only_the_demo_aggressive_profile_can_act_on_high_risk(cfg):
    """The exception exists, is explicit, and is confined to the DEMO profile."""
    args = ("high_risk_thin", -2.03, 4.00, 2.50)
    for profile in DEFAULT_PROFILES:
        assert _recommend(cfg, profile, *args).actionable is False
    aggressive = _recommend(cfg, "aggressive", *args)
    assert aggressive.risk_level == "HIGH"
    assert aggressive.actionable is True
    assert aggressive.decision == DecisionState.RECOMMEND_CHANGE.value


def test_conservative_profile_keeps_current_instead_of_escalating(cfg):
    """`conservative` sets high_risk_action = keep_current, so no proposal survives."""
    rec = _recommend(cfg, "conservative", "high_risk_thin", -2.03, 4.00, 2.50)
    assert rec.decision == DecisionState.KEEP_CURRENT.value
    assert rec.proposed_candidate_price == pytest.approx(rec.current_price)
    assert "HIGH_RISK_KEEP_CURRENT" in rec.reason_codes


def test_ineligible_context_is_screened_out_before_any_proposal(cfg):
    rec = _recommend(cfg, "standard", "ineligible", -2.03, 4.00, 2.50)
    assert rec.decision == DecisionState.KEEP_CURRENT.value
    assert rec.proposed_candidate_price == pytest.approx(rec.current_price)
    assert {"INSUFFICIENT_HISTORY", "INSUFFICIENT_PRICE_VARIATION"} & set(rec.reason_codes)


def test_audit_log_records_both_prices(cfg, tmp_path):
    """The append-only log must keep the proposal AND the final price apart."""
    from pricing_engine.audit import RecommendationLog

    rec = _recommend(cfg, "standard", "high_risk_thin", -2.03, 4.00, 2.50)
    log = RecommendationLog(tmp_path / "log.csv")
    frame = log.append([rec])
    assert float(frame["proposed_candidate_price"].iloc[0]) == pytest.approx(
        rec.proposed_candidate_price
    )
    assert float(frame["final_recommended_price"].iloc[0]) == pytest.approx(rec.current_price)
    assert float(frame["realisable_profit_uplift_pct"].iloc[0]) == pytest.approx(0.0)


def test_audit_log_rotates_when_the_recommendation_schema_changes(cfg, tmp_path):
    """An append-only CSV cannot hold two schemas.

    Phase M added `proposed_candidate_price` / `final_recommended_price`, and
    appending the wider rows under the older header produced a file no reader
    could parse - the failure surfaced in the dashboard, far from its cause. The
    log now archives the old file instead of corrupting it.
    """
    import pandas as pd

    from pricing_engine.audit import LOG_COLUMNS, RecommendationLog

    path = tmp_path / "log.csv"
    pd.DataFrame(columns=LOG_COLUMNS[:-3]).to_csv(path, index=False)   # older, narrower schema

    rec = _recommend(cfg, "standard", "low_risk_wide_support", -2.03, 4.00, 2.50)
    RecommendationLog(path).append([rec])

    archived = list(tmp_path.glob("log__schema_*.csv"))
    assert len(archived) == 1, "the previous log must be archived, not overwritten"
    reread = pd.read_csv(path)              # must parse cleanly
    assert list(reread.columns) == LOG_COLUMNS
    assert len(reread) == 1
