"""Forecast-accuracy metrics for weekly unit demand.

WAPE is the headline metric: weekly unit sales are small counts with many low
values, so MAPE explodes and is not reported as a primary number.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _arrays(y_true, y_pred) -> tuple[np.ndarray, np.ndarray]:
    yt = np.asarray(y_true, dtype="float64")
    yp = np.asarray(y_pred, dtype="float64")
    if yt.shape != yp.shape:
        raise ValueError(f"shape mismatch: {yt.shape} vs {yp.shape}")
    return yt, yp


def mae(y_true, y_pred) -> float:
    yt, yp = _arrays(y_true, y_pred)
    return float(np.mean(np.abs(yt - yp)))


def rmse(y_true, y_pred) -> float:
    yt, yp = _arrays(y_true, y_pred)
    return float(np.sqrt(np.mean((yt - yp) ** 2)))


def wape(y_true, y_pred) -> float:
    """Weighted absolute percentage error = sum|e| / sum|y|."""
    yt, yp = _arrays(y_true, y_pred)
    denom = np.sum(np.abs(yt))
    if denom == 0:
        return float("nan")
    return float(np.sum(np.abs(yt - yp)) / denom)


def bias(y_true, y_pred) -> float:
    """Mean signed error (positive = over-forecast)."""
    yt, yp = _arrays(y_true, y_pred)
    return float(np.mean(yp - yt))


def smape(y_true, y_pred) -> float:
    yt, yp = _arrays(y_true, y_pred)
    denom = (np.abs(yt) + np.abs(yp)) / 2.0
    mask = denom > 0
    if not mask.any():
        return float("nan")
    return float(np.mean(np.abs(yt[mask] - yp[mask]) / denom[mask]))


def evaluate(y_true, y_pred) -> dict[str, float]:
    """All headline metrics in one dict."""
    return {
        "mae": mae(y_true, y_pred),
        "rmse": rmse(y_true, y_pred),
        "wape": wape(y_true, y_pred),
        "bias": bias(y_true, y_pred),
        "smape": smape(y_true, y_pred),
        "n": int(np.asarray(y_true).size),
    }


def evaluate_by(
    df: pd.DataFrame, y_col: str, pred_col: str, group_col: str, *, top: int | None = None
) -> pd.DataFrame:
    """Segment-level error table (by store, UPC, promotion state, ...)."""
    rows: list[dict[str, Any]] = []
    for key, sub in df.groupby(group_col, observed=True):
        m = evaluate(sub[y_col], sub[pred_col])
        m[group_col] = key
        m["units"] = float(sub[y_col].sum())
        rows.append(m)
    out = pd.DataFrame(rows).sort_values("units", ascending=False)
    cols = [group_col, "n", "units", "mae", "rmse", "wape", "bias"]
    out = out[cols]
    return out.head(top) if top else out
