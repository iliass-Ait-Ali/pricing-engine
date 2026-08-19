"""Phase M tests: drift metrics behave as documented."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pricing_engine.monitoring.drift import (
    feature_drift,
    ks_statistic,
    performance_when_outcomes_arrive,
    population_stability_index,
    prediction_distribution_check,
    schema_check,
)


def test_psi_is_zero_for_identical_distributions():
    rng = np.random.default_rng(0)
    x = rng.normal(size=20_000)
    assert population_stability_index(x, x) == pytest.approx(0.0, abs=1e-9)


def test_psi_grows_with_the_size_of_the_shift():
    rng = np.random.default_rng(0)
    ref = rng.normal(0, 1, 20_000)
    small = rng.normal(0.2, 1, 20_000)
    large = rng.normal(1.5, 1, 20_000)
    assert population_stability_index(ref, small) < population_stability_index(ref, large)


def test_psi_handles_empty_input():
    assert np.isnan(population_stability_index(np.array([]), np.array([1.0, 2.0])))


def test_ks_statistic_bounds():
    rng = np.random.default_rng(1)
    a = rng.normal(size=5_000)
    b = rng.normal(5, 1, 5_000)
    assert 0.0 <= ks_statistic(a, a) < 0.02
    assert ks_statistic(a, b) > 0.9


def test_schema_check_detects_missing_columns_and_types():
    frame = pd.DataFrame({"upc": [1, 2], "price": ["a", "b"]})
    result = schema_check(frame, {"upc": "int", "price": "float", "store": "int"})
    assert result["passed"] is False
    assert result["missing_columns"] == ["store"]
    assert "price" in result["unexpected_dtypes"]


def test_schema_check_passes_on_a_conforming_frame():
    frame = pd.DataFrame({"upc": [1, 2], "price": [1.0, 2.0]})
    assert schema_check(frame, {"upc": "int", "price": "float"})["passed"] is True


def test_feature_drift_ranks_the_most_shifted_feature_first():
    rng = np.random.default_rng(2)
    ref = pd.DataFrame({"stable": rng.normal(size=8000), "shifted": rng.normal(size=8000)})
    cur = pd.DataFrame({"stable": rng.normal(size=8000), "shifted": rng.normal(3, 1, 8000)})
    results = feature_drift(ref, cur, ["stable", "shifted"])
    assert results[0].feature == "shifted"
    assert results[0].band in {"moderate shift", "large shift"}
    assert results[-1].band == "stable"


def test_prediction_distribution_check_reports_both_periods():
    rng = np.random.default_rng(3)
    out = prediction_distribution_check(rng.gamma(2, 5, 5000), rng.gamma(2, 8, 5000))
    assert out["current_mean"] > out["reference_mean"]
    assert out["psi"] > 0
    assert 0 <= out["ks"] <= 1


def test_performance_windows_split_by_period():
    df = pd.DataFrame(
        {
            "y": [10.0, 12.0, 20.0, 22.0],
            "pred": [11.0, 11.0, 25.0, 20.0],
            "period": ["Q1", "Q1", "Q2", "Q2"],
        }
    )
    windows = performance_when_outcomes_arrive(df, "y", "pred", "period")
    assert {w.period for w in windows} == {"Q1", "Q2"}
    assert all(w.n == 2 for w in windows)
    assert windows[0].metrics["mae"] > 0
