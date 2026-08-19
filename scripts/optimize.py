"""Phase G/L CLI - produce price recommendations from the trained model.

    # one real UPC x store context
    python scripts/optimize.py --upc 3000006560 --store 86

    # batch over the latest decision week
    python scripts/optimize.py --batch 3000 --profile standard

    # benchmark the native ML price response instead of the elasticity hybrid
    python scripts/optimize.py --batch 3000 --method ml

Outputs (batch mode)
--------------------
    artifacts/recommendation_log.csv        (append-only audit log)
    artifacts/metrics/recommendations.json
    reports/07_RECOMMENDATION_SUMMARY.md
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.audit import RecommendationLog  # noqa: E402
from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.features.build import training_frame  # noqa: E402
from pricing_engine.models.demand_model import load_model  # noqa: E402
from pricing_engine.models.hybrid import load_pricing_model  # noqa: E402
from pricing_engine.optimization.optimizer import (  # noqa: E402
    optimize_price,
    optimize_price_batch,
)
from pricing_engine.utils.io import write_json  # noqa: E402

STAT_COLUMNS = ["n_obs", "n_distinct_prices", "price_cv", "price_min", "price_max"]


def load_context_pool(cfg):
    feats = pd.read_parquet(cfg.path("features_table"))
    usable = training_frame(feats)
    var = pd.read_csv(cfg.path("metrics_dir") / "price_variation_upc_store.csv")
    stats = var[["upc", "store", *STAT_COLUMNS, "eligible"]]
    return usable, stats


def print_recommendation(rec) -> None:
    d = rec.as_dict()
    print("\n" + "=" * 78)
    print(f"UPC {d['upc']} | store {d['store']} | week {d['decision_week']} ({d['decision_week_start_date']})")
    if d.get("product_description"):
        print(f"product: {d['product_description']}")
    print(f"objective: {d['objective']} | policy: {d['policy_profile']} | model: {d['model_version']}")
    print(f"price response: {d['price_response_method']}"
          + (f" | elasticity {d['elasticity_used']:.3f} ({d['elasticity_source']})"
             if d["elasticity_used"] is not None else ""))
    print("-" * 78)
    print(f"current price          : ${d['current_price']:.2f}")
    print(f"proposed candidate     : ${d['proposed_candidate_price']:.2f}  ({100*d['proposed_price_change_pct']:+.2f}%)")
    print(f"FINAL recommended price: ${d['final_recommended_price']:.2f}  ({100*d['price_change_pct']:+.2f}%)")
    print(f"decision state         : {d['decision']}  (actionable: {d['actionable']})")
    print(f"predicted units        : {d['predicted_units_current']:.2f} -> {d['predicted_units_recommended']:.2f}")
    print(f"expected revenue       : ${d['expected_revenue_current']:.2f} -> ${d['expected_revenue_recommended']:.2f}")
    if d["expected_gross_profit_current"] is not None:
        print(
            f"expected gross profit  : ${d['expected_gross_profit_current']:.2f} -> "
            f"${d['expected_gross_profit_recommended']:.2f}"
        )
    if d["model_internal_estimated_profit_uplift_pct"] is not None:
        print(
            "model-INTERNAL estimated profit uplift: "
            f"{100*d['model_internal_estimated_profit_uplift_pct']:+.2f}% "
            "(internal simulation; not realised, not causal)"
        )
    print(f"unit cost used (lagged AAC): {d['unit_cost_used']}")
    print(f"risk level             : {d['risk_level']} (HIGH = risky)")
    for note in d["risk_notes"]:
        print(f"  - {note}")
    print(f"reason codes           : {', '.join(d['reason_codes'])}")
    bounds = d["constraints"].get("bounds")
    if bounds:
        print(f"feasible price range   : ${bounds[0]:.2f} .. ${bounds[1]:.2f}")
    print("=" * 78)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upc", type=int, default=None)
    parser.add_argument("--store", type=int, default=None)
    parser.add_argument("--week", type=int, default=None, help="Decision week (default: latest).")
    parser.add_argument("--batch", type=int, default=None, help="Recommend for N contexts.")
    parser.add_argument("--profile", default="standard",
                        choices=["conservative", "standard", "aggressive"])
    parser.add_argument("--objective", default=None, choices=["gross_profit", "revenue"])
    parser.add_argument("--method", default=None, choices=["ml", "pooled", "shrunk"],
                        help="Price-response method (default: configs/config.yaml).")
    args = parser.parse_args()

    cfg = load_config()
    base_model = load_model(cfg.path("models_dir") / "demand_model.joblib", cfg=cfg)
    model = load_pricing_model(base_model, cfg=cfg, method=args.method)
    print(f"price-response method: {model.method.value}")
    usable, stats = load_context_pool(cfg)
    log = RecommendationLog(cfg.path("recommendation_log"))

    if args.upc is not None and args.store is not None:
        sub = usable[(usable["upc"] == args.upc) & (usable["store"] == args.store)]
        if sub.empty:
            print(f"No rows for UPC {args.upc} at store {args.store}.")
            return 1
        week = args.week or int(sub["week"].max())
        row = sub[sub["week"] == week]
        if row.empty:
            print(f"No observation for week {week}; latest is {int(sub['week'].max())}.")
            return 1
        st = stats[(stats["upc"] == args.upc) & (stats["store"] == args.store)]
        series_stats = st.iloc[0].to_dict() if len(st) else {}
        rec = optimize_price(
            model, row.head(1), cfg=cfg, policy_profile=args.profile,
            objective=args.objective, series_stats=series_stats,
        )
        print_recommendation(rec)
        log.append([rec])
        print(f"logged to {cfg.path('recommendation_log')}")
        return 0

    if not args.batch:
        parser.error("supply either --upc and --store, or --batch N")

    t0 = time.time()
    week = args.week or int(usable["week"].max())
    pool = usable[usable["week"] == week].merge(stats, on=["upc", "store"], how="left")
    eligible_pool = pool[pool["eligible"].fillna(False)]
    print(f"decision week {week}: {len(pool):,} contexts, {len(eligible_pool):,} eligible")

    take = pool.sample(min(args.batch, len(pool)), random_state=cfg.seed).reset_index(drop=True)
    # Batched scoring: identical decisions to one optimize_price() call per row
    # (pinned by tests/test_batch_equivalence.py), with the demand model called
    # once per group of contexts instead of once per context.
    series_stats = take[STAT_COLUMNS].to_dict(orient="records")
    recs = optimize_price_batch(
        model, take, cfg=cfg, policy_profile=args.profile,
        objective=args.objective, series_stats=series_stats,
    )
    print(f"  scored {len(recs):,} contexts")

    frame = log.append(recs)
    df = pd.DataFrame([r.as_dict() for r in recs])
    actionable = df[df["actionable"]]

    all_codes = [c for codes in df["reason_codes"] for c in codes]
    code_counts = pd.Series(all_codes).value_counts()

    # Portfolio economics count ONLY actionable recommendations: a
    # REVIEW_REQUIRED proposal does not move a price.
    gp_current = float(df["expected_gross_profit_current"].sum(skipna=True))
    gp_applied = float(
        np.nansum(
            np.where(
                df["actionable"].to_numpy(),
                df["expected_gross_profit_recommended"].to_numpy(dtype="float64"),
                df["expected_gross_profit_current"].to_numpy(dtype="float64"),
            )
        )
    )

    summary = {
        "decision_week": week,
        "policy_profile": args.profile,
        "objective": args.objective or cfg.get("optimization.objective"),
        "price_response_method": model.method.value,
        "model_version": recs[0].model_version if recs else None,
        "n_contexts": int(len(df)),
        "decision_counts": df["decision"].value_counts().to_dict(),
        "n_actionable": int(len(actionable)),
        "share_actionable": float(len(actionable) / max(len(df), 1)),
        "share_review_required": float((df["decision"] == "REVIEW_REQUIRED").mean()),
        "share_keep_current": float((df["decision"] == "KEEP_CURRENT").mean()),
        "median_abs_price_change_pct": float(actionable["price_change_pct"].abs().median())
        if len(actionable) else 0.0,
        "share_price_increases": float((actionable["price_change_pct"] > 0).mean())
        if len(actionable) else 0.0,
        "risk_distribution": df["risk_level"].value_counts().to_dict(),
        "high_risk_contexts": int((df["risk_level"] == "HIGH").sum()),
        "high_risk_actionable": int(((df["risk_level"] == "HIGH") & df["actionable"]).sum()),
        "elasticity_source_counts": df["elasticity_source"].value_counts(dropna=False).to_dict(),
        "median_elasticity_used": float(df["elasticity_used"].median())
        if df["elasticity_used"].notna().any() else None,
        "reason_code_counts": code_counts.to_dict(),
        "portfolio_expected_gross_profit_current": gp_current,
        "portfolio_expected_gross_profit_if_applied": gp_applied,
        "portfolio_model_internal_estimated_profit_uplift_pct": float(gp_applied / gp_current - 1.0),
        "median_internal_uplift_pct_among_actionable": float(
            actionable["model_internal_estimated_profit_uplift_pct"].median()
        ) if len(actionable) else 0.0,
        "runtime_seconds": round(time.time() - t0, 1),
    }
    write_json(cfg.path("metrics_dir") / "recommendations.json", summary)

    top = actionable.nlargest(15, "expected_gross_profit_recommended")[
        [
            "upc", "store", "product_description", "current_price",
            "proposed_candidate_price", "final_recommended_price",
            "price_change_pct", "elasticity_used", "predicted_units_current",
            "predicted_units_recommended", "expected_gross_profit_current",
            "expected_gross_profit_recommended", "model_internal_estimated_profit_uplift_pct",
            "risk_level", "decision",
        ]
    ]

    def table(df_: pd.DataFrame) -> str:
        cols = list(df_.columns)
        head = "| " + " | ".join(cols) + " |"
        sep = "| " + " | ".join("---" for _ in cols) + " |"
        rows = []
        for _, r in df_.iterrows():
            vals = []
            for c in cols:
                v = r[c]
                if isinstance(v, list):
                    vals.append(", ".join(v))
                elif isinstance(v, float):
                    vals.append(f"{v:,.4f}" if abs(v) < 10 else f"{v:,.2f}")
                else:
                    vals.append(str(v)[:26])
            rows.append("| " + " | ".join(vals) + " |")
        return "\n".join([head, sep, *rows])

    report = f"""# 07 - Recommendation summary

