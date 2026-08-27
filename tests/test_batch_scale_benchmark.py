"""Phase O - the batch scale-sweep harness (scripts/benchmark_scale.py).

Only the sweep's own bookkeeping is under test here (one result per
requested size, exact row count after tiling, sane non-negative timings) -
`optimize_price_batch` itself is already pinned against `optimize_price` by
`tests/test_batch_equivalence.py`. Fixtures are a small, self-contained,
deterministic demand model - no real dataset is used.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pricing_engine.optimization.optimizer import optimize_price_batch
from scripts.benchmark_scale import run_scale_sweep


class ConstantElasticityModel:
    """Q(p) = k * p^e - a closed-form, row-independent demand fixture."""

    def __init__(self, k: float = 1000.0, e: float = -2.0) -> None:
        self.k, self.e = k, e
        self.metadata = None

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        p = frame["effective_unit_price"].to_numpy(dtype="float64")
        return self.k * np.power(p, self.e)


def _make_base_pool(n: int = 6) -> tuple[pd.DataFrame, list[dict]]:
    rows, stats = [], []
    for i in range(n):
        price = round(2.0 + 0.35 * i, 2)
        rows.append(
            {
                "upc": 1000 + i,
                "store": 10,
                "week": 399,
                "week_start_date": pd.Timestamp("1997-05-01"),
                "effective_unit_price": price,
                "lag_price_1": price,
                "lag_price_2": price,
                "roll_mean_price_4": price,
                "series_reference_price": price,
                "decision_time_unit_cost": round(price * 0.6, 4),
                "lag_move_1": 20.0,
                "roll_mean_move_4": 20.0,
                "recorded_promotion_flag": 0,
                "descrip": f"TEST CEREAL {i}",
            }
        )
        stats.append(
            {"n_obs": 400, "n_distinct_prices": 25, "price_cv": 0.20,
             "price_min": price * 0.7, "price_max": price * 1.3}
        )
    return pd.DataFrame(rows), stats


@pytest.fixture
def base_pool() -> tuple[pd.DataFrame, list[dict]]:
    return _make_base_pool()


def test_scale_sweep_returns_one_result_per_requested_size(cfg, base_pool):
    base, stats = base_pool
    sizes = [len(base), 2 * len(base), 3 * len(base)]
    results = run_scale_sweep(ConstantElasticityModel(), base, stats, sizes, cfg=cfg)
    assert len(results) == len(sizes)
    assert [r["n_contexts"] for r in results] == sizes


def test_scale_sweep_output_is_row_count_correct_after_tiling(cfg, base_pool, monkeypatch):
    base, stats = base_pool
    seen_lengths: list[int] = []
    real_batch = optimize_price_batch

    def _spy(model, contexts, **kwargs):
        seen_lengths.append(len(contexts))
        return real_batch(model, contexts, **kwargs)

    monkeypatch.setattr("scripts.benchmark_scale.optimize_price_batch", _spy)
    sizes = [len(base), 2 * len(base) + 1, 5 * len(base) - 2]
    run_scale_sweep(ConstantElasticityModel(), base, stats, sizes, cfg=cfg)
    assert seen_lengths == sizes


def test_scale_sweep_timing_is_non_negative(cfg, base_pool):
    base, stats = base_pool
    results = run_scale_sweep(ConstantElasticityModel(), base, stats, [len(base), 2 * len(base)], cfg=cfg)
    for r in results:
        assert r["seconds"] >= 0
        assert r["contexts_per_second"] is None or r["contexts_per_second"] > 0
