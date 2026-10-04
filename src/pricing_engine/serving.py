"""Serving state shared by the API, the dashboard and the pricing copilot.

The model artifact and the recent feature slice are loaded **once** and reused
by every request. Reloading a model per request is the classic way to turn a
5 ms endpoint into a 2 s one.

The three pricing operations (predict, simulate, recommend) live here rather
than in the HTTP layer, so every consumer gets byte-identical answers from one
implementation: the FastAPI routes are thin adapters over these methods, and
the copilot's tools call them directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from pricing_engine.config import Config, load_config
from pricing_engine.features.build import recompute_price_features
from pricing_engine.models.demand_model import load_model
from pricing_engine.models.hybrid import (
    HybridPricingModel,
    attach_reference_price,
    load_pricing_model,
)
from pricing_engine.optimization.optimizer import Recommendation, optimize_price
from pricing_engine.simulation.counterfactual import (
    demand_curve_diagnostics,
    simulate_price_grid,
)

STAT_COLUMNS = ["n_obs", "n_distinct_prices", "price_cv", "price_min", "price_max"]

#: Number of most recent weeks kept in memory for serving.
SERVING_WEEKS = 8


class ContextNotFound(LookupError):
    """Raised when no decision context exists for the requested key."""


class ServingError(ValueError):
    """Raised when a request is well-formed but cannot be served (e.g. empty grid)."""


@dataclass
class AppState:
    model: HybridPricingModel
    contexts: pd.DataFrame
    stats: pd.DataFrame
    cfg: Config

    # -- provenance ------------------------------------------------------------
    @property
    def data_mode(self) -> str:
        return self.cfg.data_mode

    @property
    def data_label(self) -> str:
        return self.cfg.data_label

    @property
    def weeks(self) -> list[int]:
        return sorted(int(w) for w in self.contexts["week"].unique())

    @property
    def model_version(self) -> str | None:
        return getattr(self.model.metadata, "version", None)

    # -- lookup ------------------------------------------------------------------
    def context(self, upc: int, store: int, week: int | None = None) -> pd.DataFrame:
        sub = self.contexts[(self.contexts["upc"] == upc) & (self.contexts["store"] == store)]
        if sub.empty:
            source = "synthetic demo panel" if self.cfg.is_synthetic else "Dominick's Cereals panel"
            raise ContextNotFound(
                f"No decision context for UPC {upc} at store {store} in the served window "
                f"(weeks {self.weeks[0]}-{self.weeks[-1]}). The API serves the most recent "
                f"{SERVING_WEEKS} weeks of the {source}."
            )
        if week is not None:
            sub = sub[sub["week"] == week]
            if sub.empty:
                raise ContextNotFound(
                    f"No observation for UPC {upc}, store {store}, week {week}. "
                    f"Served weeks: {self.weeks}."
                )
        return sub.sort_values("week").tail(1)

    def series_stats(self, upc: int, store: int) -> dict:
        row = self.stats[(self.stats["upc"] == upc) & (self.stats["store"] == store)]
        return row.iloc[0][STAT_COLUMNS].to_dict() if len(row) else {}

    def products(self) -> pd.DataFrame:
        """One row per served UPC x store: description and latest served week."""
        cols = ["upc", "store", "week"] + (["descrip"] if "descrip" in self.contexts else [])
        latest = self.contexts[cols].sort_values("week").drop_duplicates(["upc", "store"], keep="last")
        return latest.sort_values(["upc", "store"]).reset_index(drop=True)

    # -- operations ----------------------------------------------------------------
    def predict_demand(
        self, upc: int, store: int, week: int | None = None, price: float | None = None
    ) -> dict[str, Any]:
        row = self.context(upc, store, week)
        observed_price = float(row["effective_unit_price"].iloc[0])
        used = float(price) if price is not None else observed_price
        # Reference price = the observed price of the context, stamped before the
        # candidate price is substituted (the elasticity response needs p0).
        frame = recompute_price_features(attach_reference_price(row), np.array([used]))
        return {
            "upc": upc,
            "store": store,
            "week": int(row["week"].iloc[0]),
            "week_start_date": str(row["week_start_date"].iloc[0])[:10],
            "product_description": str(row["descrip"].iloc[0]) if "descrip" in row else None,
            "price_used": used,
            "observed_price": observed_price,
            "predicted_units": float(self.model.predict(frame)[0]),
            "model_version": self.model_version,
        }

    def decision_cost(self, row: pd.DataFrame) -> float | None:
        raw = float(row["decision_time_unit_cost"].iloc[0])
        return raw if np.isfinite(raw) and raw > 0 else None

    def simulate_prices(
        self,
        upc: int,
        store: int,
        min_price: float,
        max_price: float,
        step: float = 0.05,
        week: int | None = None,
        unit_cost: float | None = None,
    ) -> dict[str, Any]:
        row = self.context(upc, store, week)
        cost = unit_cost if unit_cost is not None else self.decision_cost(row)
        grid = np.round(np.arange(min_price, max_price + step / 2, step), 4)
        grid = grid[grid > 0]
        if grid.size == 0:
            raise ServingError("The requested price grid is empty.")

        sim = simulate_price_grid(self.model, row, grid, unit_cost=cost)
        curve = [
            {
                "candidate_price": float(r.candidate_price),
                "predicted_units": float(r.predicted_units),
                "expected_revenue": float(r.expected_revenue),
                "expected_gross_profit": None
                if np.isnan(r.expected_gross_profit)
                else float(r.expected_gross_profit),
                "expected_margin_rate": None
                if np.isnan(r.expected_margin_rate)
                else float(r.expected_margin_rate),
            }
            for r in sim.itertuples()
        ]
        eps = self.model.elasticity_for(row) if hasattr(self.model, "elasticity_for") else None
        src = self.model.elasticity_source(row) if hasattr(self.model, "elasticity_source") else None
        return {
            "upc": upc,
            "store": store,
            "week": int(row["week"].iloc[0]),
            "unit_cost_used": cost,
            "model_version": self.model_version,
            "price_response_method": getattr(getattr(self.model, "method", None), "value", None),
            "elasticity_used": None if eps is None or not np.isfinite(eps[0]) else float(eps[0]),
            "elasticity_source": None if src is None else str(src[0]),
            "n_candidates": len(curve),
            "curve": curve,
            "diagnostics": demand_curve_diagnostics(sim),
        }

    def recommend(
        self,
        upc: int,
        store: int,
        week: int | None = None,
        *,
        policy_profile: str | None = None,
        objective: str | None = None,
        unit_cost: float | None = None,
    ) -> Recommendation:
        row = self.context(upc, store, week)
        return optimize_price(
            self.model,
            row,
            cfg=self.cfg,
            policy_profile=policy_profile,
            objective=objective,
            series_stats=self.series_stats(upc, store),
            unit_cost=unit_cost,
        )


def build_state(cfg: Config | None = None) -> AppState:
    """Load the model artifact and the serving context slice."""
    cfg = cfg or load_config()
    model_path = cfg.path("models_dir") / "demand_model.joblib"
    if not model_path.exists():
        raise FileNotFoundError(
            f"Demand model artifact not found at {model_path}. Run `python scripts/train.py` first."
        )
    base_model = load_model(model_path, cfg=cfg)
    # The service serves the configured PRICING model (the elasticity hybrid
    # by default), not the raw forecaster.
    model = load_pricing_model(base_model, cfg=cfg)

    features_path = cfg.path("features_table")
    if not features_path.exists():
        raise FileNotFoundError(
            f"Feature table not found at {features_path}. Run `python scripts/build_features.py` first."
        )
    feats = pd.read_parquet(features_path)
    max_week = int(feats["week"].max())
    contexts = feats[feats["week"] > max_week - SERVING_WEEKS].copy()
    contexts = contexts[contexts["lag_price_1"].notna()]

    stats = pd.read_csv(cfg.path("metrics_dir") / "price_variation_upc_store.csv")
    return AppState(model=model, contexts=contexts, stats=stats, cfg=cfg)
