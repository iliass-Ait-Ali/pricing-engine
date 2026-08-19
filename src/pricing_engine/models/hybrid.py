"""Hybrid pricing model: forecasting and price response are separated.

Phase L. The selected demand model wins on WAPE, but WAPE is measured at the
prices that were actually charged. Its *implied* elasticity (about -3.10) is
materially steeper than the controlled econometric estimate (-1.91 to -2.42),
so using it directly for counterfactuals bakes that bias into every
recommendation.

The hybrid separates the two jobs:

    Q(p) = Q_hat(p0) * (p / p0) ** epsilon

where

    p0        = the reference (current) price of the decision context
    Q_hat(p0) = the ML model's baseline demand prediction AT the reference
                price - all context, seasonality, promotion and lag structure
                comes from here
    epsilon   = a separately estimated price elasticity (pooled or shrunk
                per-UPC), fitted on TRAINING-PERIOD data only

Consequences:

* at ``p = p0`` the hybrid reproduces the base model's prediction exactly, so
  the baseline forecast at the *reference price* is preserved bit for bit
  (``tests/test_phase_l_pricing.py`` pins this to numerical tolerance);
* the price counterfactual is governed by an estimate whose provenance,
  standard error and shrinkage are known.

**What is NOT claimed.** Preserving the prediction at ``p0`` says nothing about
accuracy at any other price. Away from the reference price the hybrid's demand
curve is the elasticity model's, not the forecaster's, and its counterfactual
accuracy is a separate empirical question - measured out of time in
``reports/16_OUT_OF_TIME_PRICE_RESPONSE.md``, not assumed here. "No forecast
skill is lost" would be a claim about all prices and is not supported.

The native ML response is kept as ``PriceResponseMethod.ML`` and is used as the
benchmark in ``reports/08_PRICE_RESPONSE_COMPARISON.md``.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from pricing_engine.config import Config, load_config
from pricing_engine.economics.elasticity_store import ElasticityError, ElasticityTable
from pricing_engine.features.build import recompute_price_features

REFERENCE_PRICE_COLUMN = "reference_price"


class PriceResponseMethod(StrEnum):
    ML = "ml"
    POOLED = "pooled"
    SHRUNK = "shrunk"


class HybridModelError(RuntimeError):
    """Raised when the hybrid pricing model cannot score a frame."""


def attach_reference_price(frame: pd.DataFrame, price: np.ndarray | float | None = None) -> pd.DataFrame:
    """Stamp the decision context with its reference price.

    Must be called on the *observed* context row, before any candidate price is
    substituted - that is what makes ``p0`` the current price rather than
    whatever price is being simulated.
    """
    out = frame.copy()
    if price is None:
        if "effective_unit_price" not in out.columns:
            raise HybridModelError(
                "attach_reference_price needs either an explicit price or an "
                "'effective_unit_price' column on the context frame."
            )
        out[REFERENCE_PRICE_COLUMN] = out["effective_unit_price"].astype("float64")
    else:
        out[REFERENCE_PRICE_COLUMN] = np.asarray(price, dtype="float64")
    return out


class HybridPricingModel:
    """Wraps a fitted demand model with an explicit elasticity price response.

    Exposes the same contract as :class:`DemandModel`
    (``predict(frame) -> non-negative units``), so the simulator, optimizer,
    API and dashboard need no special case.
    """

    price_aware = True

    def __init__(
        self,
        base_model,
        *,
        method: str | PriceResponseMethod = PriceResponseMethod.SHRUNK,
        elasticity_table: ElasticityTable | None = None,
        cfg: Config | None = None,
    ) -> None:
        self.cfg = cfg or load_config()
        self.base_model = base_model
        self.method = PriceResponseMethod(str(method))
        self.elasticity_table = elasticity_table
        if self.method in (PriceResponseMethod.POOLED, PriceResponseMethod.SHRUNK) and elasticity_table is None:
            raise HybridModelError(
                f"Price-response method '{self.method}' requires an elasticity table. "
                "Run `python scripts/estimate_elasticity.py` first."
            )
        self.metadata = getattr(base_model, "metadata", None)

    # -- introspection -----------------------------------------------------
    @property
    def name(self) -> str:
        base = getattr(self.base_model, "name", "demand model")
        return f"{base} + {self.method.value} price response"

    def elasticity_for(self, frame: pd.DataFrame) -> np.ndarray:
        """The elasticity applied to each row."""
        if self.method is PriceResponseMethod.ML:
            return np.full(len(frame), np.nan)
        table = self.elasticity_table
        assert table is not None
        if self.method is PriceResponseMethod.POOLED:
            return np.full(len(frame), table.pooled)
        return table.epsilon_for(frame["upc"])

    def elasticity_source(self, frame: pd.DataFrame) -> np.ndarray:
        if self.method is PriceResponseMethod.ML:
            return np.array(["ml_native"] * len(frame))
        table = self.elasticity_table
        assert table is not None
        if self.method is PriceResponseMethod.POOLED:
            return np.array(["pooled"] * len(frame))
        return table.source_for(frame["upc"])

    # -- scoring -----------------------------------------------------------
    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        if self.method is PriceResponseMethod.ML:
            return np.asarray(self.base_model.predict(frame), dtype="float64")

        if REFERENCE_PRICE_COLUMN not in frame.columns:
            raise HybridModelError(
                "The hybrid price response needs a 'reference_price' column. Call "
                "pricing_engine.models.hybrid.attach_reference_price(context) on the "
                "decision context before simulating candidate prices."
            )

        p = frame["effective_unit_price"].to_numpy(dtype="float64")
        p0 = frame[REFERENCE_PRICE_COLUMN].to_numpy(dtype="float64")
        if np.any(~np.isfinite(p0)) or np.any(p0 <= 0):
            raise HybridModelError("reference_price must be positive and finite for every row")

        # Baseline demand is evaluated AT the reference price, so every
        # price-dependent feature is consistent with p0 and the price effect
        # comes only from the elasticity term.
        baseline_frame = recompute_price_features(frame, p0)
        q0 = np.asarray(self.base_model.predict(baseline_frame), dtype="float64")

        epsilon = self.elasticity_for(frame)
        with np.errstate(over="ignore", invalid="ignore"):
            ratio = np.power(p / p0, epsilon)
        units = np.clip(q0 * ratio, 0.0, None)
        cap = getattr(self.base_model, "prediction_cap_", None)
        return np.clip(units, 0.0, cap) if cap else units

    def describe(self) -> dict[str, Any]:
        table = self.elasticity_table
        return {
            "method": self.method.value,
            "base_model": getattr(self.base_model, "name", None),
            "base_model_version": getattr(self.metadata, "version", None),
            "pooled_elasticity": None if table is None else table.pooled,
            "elasticity_metadata": {} if table is None else dict(table.metadata),
        }


def load_pricing_model(
    base_model,
    *,
    cfg: Config | None = None,
    method: str | None = None,
) -> HybridPricingModel:
    """Build the configured pricing model around a fitted demand model."""
    cfg = cfg or load_config()
    method = method or str(cfg.get("pricing_response.method", "shrunk"))
    if PriceResponseMethod(method) is PriceResponseMethod.ML:
        return HybridPricingModel(base_model, method=method, cfg=cfg)

    path = Path(cfg.get("pricing_response.elasticity_table", "artifacts/models/elasticity_table.csv"))
    if not path.is_absolute():
        path = cfg.root / path
    try:
        table = ElasticityTable.load(path)
    except ElasticityError as exc:
        raise HybridModelError(str(exc)) from exc
    return HybridPricingModel(base_model, method=method, elasticity_table=table, cfg=cfg)
