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
from pricing_engine.data.validator import validate_processed
from pricing_engine.economics.metrics import price_variation_summary
from pricing_engine.features.build import build_feature_table, temporal_split, training_frame
from pricing_engine.models.demand_model import DemandModel, load_model
from pricing_engine.models.metrics import evaluate
from pricing_engine.optimization.optimizer import optimize_price
from pricing_engine.simulation.counterfactual import simulate_price_grid


@pytest.fixture(scope="module")
def synthetic_raw() -> tuple[pd.DataFrame, pd.DataFrame]:
    """A small Dominick's-shaped panel with a known price response."""
    rng = np.random.default_rng(7)
    rows = []
    for upc in (1000000001, 1000000002, 1000000003):
        for store in (2, 5, 8):
            base_price = {1000000001: 3.0, 1000000002: 4.5, 1000000003: 2.0}[upc]
            for week in range(1, 121):
                promo = int(rng.random() < 0.12)
                price = base_price * (1.0 + 0.12 * np.sin(week / 5.0) - 0.15 * promo)
                units = np.exp(3.2 - 1.6 * np.log(price) + 0.35 * promo + rng.normal(0, 0.12))
                rows.append(
                    {
                        "store": store,
                        "upc": upc,
                        "week": week,
                        "move": float(max(1.0, round(units))),
                        "qty": 1.0,
                        "price": round(price, 2),
                        "sale": "B" if promo else None,
                        "profit": 25.0 + rng.normal(0, 1.0),
                        "ok": 1,
                    }
                )
    movement = pd.DataFrame(rows)
    meta = pd.DataFrame(
        {
            "com_code": [311, 311, 311],
            "upc": [1000000001, 1000000002, 1000000003],
            "descrip": ["SYNTH FLAKES", "SYNTH OATS", "SYNTH PUFFS"],
            "size": ["12 OZ", "18 OZ", "10 OZ"],
            "case": [12, 12, 12],
            "nitem": [1, 2, 3],
        }
    )
    return movement, meta


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
