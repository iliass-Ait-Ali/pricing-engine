"""End-to-end integration test on a deterministic synthetic fixture.

    raw movement + metadata
      -> canonical table
      -> features
      -> trained model
      -> counterfactual simulation
      -> constrained optimization
      -> audit log

Synthetic data is used here (and only here) so the whole chain can be exercised
in CI without the licensed Dominick's files. The portfolio analysis itself
always runs on the real data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pricing_engine.audit import LIFECYCLE_STATES, RecommendationLog
from pricing_engine.data.cleaning import build_canonical
from pricing_engine.data.synthetic import SyntheticSpec, generate_panel
from pricing_engine.data.validator import validate_processed
from pricing_engine.economics.metrics import price_variation_summary
from pricing_engine.features.build import build_feature_table, temporal_split, training_frame
from pricing_engine.models.demand_model import DemandModel, load_model
from pricing_engine.models.metrics import evaluate
from pricing_engine.optimization.optimizer import optimize_price
from pricing_engine.simulation.counterfactual import simulate_price_grid


@pytest.fixture(scope="module")
def synthetic_raw() -> tuple[pd.DataFrame, pd.DataFrame]:
    """A small Dominick's-shaped panel with a known price response.

    Built by the same generator as the public demo
    (:mod:`pricing_engine.data.synthetic`), without its deliberate data-quality
    noise so every raw row survives cleaning.
    """
    panel = generate_panel(
        SyntheticSpec(
            n_upcs=3, n_stores=3, n_weeks=120, seed=7,
            late_entry_share=0.0, multipack_share=0.0, bad_row_share=0.0,
        )
    )
    return panel.movement, panel.upc_meta


def test_full_pipeline(synthetic_raw, cfg, tmp_path):
    movement, meta = synthetic_raw

    # 1. canonical table -------------------------------------------------
    canonical, audit = build_canonical(movement, meta, cfg=cfg)
    assert audit["processed_rows"] == len(movement)
    checks = validate_processed(canonical)
    assert all(c.passed for c in checks), [c.name for c in checks if not c.passed]

    # 2. features --------------------------------------------------------
    feats = build_feature_table(canonical, cfg=cfg)
    usable = training_frame(feats)
    assert usable["lag_move_1"].notna().all()
    train, valid, test, split = temporal_split(usable, cfg=cfg)
    assert train["week"].max() < test["week"].min()

    # 3. model -----------------------------------------------------------
    model = DemandModel("ridge_loglog", cfg=cfg).fit(train)
    metrics = evaluate(test["move"], model.predict(test))
    assert 0 < metrics["wape"] < 0.5  # the fixture is learnable
    elasticity = model.implied_elasticity(test, sample=None)
    assert elasticity["median"] < 0  # the model learned a downward demand curve

    # 4. persistence -----------------------------------------------------
    path = tmp_path / "model.joblib"
    model.save(path)
    reloaded = load_model(path, cfg=cfg)
    assert np.allclose(model.predict(test), reloaded.predict(test))

    # 5. simulation ------------------------------------------------------
    stats = price_variation_summary(canonical, ["upc", "store"])
    row = test.head(1)
    key = (int(row["upc"].iloc[0]), int(row["store"].iloc[0]))
    srow = stats[(stats["upc"] == key[0]) & (stats["store"] == key[1])].iloc[0]
    cost = float(row["decision_time_unit_cost"].iloc[0])
    grid = np.round(np.linspace(float(row["effective_unit_price"].iloc[0]) * 0.8,
                                float(row["effective_unit_price"].iloc[0]) * 1.2, 15), 2)
    sim = simulate_price_grid(reloaded, row, grid, unit_cost=cost)
    assert len(sim) == len(np.unique(grid))
    assert sim["unit_cost"].nunique() == 1  # cost held fixed
    assert (np.diff(sim["predicted_units"]) <= 1e-9).all()  # demand decreasing

    # 6. optimization ----------------------------------------------------
    rec = optimize_price(
        reloaded,
        row,
        cfg=cfg,
        policy_profile="standard",
        objective="gross_profit",
        series_stats={
            "n_obs": int(srow["n_obs"]),
            "n_distinct_prices": int(srow["n_distinct_prices"]),
            "price_cv": float(srow["price_cv"]),
            "price_min": float(srow["price_min"]),
            "price_max": float(srow["price_max"]),
        },
    )
    assert rec.decision in {"RECOMMEND_CHANGE", "KEEP_CURRENT", "REVIEW_REQUIRED"}
    assert rec.actionable is (rec.decision == "RECOMMEND_CHANGE")
    assert rec.proposed_candidate_price > 0
    assert rec.reason_codes
    low, high = rec.constraints["bounds"]
    assert low <= rec.proposed_candidate_price <= high

    # 7. audit log -------------------------------------------------------
    log = RecommendationLog(tmp_path / "log.csv")
    log.append([rec], state="GENERATED")
    stored = log.read()
    assert len(stored) == 1
    assert stored["lifecycle_state"].iloc[0] in LIFECYCLE_STATES
    assert float(stored["final_recommended_price"].iloc[0]) == pytest.approx(rec.final_recommended_price)
    assert stored["model_version"].isna().all() or stored["model_version"].notna().all()


def test_audit_log_rejects_unknown_lifecycle_state(tmp_path):
    log = RecommendationLog(tmp_path / "log.csv")
    with pytest.raises(ValueError):
        log.to_frame([], state="SHIPPED")
