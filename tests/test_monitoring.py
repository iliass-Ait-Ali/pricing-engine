"""Phase M tests: drift metrics behave as documented."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pricing_engine.monitoring.drift import (
    append_history,
    evaluate_alerts,
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


# ---------------------------------------------------------------------------
# Phase O: config-driven PSI bands, local threshold alerting, history file.
# ---------------------------------------------------------------------------
def test_band_uses_custom_thresholds_when_provided():
    tight_bands = ((0.01, "stable"), (0.02, "moderate shift"), (float("inf"), "large shift"))
    rng = np.random.default_rng(4)
    ref = pd.DataFrame({"x": rng.normal(size=8000)})
    cur = pd.DataFrame({"x": rng.normal(0.15, 1, 8000)})
    default_band = feature_drift(ref, cur, ["x"])[0].band
    tight_band = feature_drift(ref, cur, ["x"], bands=tight_bands)[0].band
    assert default_band == "stable"
    assert tight_band in {"moderate shift", "large shift"}
    assert tight_band != default_band


def _payload(*, schema_passed=True, psi=0.01, wape=0.40, bias=-1.0) -> dict:
    return {
        "schema": {"passed": schema_passed, "missing_columns": [], "unexpected_dtypes": {}},
        "prediction_drift": {"psi": psi},
        "performance_by_quarter": [{"period": "Q1", "wape": wape, "bias": bias}],
    }


THRESHOLDS = {
    "psi_alert": 0.25,
    "wape_alert": 0.70,
    "bias_abs_alert": 10.0,
    "schema_check_is_hard_fail": True,
}


def test_evaluate_alerts_passes_when_metrics_are_within_thresholds():
    result = evaluate_alerts(_payload(), THRESHOLDS)
    assert result.passed is True
    assert result.alerts == []
    assert result.checked


def test_evaluate_alerts_flags_a_psi_breach():
    result = evaluate_alerts(_payload(psi=0.30), THRESHOLDS)
    assert result.passed is False
    assert any("PSI" in a for a in result.alerts)


def test_evaluate_alerts_flags_a_performance_breach():
    result = evaluate_alerts(_payload(wape=0.9, bias=-15.0), THRESHOLDS)
    assert result.passed is False
    assert any("WAPE" in a for a in result.alerts)
    assert any("bias" in a for a in result.alerts)


def test_evaluate_alerts_hard_fails_on_schema_break():
    result = evaluate_alerts(_payload(schema_passed=False), THRESHOLDS)
    assert result.passed is False
    assert any("schema" in a for a in result.alerts)


def test_evaluate_alerts_ignores_schema_break_when_not_configured_as_hard_fail():
    lenient = {**THRESHOLDS, "schema_check_is_hard_fail": False}
    result = evaluate_alerts(_payload(schema_passed=False), lenient)
    assert result.passed is True


def test_append_history_grows_across_repeated_appends_without_losing_prior_rows(tmp_path):
    path = tmp_path / "history.jsonl"
    append_history(path, {"run": 1})
    append_history(path, {"run": 2})
    append_history(path, {"run": 3})

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    import json

    assert [json.loads(line)["run"] for line in lines] == [1, 2, 3]
