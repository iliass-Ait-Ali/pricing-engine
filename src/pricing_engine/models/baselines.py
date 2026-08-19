"""Naive demand baselines.

If a gradient-boosted model cannot beat "last week's units" on a temporal
split, it has not earned its place in a pricing system. These baselines make
that comparison honest.

All of them are price-blind by construction, which is exactly why they are
useless for optimization even when their accuracy is competitive - a point the
model-comparison report makes explicitly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


class BaselineModel:
    """Common interface: ``fit(df)`` then ``predict(df)``."""

    name = "baseline"
    price_aware = False

    def fit(self, df: pd.DataFrame) -> BaselineModel:  # noqa: D401
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        raise NotImplementedError


class LastWeekNaive(BaselineModel):
    """Predict last week's units for the same UPC x store."""

    name = "M0a last-week naive"

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        pred = df["lag_move_1"].to_numpy(dtype="float64")
        return np.nan_to_num(pred, nan=0.0).clip(min=0.0)


class RollingMeanNaive(BaselineModel):
    """Predict the mean of the previous 4 weeks."""

    name = "M0b rolling-mean(4) naive"

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        pred = df["roll_mean_move_4"].to_numpy(dtype="float64")
        fallback = np.nan_to_num(df["lag_move_1"].to_numpy(dtype="float64"), nan=0.0)
        pred = np.where(np.isfinite(pred), pred, fallback)
        return pred.clip(min=0.0)


class SeriesMean(BaselineModel):
    """Predict the training-period mean of each UPC x store series."""

    name = "M0c series historical mean"

    def __init__(self) -> None:
        self._series_mean: pd.Series | None = None
        self._global_mean: float = 0.0

    def fit(self, df: pd.DataFrame) -> SeriesMean:
        self._series_mean = df.groupby(["upc", "store"], observed=True)["move"].mean()
        self._global_mean = float(df["move"].mean())
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        if self._series_mean is None:
            raise RuntimeError("SeriesMean.fit must be called before predict")
        idx = pd.MultiIndex.from_arrays([df["upc"], df["store"]])
        pred = self._series_mean.reindex(idx).to_numpy(dtype="float64")
        return np.nan_to_num(pred, nan=self._global_mean).clip(min=0.0)


class SeasonalNaive(BaselineModel):
    """Predict the same series' units from 52 weeks earlier where available.

    Falls back to the rolling mean when a year-old observation is missing,
    which is common here because series start and stop.
    """

    name = "M0d seasonal naive (52w)"

    def __init__(self) -> None:
        self._history: pd.Series | None = None

    def fit(self, df: pd.DataFrame) -> SeasonalNaive:
        hist = df[["upc", "store", "week", "move"]].drop_duplicates(
            subset=["upc", "store", "week"], keep="last"
        )
        self._history = hist.set_index(["upc", "store", "week"])["move"].astype("float32")
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        if self._history is None:
            raise RuntimeError("SeasonalNaive.fit must be called before predict")
        idx = pd.MultiIndex.from_arrays([df["upc"], df["store"], df["week"] - 52])
        pred = self._history.reindex(idx).to_numpy(dtype="float64")
        fallback = RollingMeanNaive().predict(df)
        return np.where(np.isfinite(pred), pred, fallback).clip(min=0.0)


ALL_BASELINES = (LastWeekNaive, RollingMeanNaive, SeriesMean, SeasonalNaive)
