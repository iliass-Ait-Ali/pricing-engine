"""Application state: the model and the decision-context store.

The model artifact and the recent feature slice are loaded **once** at startup
and reused by every request. Reloading a model per request is the classic way
to turn a 5 ms endpoint into a 2 s one.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.models.demand_model import load_model  # noqa: E402
from pricing_engine.models.hybrid import (  # noqa: E402
    HybridPricingModel,
    load_pricing_model,
)

STAT_COLUMNS = ["n_obs", "n_distinct_prices", "price_cv", "price_min", "price_max"]

#: Number of most recent weeks kept in memory for serving.
SERVING_WEEKS = 8


class ContextNotFound(LookupError):
    """Raised when no decision context exists for the requested key."""


@dataclass
class AppState:
    model: HybridPricingModel
    contexts: pd.DataFrame
    stats: pd.DataFrame
    cfg: object

    @property
    def weeks(self) -> list[int]:
        return sorted(int(w) for w in self.contexts["week"].unique())

    def context(self, upc: int, store: int, week: int | None = None) -> pd.DataFrame:
        sub = self.contexts[(self.contexts["upc"] == upc) & (self.contexts["store"] == store)]
        if sub.empty:
            raise ContextNotFound(
                f"No decision context for UPC {upc} at store {store} in the served window "
                f"(weeks {self.weeks[0]}-{self.weeks[-1]}). The API serves the most recent "
                f"{SERVING_WEEKS} weeks of the Dominick's Cereals panel."
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


def build_state() -> AppState:
    """Load the model artifact and the serving context slice."""
    cfg = load_config()
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
