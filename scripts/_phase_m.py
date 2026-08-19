"""Shared plumbing for the Phase M scientific audit scripts.

Every Phase M report must talk about the *same* decision contexts, otherwise
the constraint attribution, the rule-only ablation and the guardrail ablation
cannot be compared with each other. This module is the single definition of
that context pool.

Pool definition
---------------
All ``UPC x store`` rows of the **final decision week** of the test window
(week 399), with their per-series price statistics attached. That is the same
pool ``scripts/optimize.py --batch`` samples from, except the audit uses it in
full: no sampling, therefore no sampling bias to argue about.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.features.build import training_frame  # noqa: E402
from pricing_engine.models.demand_model import load_model  # noqa: E402
from pricing_engine.models.hybrid import load_pricing_model  # noqa: E402

STAT_COLUMNS = ["n_obs", "n_distinct_prices", "price_cv", "price_min", "price_max"]


def load_decision_pool(cfg=None, *, week: int | None = None, limit: int | None = None):
    """Return ``(pool, week)`` - every decision context of one week."""
    cfg = cfg or load_config()
    feats = pd.read_parquet(cfg.path("features_table"))
    usable = training_frame(feats)
    var = pd.read_csv(cfg.path("metrics_dir") / "price_variation_upc_store.csv")
    stats = var[["upc", "store", *STAT_COLUMNS, "eligible"]]
    week = int(week or usable["week"].max())
    pool = (
        usable[usable["week"] == week]
        .merge(stats, on=["upc", "store"], how="left")
        .reset_index(drop=True)
    )
    if limit is not None and len(pool) > limit:
        pool = pool.sample(limit, random_state=cfg.seed).reset_index(drop=True)
    return pool, week


def load_models(cfg=None, *, method: str | None = None):
    """Return ``(base_model, pricing_model)`` for the configured price response."""
    cfg = cfg or load_config()
    base = load_model(cfg.path("models_dir") / "demand_model.joblib", cfg=cfg)
    return base, load_pricing_model(base, cfg=cfg, method=method)


def promotion_share(cfg, pool: pd.DataFrame) -> pd.Series:
    """Share of weeks each UPC x store series carries a recorded promotion."""
    feats = pd.read_parquet(
        cfg.path("features_table"), columns=["upc", "store", "recorded_promotion_flag"]
    )
    share = feats.groupby(["upc", "store"], observed=True)["recorded_promotion_flag"].mean()
    return pool.set_index(["upc", "store"]).index.map(share)


def fmt_pct(x: float, digits: int = 1) -> str:
    return "n/a" if x != x else f"{100.0 * x:.{digits}f}%"


def md_table(rows: list[list[str]], header: list[str], align: list[str] | None = None) -> str:
    align = align or ["---"] * len(header)
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join(align) + " |"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)