Generated by
`python scripts/optimize.py --batch {args.batch} --profile {args.profile} --method {model.method.value}`.

Model: `{summary['model_version']}` | price response: **{model.method.value}** |
objective: **{summary['objective']}** | policy profile: **{args.profile}**
(a DEMO policy setting, not a universal best practice) | decision week: **{week}**.

> **These are model-internal estimated counterfactuals.** The same fitted
> price-response model both proposes the price and scores it, so the figures
> below are an internal simulation, not an unbiased estimate of what the policy
> would earn. Demand at prices that were never charged was never observed.
> Nothing here is realised or causal uplift.

## Decision states

| state | contexts | share |
| --- | --- | --- |
{chr(10).join(f"| `{k}` | {v:,} | {100*v/len(df):.1f}% |" for k, v in summary['decision_counts'].items())}

Only `RECOMMEND_CHANGE` is actionable. `REVIEW_REQUIRED` means a proposal
exists but a human must approve it - this is what HIGH-risk contexts now
receive under the default policy.

## Portfolio view (actionable recommendations only)

| metric | value |
| --- | --- |
| contexts scored | {summary['n_contexts']:,} |
| actionable price changes | {summary['n_actionable']:,} ({100*summary['share_actionable']:.1f}%) |
| escalated for review | {100*summary['share_review_required']:.1f}% |
| keep current price | {100*summary['share_keep_current']:.1f}% |
| median absolute change (actionable) | {100*summary['median_abs_price_change_pct']:.2f}% |
| share of changes that are increases | {100*summary['share_price_increases']:.1f}% |
| expected gross profit at current prices | ${summary['portfolio_expected_gross_profit_current']:,.2f} |
| expected gross profit if actionable changes applied | ${summary['portfolio_expected_gross_profit_if_applied']:,.2f} |
| **model-internal estimated portfolio profit uplift** | **{100*summary['portfolio_model_internal_estimated_profit_uplift_pct']:+.2f}%** |
| median internal uplift among actionable | {100*summary['median_internal_uplift_pct_among_actionable']:+.2f}% |
| runtime | {summary['runtime_seconds']}s |

