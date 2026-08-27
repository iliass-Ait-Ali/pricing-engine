"""Minimal, honest monitoring primitives.

Scope is deliberately small: schema conformance, feature drift, prediction
drift, and accuracy once outcomes arrive. This is not a production MLOps
platform and does not pretend to be one.

Interpretation caveats are part of the API surface, because PSI thresholds in
particular are folklore:

* PSI has no distributional theory behind the usual 0.1 / 0.25 cut-offs; they
  are industry rules of thumb, sensitive to bin count and sample size.
* On a 4.7 M-row panel almost any KS test rejects equality of distributions;
  the *statistic* is informative, the p-value is not.
* Drift is a prompt to investigate, never by itself proof of model failure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

#: Conventional PSI bands (industry rules of thumb, not statistical thresholds).
PSI_BANDS = ((0.10, "stable"), (0.25, "moderate shift"), (float("inf"), "large shift"))


@dataclass
class DriftResult:
    feature: str
    psi: float
    ks: float
    reference_mean: float
    current_mean: float
    band: str
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "psi": self.psi,
            "ks": self.ks,
            "reference_mean": self.reference_mean,
            "current_mean": self.current_mean,
            "band": self.band,
            "note": self.note,
        }


def _band(psi: float, bands: tuple = PSI_BANDS) -> str:
    for threshold, label in bands:
        if psi < threshold:
            return label
    return "large shift"


def population_stability_index(
    reference: np.ndarray, current: np.ndarray, *, bins: int = 10, epsilon: float = 1e-6
) -> float:
    """PSI between a reference and a current sample, using reference quantile bins."""
    ref = np.asarray(reference, dtype="float64")
    cur = np.asarray(current, dtype="float64")
    ref = ref[np.isfinite(ref)]
    cur = cur[np.isfinite(cur)]
    if ref.size == 0 or cur.size == 0:
        return float("nan")

    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if edges.size < 3:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf

    ref_share = np.histogram(ref, bins=edges)[0] / ref.size
    cur_share = np.histogram(cur, bins=edges)[0] / cur.size
    ref_share = np.clip(ref_share, epsilon, None)
    cur_share = np.clip(cur_share, epsilon, None)
    return float(np.sum((cur_share - ref_share) * np.log(cur_share / ref_share)))


def ks_statistic(reference: np.ndarray, current: np.ndarray) -> float:
    """Two-sample Kolmogorov-Smirnov statistic (the D value only)."""
    from scipy.stats import ks_2samp

    ref = np.asarray(reference, dtype="float64")
    cur = np.asarray(current, dtype="float64")
    ref = ref[np.isfinite(ref)]
    cur = cur[np.isfinite(cur)]
    if ref.size == 0 or cur.size == 0:
        return float("nan")
    return float(ks_2samp(ref, cur).statistic)


def feature_drift(
    reference: pd.DataFrame,
    current: pd.DataFrame,
    features: list[str],
    *,
    bins: int = 10,
    bands: tuple = PSI_BANDS,
) -> list[DriftResult]:
    """PSI + KS for each numeric feature."""
    out: list[DriftResult] = []
    for col in features:
        if col not in reference.columns or col not in current.columns:
            continue
        ref = reference[col].to_numpy(dtype="float64", na_value=np.nan)
        cur = current[col].to_numpy(dtype="float64", na_value=np.nan)
        psi = population_stability_index(ref, cur, bins=bins)
        out.append(
            DriftResult(
                feature=col,
                psi=psi,
                ks=ks_statistic(ref, cur),
                reference_mean=float(np.nanmean(ref)),
                current_mean=float(np.nanmean(cur)),
                band=_band(psi, bands),
            )
        )
    return sorted(out, key=lambda r: (-r.psi if np.isfinite(r.psi) else 0))


def schema_check(frame: pd.DataFrame, expected: dict[str, str]) -> dict[str, Any]:
    """Verify that required columns exist with compatible dtypes."""
    missing = [c for c in expected if c not in frame.columns]
    wrong_type = {
        c: f"{frame[c].dtype} (expected {t})"
        for c, t in expected.items()
        if c in frame.columns and not str(frame[c].dtype).startswith(t)
    }
    nulls = {c: float(frame[c].isna().mean()) for c in expected if c in frame.columns and frame[c].isna().any()}
    return {
        "passed": not missing and not wrong_type,
        "missing_columns": missing,
        "unexpected_dtypes": wrong_type,
        "null_share": nulls,
        "rows": int(len(frame)),
    }


def prediction_distribution_check(
    reference_predictions: np.ndarray,
    current_predictions: np.ndarray,
    *,
    bins: int = 10,
    bands: tuple = PSI_BANDS,
) -> dict[str, Any]:
    """Compare the distribution of predictions between two periods."""
    ref = np.asarray(reference_predictions, dtype="float64")
    cur = np.asarray(current_predictions, dtype="float64")
    psi = population_stability_index(ref, cur, bins=bins)
    return {
        "psi": psi,
        "band": _band(psi, bands),
        "ks": ks_statistic(ref, cur),
        "reference_mean": float(np.nanmean(ref)),
        "current_mean": float(np.nanmean(cur)),
        "reference_p95": float(np.nanpercentile(ref, 95)),
        "current_p95": float(np.nanpercentile(cur, 95)),
        "share_zero_reference": float(np.mean(ref <= 0)),
        "share_zero_current": float(np.mean(cur <= 0)),
    }


@dataclass
class PerformanceWindow:
    """Accuracy once realised outcomes become available for a served period."""

    period: str
    metrics: dict[str, float] = field(default_factory=dict)
    n: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"period": self.period, "n": self.n, **self.metrics}


def performance_when_outcomes_arrive(
    frame: pd.DataFrame, y_col: str, pred_col: str, period_col: str
) -> list[PerformanceWindow]:
    """Recompute accuracy per period once actuals exist (the only real alarm)."""
    from pricing_engine.models.metrics import evaluate

    windows = []
    for period, sub in frame.groupby(period_col, observed=True):
        windows.append(
            PerformanceWindow(period=str(period), metrics=evaluate(sub[y_col], sub[pred_col]), n=int(len(sub)))
        )
    return windows


# ---------------------------------------------------------------------------
# Post-freeze engineering (Phase O): local threshold alerting + a history
# file. Neither is a production MLOps platform: thresholds are LOCAL
# (configs/config.yaml `monitoring.alerts`), not agreed with a business or
# fitted against realised out-of-sample error, and "alerting" means a
# structured, persisted result plus a non-zero process exit code - no
# network, no paging. See reports/MONITORING_DESIGN.md.
# ---------------------------------------------------------------------------
@dataclass
class AlertResult:
    passed: bool
    alerts: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "alerts": self.alerts, "checked": self.checked}


def evaluate_alerts(payload: dict[str, Any], thresholds: dict[str, Any]) -> AlertResult:
    """Compare a monitoring payload (the shape `scripts/monitor.py` builds)
    against config-driven local thresholds. Pure comparison - no I/O."""
    alerts: list[str] = []
    checked: list[str] = []

    schema = payload.get("schema")
    if schema is not None:
        checked.append("schema.passed")
        if thresholds.get("schema_check_is_hard_fail", True) and not schema.get("passed", True):
            alerts.append(
                f"schema check failed: missing={schema.get('missing_columns')} "
                f"wrong_dtypes={schema.get('unexpected_dtypes')}"
            )

    pred_drift = payload.get("prediction_drift")
    if pred_drift is not None and "psi_alert" in thresholds:
        checked.append("prediction_drift.psi")
        psi = pred_drift.get("psi")
        if psi is not None and np.isfinite(psi) and psi >= thresholds["psi_alert"]:
            alerts.append(f"prediction drift PSI {psi:.4f} >= alert threshold {thresholds['psi_alert']}")

    for window in payload.get("performance_by_quarter", []):
        period = window.get("period", "?")
        wape = window.get("wape")
        if wape is not None and "wape_alert" in thresholds:
            checked.append(f"performance[{period}].wape")
            if wape >= thresholds["wape_alert"]:
                alerts.append(f"period {period}: WAPE {wape:.4f} >= alert threshold {thresholds['wape_alert']}")
        bias = window.get("bias")
        if bias is not None and "bias_abs_alert" in thresholds:
            checked.append(f"performance[{period}].bias")
            if abs(bias) >= thresholds["bias_abs_alert"]:
                alerts.append(
                    f"period {period}: |bias| {abs(bias):.4f} >= alert threshold {thresholds['bias_abs_alert']}"
                )

    return AlertResult(passed=not alerts, alerts=alerts, checked=checked)


def append_history(path: str | Path, record: dict[str, Any]) -> None:
    """Append one JSON line to a JSONL history file (create parents as needed).

    Append-only by construction: no read-modify-write, so concurrent runs
    cannot corrupt earlier lines the way a rewritten file could.
    """
    from pricing_engine.utils.io import _json_default

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=_json_default) + "\n")
