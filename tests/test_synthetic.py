"""The synthetic generator behind the public demo and the ground-truth study."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pricing_engine.data.cleaning import build_canonical
from pricing_engine.data.schema import MOVEMENT_REQUIRED_COLUMNS, UPC_REQUIRED_COLUMNS
from pricing_engine.data.synthetic import (
    STORE_BASE,
    UPC_BASE,
    SyntheticSpec,
    generate_panel,
    oracle_price,
    write_panel,
)
from pricing_engine.data.validator import validate_processed

SMALL = SyntheticSpec(n_upcs=6, n_stores=5, n_weeks=120, seed=3)


@pytest.fixture(scope="module")
def panel():
    return generate_panel(SMALL)


def test_same_seed_same_panel(panel):
    again = generate_panel(SMALL)
    pd.testing.assert_frame_equal(panel.movement, again.movement)
    pd.testing.assert_frame_equal(panel.truth, again.truth)


def test_different_seed_different_panel(panel):
    other = generate_panel(SyntheticSpec(n_upcs=6, n_stores=5, n_weeks=120, seed=4))
    assert not panel.movement["move"].equals(other.movement["move"])


def test_raw_schema_matches_dominicks(panel):
    assert set(MOVEMENT_REQUIRED_COLUMNS) <= set(panel.movement.columns)
    assert set(UPC_REQUIRED_COLUMNS) <= set(panel.upc_meta.columns)


def test_ids_never_collide_with_dominicks(panel):
    assert (panel.movement["upc"] > UPC_BASE).all()
    assert (panel.movement["store"] > STORE_BASE).all()


def test_canonical_table_passes_every_validation_check(panel, cfg):
    canonical, _ = build_canonical(panel.movement, panel.upc_meta, cfg=cfg)
    checks = validate_processed(canonical, date_range=("1989-01-01", "1998-12-31"))
    assert all(c.passed for c in checks), [c.name for c in checks if not c.passed]


def test_truth_is_kept_out_of_the_raw_files(panel):
    raw_columns = set(panel.movement.columns) | set(panel.upc_meta.columns)
    assert "true_elasticity" not in raw_columns
    assert not (set(panel.truth.columns) - {"upc", "descrip"}) & raw_columns


def test_true_elasticities_are_elastic_and_bounded(panel):
    lo, hi = SMALL.elasticity_bounds
    e = panel.truth["true_elasticity"]
    assert e.between(lo, hi).all()
    assert (e < -1).all()  # finite profit optimum for every product


def test_recorded_promotions_follow_the_record_rate():
    full = generate_panel(SyntheticSpec(n_upcs=4, n_stores=4, n_weeks=100, seed=1))
    none = generate_panel(
        SyntheticSpec(n_upcs=4, n_stores=4, n_weeks=100, seed=1, promo_record_rate=0.0)
    )
    assert full.movement["sale"].notna().mean() > 0.05
    assert none.movement["sale"].notna().sum() == 0
    assert none.cost_path["true_promotion"].mean() > 0.05  # promotions still happen


def test_clean_panel_recovers_the_true_mean_elasticity():
    """No confounding, all promotions recorded: a controlled within-store fit finds the truth.

    The controls matter even here: in one finite panel the cost-driven price
    trend happens to correlate with the demand season, so dropping the
    seasonality and trend controls biases this same fit by about -0.3.
    """
    spec = SyntheticSpec(
        n_upcs=8, n_stores=6, n_weeks=150, seed=11, store_confounding=0.0,
        promo_record_rate=1.0, price_endogeneity=0.0, multipack_share=0.0, bad_row_share=0.0,
    )
    p = generate_panel(spec)
    m = p.movement[p.movement["move"] > 0].copy()
    m["log_q"] = np.log(m["move"])
    m["log_p"] = np.log(m["price"])
    m["promo"] = m["sale"].notna().astype(float)
    m["sin52"] = np.sin(2 * np.pi * m["week"] / 52)
    m["cos52"] = np.cos(2 * np.pi * m["week"] / 52)
    m["trend"] = m["week"] / 100
    regressors = ["log_p", "promo", "sin52", "cos52", "trend"]
    estimates = []
    for _, g in m.groupby("upc"):
        g = g.copy()
        for col in ["log_q", *regressors]:
            g[col] = g[col] - g.groupby("store")[col].transform("mean")
        beta = np.linalg.lstsq(g[regressors].to_numpy(), g["log_q"], rcond=None)[0]
        estimates.append(beta[0])
    assert abs(np.mean(estimates) - p.truth["true_elasticity"].mean()) < 0.15


def test_oracle_price_matches_the_closed_form():
    assert oracle_price(2.0, -2.0) == pytest.approx(4.0)
    assert oracle_price(np.array([1.0, 3.0]), np.array([-3.0, -1.5])) == pytest.approx([1.5, 9.0])
    assert np.isinf(oracle_price(2.0, -0.5))


def test_write_panel_separates_truth_from_raw(panel, tmp_path):
    source = write_panel(panel, tmp_path / "raw", tmp_path / "truth",
                         movement_file="m.csv", upc_file="u.csv")
    assert source["source"] == "synthetic"
    assert sorted(p.name for p in (tmp_path / "raw").iterdir()) == ["SOURCE.json", "m.csv", "u.csv"]
    assert (tmp_path / "truth" / "truth_products.csv").exists()
    reread = pd.read_csv(tmp_path / "raw" / "m.csv", encoding="latin-1")
    assert len(reread) == len(panel.movement)
