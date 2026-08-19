"""End-to-end demo on the real Dominick's data and the real trained artifacts.

    python scripts/run_demo.py [--upc ... --store ...]

Walks through the whole engine for one genuine UPC x store context:
history -> demand prediction -> counterfactual price sweep -> constrained
optimization -> auditable recommendation. Nothing is hardcoded; if an artifact
is missing the script says which command to run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.audit import RecommendationLog  # noqa: E402
from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.features.build import training_frame  # noqa: E402
from pricing_engine.models.demand_model import load_model  # noqa: E402
from pricing_engine.models.hybrid import attach_reference_price, load_pricing_model  # noqa: E402
from pricing_engine.optimization.optimizer import optimize_price  # noqa: E402
from pricing_engine.simulation.counterfactual import (  # noqa: E402
    demand_curve_diagnostics,
    simulate_price_grid,
)

STAT_COLUMNS = ["n_obs", "n_distinct_prices", "price_cv", "price_min", "price_max"]


def rule(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upc", type=int, default=None)
    parser.add_argument("--store", type=int, default=None)
    parser.add_argument("--profile", default="standard")
    parser.add_argument("--method", default=None, choices=["ml", "pooled", "shrunk"])
    args = parser.parse_args()

    cfg = load_config()
    model_path = cfg.path("models_dir") / "demand_model.joblib"
    if not model_path.exists():
        print(f"Model artifact missing: {model_path}\nRun: make data && make train")
        return 1
    base = load_model(model_path, cfg=cfg)
    model = load_pricing_model(base, cfg=cfg, method=args.method)
    feats = pd.read_parquet(cfg.path("features_table"))
    usable = training_frame(feats)
    stats = pd.read_csv(cfg.path("metrics_dir") / "price_variation_upc_store.csv")

    latest_week = int(usable["week"].max())
    pool = usable[usable["week"] == latest_week].merge(stats, on=["upc", "store"], how="left")
    pool = pool[pool["eligible"].fillna(False) & pool["decision_time_unit_cost"].notna()]

    if args.upc is not None and args.store is not None:
        row = pool[(pool["upc"] == args.upc) & (pool["store"] == args.store)].head(1)
        if row.empty:
            print(f"UPC {args.upc} at store {args.store} is not an eligible context in week {latest_week}.")
            return 1
    else:
        # Pick a high-volume, eligible series so the demo is representative.
        row = pool.sort_values("total_units", ascending=False).head(1) if "total_units" in pool.columns else pool.head(1)

    # Stamp the reference price once: every later prediction, simulation and
    # optimisation for this context measures its price response relative to p0.
    row = attach_reference_price(row)
    ctx = row.iloc[0]
    rule("1. DECISION CONTEXT (real Dominick's data)")
    print(f"product      : {ctx['descrip']} ({int(ctx['upc'])})")
    print(f"store        : {int(ctx['store'])}")
    print(f"decision week: {int(ctx['week'])} ({pd.Timestamp(ctx['week_start_date']).date()})")
    print(f"current price: ${float(ctx['effective_unit_price']):.2f}")
    print(f"decision-time unit cost (lagged AAC): ${float(ctx['decision_time_unit_cost']):.2f}")
    print(f"observed price support: ${float(ctx['price_min']):.2f} .. ${float(ctx['price_max']):.2f} "
          f"over {int(ctx['n_obs'])} weeks, {int(ctx['n_distinct_prices'])} distinct prices")

    history = usable[(usable["upc"] == ctx["upc"]) & (usable["store"] == ctx["store"])].tail(8)
    rule("2. RECENT HISTORY (observed)")
    print(history[["week", "effective_unit_price", "move", "revenue", "gross_profit",
                   "recorded_promotion_type"]].to_string(index=False))

    rule("3. DEMAND PREDICTION AT THE CURRENT PRICE")
    units = float(model.predict(row)[0])
    print(f"model: {base.metadata.name} ({base.metadata.version})")
    print(f"price response: {model.method.value}", end="")
    if model.method.value != "ml":
        eps = float(model.elasticity_for(row)[0])
        print(f" | elasticity {eps:.3f} ({model.elasticity_source(row)[0]})")
    else:
        print()
    print(f"predicted units next week: {units:,.2f}  (actual observed that week: {float(ctx['move']):,.0f})")

    rule("4. COUNTERFACTUAL PRICE SWEEP (model-internal estimate, cost held fixed)")
    p0 = float(ctx["effective_unit_price"])
    cost = float(ctx["decision_time_unit_cost"])
    grid = np.round(np.arange(p0 * 0.75, p0 * 1.25 + 0.01, 0.10), 2)
    sim = simulate_price_grid(model, row, grid, unit_cost=cost)
    show = sim.assign(
        candidate_price=lambda d: d["candidate_price"].map(lambda v: f"${v:.2f}"),
        predicted_units=lambda d: d["predicted_units"].round(2),
        expected_revenue=lambda d: d["expected_revenue"].round(2),
        expected_gross_profit=lambda d: d["expected_gross_profit"].round(2),
        expected_margin_rate=lambda d: (100 * d["expected_margin_rate"]).round(1),
    )
    print(show[["candidate_price", "predicted_units", "expected_revenue",
                "expected_gross_profit", "expected_margin_rate"]].to_string(index=False))
    diag = demand_curve_diagnostics(sim)
    print(f"\ncurve diagnostics: monotone decreasing={diag['monotone_decreasing']}, "
          f"median local elasticity={diag['median_local_elasticity']:.2f}")

    rule("5. CONSTRAINED OPTIMIZATION")
    rec = optimize_price(
        model, row, cfg=cfg, policy_profile=args.profile,
        series_stats=ctx[STAT_COLUMNS].to_dict(),
    )
    d = rec.as_dict()
    print(f"objective        : {d['objective']}   policy profile: {d['policy_profile']} (DEMO setting)")
    print(f"feasible range   : ${d['constraints']['bounds'][0]:.2f} .. ${d['constraints']['bounds'][1]:.2f}")
    print(f"current price    : ${d['current_price']:.2f}")
    print(f"proposed candidate price : ${d['proposed_candidate_price']:.2f} ({100*d['proposed_price_change_pct']:+.2f}%)")
    print(f"FINAL recommended price  : ${d['final_recommended_price']:.2f} ({100*d['price_change_pct']:+.2f}%)")
    print(f"decision state   : {d['decision']}  (actionable: {d['actionable']})")
    print(f"predicted units  : {d['predicted_units_current']:.2f} -> {d['predicted_units_recommended']:.2f}")
    print(f"expected revenue : ${d['expected_revenue_current']:.2f} -> ${d['expected_revenue_recommended']:.2f}")
    if d["expected_gross_profit_current"] is not None:
        print(f"expected gross profit: ${d['expected_gross_profit_current']:.2f} -> "
              f"${d['expected_gross_profit_recommended']:.2f}")
    if d["model_internal_estimated_profit_uplift_pct"] is not None:
        print(
            "MODEL-INTERNAL estimated profit uplift: "
            f"{100*d['model_internal_estimated_profit_uplift_pct']:+.2f}% "
            "(internal simulation; not realised, not causal)"
        )
    print(f"risk level       : {d['risk_level']} (HIGH = risky; HIGH is gated by default)")
    print(f"reason codes     : {', '.join(d['reason_codes'])}")

    rule("6. AUDIT")
    log = RecommendationLog(cfg.path("recommendation_log"))
    log.append([rec])
    print(f"recommendation appended to {cfg.path('recommendation_log')}")
    print("lifecycle: GENERATED -> REVIEWED -> APPROVED / REJECTED -> PUBLISHED (human in the loop)")
    print("\nReminder: every counterfactual number above is a model-INTERNAL estimate. Demand at prices")
    print("that were never charged was never observed in this dataset.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