## Risk gating

| risk level | contexts | actionable |
| --- | --- | --- |
{chr(10).join(f"| {k} | {v:,} | {int(((df['risk_level'] == k) & df['actionable']).sum()):,} |" for k, v in summary['risk_distribution'].items())}

HIGH-risk contexts that still produced an automatic price change:
**{summary['high_risk_actionable']}** of {summary['high_risk_contexts']:,}.
Under the default profiles this must be zero; only the DEMO `aggressive`
profile is allowed to override the gate.

## Elasticity provenance

| source | contexts |
| --- | --- |
{chr(10).join(f"| {k} | {v:,} |" for k, v in summary['elasticity_source_counts'].items())}

Median elasticity applied: **{summary['median_elasticity_used'] if summary['median_elasticity_used'] is None else round(summary['median_elasticity_used'], 3)}**.

## Reason codes

{chr(10).join(f"* `{k}`: {v:,}" for k, v in summary['reason_code_counts'].items())}

## Top 15 actionable recommendations by expected gross profit

{table(top) if len(top) else "_No actionable recommendations in this run._"}

## How to read this

* `KEEP_CURRENT` is a real, frequent output: series failing the eligibility
  screen, sitting at their optimum, or whose internal estimated gain is below
  the materiality threshold are deliberately left alone.
* `REVIEW_REQUIRED` is the Phase L gate: a HIGH-risk context can propose a
  price, but it is never applied automatically.
* `PRICE_CHANGE_LIMIT` / `OUTSIDE_EXTRAPOLATION_RANGE` in the reason codes mean
  a guardrail bound the answer - not that the model wanted that exact price.
* `risk_level` is a transparent heuristic (HIGH = risky), **not** calibrated
  confidence.
* Every row is appended to `artifacts/recommendation_log.csv` with inputs,
  constraints, elasticity provenance and model version for audit.
"""
    out = cfg.path("reports_dir") / "07_RECOMMENDATION_SUMMARY.md"
    out.write_text(report, encoding="utf-8")
    print(f"\nwrote {out}")
    print(f"appended {len(frame):,} rows to {cfg.path('recommendation_log')}")
    print(
        f"actionable: {summary['n_actionable']:,}/{summary['n_contexts']:,} "
        f"({100*summary['share_actionable']:.1f}%) | review required "
        f"{100*summary['share_review_required']:.1f}% | keep {100*summary['share_keep_current']:.1f}% | "
        f"model-internal portfolio uplift {100*summary['portfolio_model_internal_estimated_profit_uplift_pct']:+.2f}%"
    )
    print(f"HIGH-risk actionable: {summary['high_risk_actionable']} (must be 0 under default profiles)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
