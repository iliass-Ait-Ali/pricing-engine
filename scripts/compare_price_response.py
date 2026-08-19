"""Phase L - compare price-response methods and probe elasticity sensitivity.

    python scripts/compare_price_response.py [--n-contexts 400]

Three ways of answering "what happens to demand if we change this price?" are
scored on the SAME contexts, with the same baseline demand model, the same
cost and the same constraints:

    1. ml      - the fitted demand model answers price counterfactuals directly
                 (kept as the benchmark)
    2. pooled  - Q(p) = Q_hat(p0) * (p/p0) ** epsilon_pooled
    3. shrunk  - same, with an empirical-Bayes shrunk per-UPC elasticity

Then the candidate profit-maximising price is recomputed under fixed elasticity scenarios
(-1.5, -1.9, -2.4, -3.1) to show how sensitive the recommendation is to a
parameter nobody can measure without an experiment.

Outputs
-------
    reports/08_PRICE_RESPONSE_COMPARISON.md
    artifacts/metrics/price_response_comparison.json
    artifacts/figures/compare_*.png
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.economics.elasticity_store import fixed_elasticity_table  # noqa: E402
from pricing_engine.features.build import training_frame  # noqa: E402
from pricing_engine.models.demand_model import load_model  # noqa: E402
from pricing_engine.models.hybrid import HybridPricingModel, load_pricing_model  # noqa: E402
from pricing_engine.optimization.optimizer import optimize_price  # noqa: E402
from pricing_engine.simulation.counterfactual import simulate_price_grid  # noqa: E402
from pricing_engine.utils.io import ensure_dir, write_json  # noqa: E402

plt.rcParams.update({"figure.dpi": 120, "figure.autolayout": True, "font.size": 9})

STAT_COLUMNS = ["n_obs", "n_distinct_prices", "price_cv", "price_min", "price_max"]
METHODS = ["ml", "pooled", "shrunk"]


def recommend_all(models: dict, row: pd.DataFrame, stats: dict, cfg, profile: str) -> dict:
    out = {}
    for name, model in models.items():
        rec = optimize_price(
            model, row, cfg=cfg, policy_profile=profile,
            objective="gross_profit", series_stats=stats,
        )
        out[name] = rec
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-contexts", type=int, default=400)
    parser.add_argument("--profile", default="standard")
    parser.add_argument("--sweep-pct", type=float, default=0.25)
    args = parser.parse_args()

    cfg = load_config()
    t0 = time.time()
    fig_dir = ensure_dir(cfg.path("figures_dir"))
    base = load_model(cfg.path("models_dir") / "demand_model.joblib", cfg=cfg)
    models = {m: load_pricing_model(base, cfg=cfg, method=m) for m in METHODS}
    shrunk_table = models["shrunk"].elasticity_table

    feats = pd.read_parquet(cfg.path("features_table"))
    usable = training_frame(feats)
    stats_table = pd.read_csv(cfg.path("metrics_dir") / "price_variation_upc_store.csv")

    week = int(usable["week"].max())
    pool = usable[usable["week"] == week].merge(stats_table, on=["upc", "store"], how="left")
    pool = pool[pool["eligible"].fillna(False) & pool["decision_time_unit_cost"].gt(0)]
    sample = pool.sample(min(args.n_contexts, len(pool)), random_state=cfg.seed).reset_index(drop=True)
    print(f"comparing price-response methods on {len(sample):,} eligible contexts (week {week})")

    rows: list[dict] = []
    curves: list[tuple[pd.DataFrame, dict]] = []
    for i in range(len(sample)):
        row = sample.iloc[[i]]
        series_stats = row.iloc[0][STAT_COLUMNS].to_dict()
        recs = recommend_all(models, row, series_stats, cfg, args.profile)
        record = {
            "upc": int(row["upc"].iloc[0]),
            "store": int(row["store"].iloc[0]),
            "descrip": str(row["descrip"].iloc[0]),
            "current_price": float(row["effective_unit_price"].iloc[0]),
            "unit_cost": float(row["decision_time_unit_cost"].iloc[0]),
            "elasticity_shrunk": float(shrunk_table.epsilon_for(row["upc"])[0]),
            "elasticity_source": str(shrunk_table.source_for(row["upc"])[0]),
        }
        for name, rec in recs.items():
            record[f"{name}_price"] = rec.proposed_candidate_price
            record[f"{name}_change_pct"] = rec.price_change_pct
            record[f"{name}_decision"] = rec.decision
            record[f"{name}_actionable"] = rec.actionable
            record[f"{name}_uplift"] = rec.model_internal_estimated_profit_uplift_pct
            record[f"{name}_risk"] = rec.risk_level
        rows.append(record)

        if i < 4:
            p0 = record["current_price"]
            grid = np.round(np.linspace(p0 * (1 - args.sweep_pct), p0 * (1 + args.sweep_pct), 21), 2)
            grid = np.unique(grid[grid > 0])
            per_method = {
                name: simulate_price_grid(model, row, grid, unit_cost=record["unit_cost"])
                for name, model in models.items()
            }
            curves.append((row, {"grid": grid, "sims": per_method, "record": record}))

    comp = pd.DataFrame(rows)

    # -- disagreement --------------------------------------------------------
    def pair_stats(a: str, b: str) -> dict:
        diff = comp[f"{a}_price"] - comp[f"{b}_price"]
        rel = diff / comp["current_price"]
        same_decision = (comp[f"{a}_decision"] == comp[f"{b}_decision"]).mean()
        both_change = comp[f"{a}_actionable"] & comp[f"{b}_actionable"]
        opposite = (
            (np.sign(comp[f"{a}_change_pct"]) * np.sign(comp[f"{b}_change_pct"]) < 0) & both_change
        ).mean()
        return {
            "pair": f"{a} vs {b}",
            "mean_abs_price_gap": float(diff.abs().mean()),
            "median_abs_price_gap": float(diff.abs().median()),
            "p90_abs_price_gap": float(diff.abs().quantile(0.9)),
            "mean_abs_gap_pct_of_price": float(rel.abs().mean()),
            "share_identical_price": float((diff.abs() < 1e-9).mean()),
            "share_same_decision_state": float(same_decision),
            "share_opposite_direction": float(opposite),
            "corr_recommended_price": float(comp[f"{a}_price"].corr(comp[f"{b}_price"])),
        }

    pairs = [pair_stats("ml", "pooled"), pair_stats("ml", "shrunk"), pair_stats("pooled", "shrunk")]

    per_method = {
        m: {
            "share_actionable": float(comp[f"{m}_actionable"].mean()),
            "share_keep_current": float((comp[f"{m}_decision"] == "KEEP_CURRENT").mean()),
            "share_review_required": float((comp[f"{m}_decision"] == "REVIEW_REQUIRED").mean()),
            "median_change_pct": float(comp.loc[comp[f"{m}_actionable"], f"{m}_change_pct"].median())
            if comp[f"{m}_actionable"].any() else 0.0,
            "share_increases": float((comp.loc[comp[f"{m}_actionable"], f"{m}_change_pct"] > 0).mean())
            if comp[f"{m}_actionable"].any() else 0.0,
            "median_internal_uplift_pct": float(
                comp.loc[comp[f"{m}_actionable"], f"{m}_uplift"].median()
            ) if comp[f"{m}_actionable"].any() else 0.0,
            "mean_recommended_price": float(comp[f"{m}_price"].mean()),
        }
        for m in METHODS
    }

    # -- sensitivity scenarios ----------------------------------------------
    scenarios = [float(x) for x in cfg.get("elasticity.sensitivity_scenarios", [-1.5, -1.9, -2.4, -3.1])]
    rep = comp.nlargest(5, "current_price").index.tolist()[:3] + comp.nsmallest(2, "current_price").index.tolist()
    # Two regimes: the production guardrails (which often bind, and that is the
    # point), and a deliberately wide what-if regime that exposes the pure
    # economics of the elasticity assumption.
    WIDE = {
        "max_price_change_pct": 0.60,
        "extrapolation_tolerance_pct": 0.60,
        "min_gross_margin_rate": 0.0,
        "materiality_threshold_pct": 0.0,
    }
    sens_rows = []
    for idx in dict.fromkeys(rep):
        row = sample.iloc[[idx]]
        series_stats = row.iloc[0][STAT_COLUMNS].to_dict()
        cost = float(row["decision_time_unit_cost"].iloc[0])
        for eps in scenarios:
            model = HybridPricingModel(
                base, method="pooled", elasticity_table=fixed_elasticity_table(eps), cfg=cfg
            )
            constrained = optimize_price(
                model, row, cfg=cfg, policy_profile=args.profile,
                objective="gross_profit", series_stats=series_stats,
            )
            wide = optimize_price(
                model, row, cfg=cfg, policy_profile=args.profile,
                objective="gross_profit", series_stats=series_stats, policy_override=WIDE,
            )
            analytic = cost * eps / (1.0 + eps) if eps < -1 else float("nan")
            sens_rows.append(
                {
                    "upc": constrained.upc,
                    "store": constrained.store,
                    "descrip": str(row["descrip"].iloc[0])[:22],
                    "assumed_elasticity": eps,
                    "current_price": constrained.current_price,
                    "unit_cost": cost,
                    "candidate_price_constrained": constrained.proposed_candidate_price,
                    "candidate_price_wide": wide.proposed_candidate_price,
                    "analytic_ce_optimum": analytic,
                    "price_change_pct": constrained.price_change_pct,
                    "predicted_units": constrained.predicted_units_recommended,
                    "expected_gross_profit": constrained.expected_gross_profit_recommended,
                    "internal_uplift_pct": constrained.model_internal_estimated_profit_uplift_pct,
                    "decision": constrained.decision,
                }
            )
    sens = pd.DataFrame(sens_rows)

    def spread_of(col: str) -> pd.DataFrame:
        return (
            sens.groupby(["upc", "store"], observed=True)[col]
            .agg(["min", "max", "mean"])
            .assign(spread_pct=lambda d: 100 * (d["max"] - d["min"]) / d["mean"])
        )

    spread = spread_of("candidate_price_constrained")
    spread_wide = spread_of("candidate_price_wide")

    payload = {
        "week": week,
        "n_contexts": int(len(comp)),
        "policy_profile": args.profile,
        "per_method": per_method,
        "pairwise_disagreement": pairs,
        "elasticity_used": {
            "pooled": float(models["shrunk"].elasticity_table.pooled),
            "shrunk_median": float(comp["elasticity_shrunk"].median()),
            "shrunk_p10": float(comp["elasticity_shrunk"].quantile(0.10)),
            "shrunk_p90": float(comp["elasticity_shrunk"].quantile(0.90)),
            "share_product_specific": float((comp["elasticity_source"] == "shrunk_product").mean()),
        },
        "sensitivity": sens.to_dict("records"),
        "sensitivity_price_spread_pct_median_constrained": float(spread["spread_pct"].median()),
        "sensitivity_price_spread_pct_median_wide": float(spread_wide["spread_pct"].median()),
        "runtime_seconds": round(time.time() - t0, 1),
    }
    write_json(cfg.path("metrics_dir") / "price_response_comparison.json", payload)

    # -- figures -------------------------------------------------------------
    fig, axes = plt.subplots(2, 4, figsize=(13, 6))
    for col, (_row, payload_curve) in enumerate(curves):
        grid = payload_curve["grid"]
        rec = payload_curve["record"]
        ax_d, ax_p = axes[0, col], axes[1, col]
        for name, sim in payload_curve["sims"].items():
            ax_d.plot(sim["candidate_price"], sim["predicted_units"], lw=1.2, label=name)
            ax_p.plot(sim["candidate_price"], sim["expected_gross_profit"], lw=1.2, label=name)
        for ax in (ax_d, ax_p):
            ax.axvline(rec["current_price"], color="grey", ls="--", lw=0.8)
            ax.set_xlabel("price ($)")
        ax_d.set_title(f"{rec['descrip'][:18]} st{rec['store']}", fontsize=8)
        ax_d.set_ylabel("predicted units")
        ax_p.set_ylabel("expected gross profit ($)")
        if col == 0:
            ax_d.legend(fontsize=7)
    fig.suptitle("Price response by method (grey = current price)", fontsize=10)
    fig.savefig(fig_dir / "compare_demand_profit_curves.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    for m in METHODS:
        ax.hist(comp[f"{m}_change_pct"] * 100, bins=40, alpha=0.5, label=m)
    ax.set_xlabel("recommended price change (%)")
    ax.set_title("Recommended change distribution by price-response method")
    ax.legend()
    fig.savefig(fig_dir / "compare_change_distribution.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    for (_upc, store), sub in sens.groupby(["upc", "store"], observed=True):
        ax.plot(sub["assumed_elasticity"], sub["candidate_price_wide"], marker="o", ms=3,
                label=f"{sub['descrip'].iloc[0]} st{store}")
    ax.set_xlabel("assumed elasticity")
    ax.set_ylabel("candidate profit-maximising price ($)")
    ax.set_title("Candidate profit-maximising price vs assumed elasticity "
                 "(wide what-if guardrails, model assumptions)")
    ax.legend(fontsize=6)
    fig.savefig(fig_dir / "compare_elasticity_sensitivity.png")
    plt.close(fig)

    # -- report --------------------------------------------------------------
    def md(df_: pd.DataFrame, floatfmt: str = "{:,.4f}") -> str:
        cols = list(df_.columns)
        head = "| " + " | ".join(cols) + " |"
        sep = "| " + " | ".join("---" for _ in cols) + " |"
        body = []
        for _, r in df_.iterrows():
            vals = []
            for c in cols:
                v = r[c]
                vals.append(floatfmt.format(v) if isinstance(v, (float, np.floating)) else str(v)[:26])
            body.append("| " + " | ".join(vals) + " |")
        return "\n".join([head, sep, *body])

    eu = payload["elasticity_used"]
    method_rows = "\n".join(
        f"| {m} | {100*per_method[m]['share_actionable']:.1f}% | "
        f"{100*per_method[m]['share_keep_current']:.1f}% | {100*per_method[m]['share_review_required']:.1f}% | "
        f"{100*per_method[m]['median_change_pct']:+.2f}% | {100*per_method[m]['share_increases']:.1f}% | "
        f"{100*per_method[m]['median_internal_uplift_pct']:+.2f}% | ${per_method[m]['mean_recommended_price']:.3f} |"
        for m in METHODS
    )
    pair_rows = "\n".join(
        f"| {p['pair']} | ${p['mean_abs_price_gap']:.3f} | ${p['median_abs_price_gap']:.3f} | "
        f"${p['p90_abs_price_gap']:.3f} | {100*p['mean_abs_gap_pct_of_price']:.2f}% | "
        f"{100*p['share_identical_price']:.1f}% | {100*p['share_same_decision_state']:.1f}% | "
        f"{100*p['share_opposite_direction']:.1f}% |"
        for p in pairs
    )

    report = f"""# 08 - Price-response method comparison (Phase L)

Generated by `python scripts/compare_price_response.py --n-contexts {args.n_contexts}`.

Baseline demand model: `{base.metadata.version}` (test WAPE
{base.metadata.metrics['test']['wape']:.4f}). Decision week **{week}**,
{len(comp):,} eligible UPC x store contexts, policy profile **{args.profile}**,
objective gross profit.

## 1. Why this comparison exists

The selected model has the best WAPE, but WAPE is measured **at the prices that
were actually charged**. Its implied elasticity (about -3.10) is materially
steeper than the controlled econometric estimate (-1.91 to -2.42). Using the
predictive model directly for counterfactuals therefore bakes that steepness
into every recommendation.

The hybrid separates the two jobs:

```text
Q(p) = Q_hat(p0) * (p / p0) ** epsilon
```

`Q_hat(p0)` is the ML model's baseline prediction **at the current price** (all
context, seasonality, promotion and lag structure), and `epsilon` is a
separately estimated elasticity fitted on **training weeks only**. At `p = p0`
the hybrid reproduces the ML prediction exactly. That preserves the
selected forecaster's baseline prediction **at the reference price only**;
counterfactual predictions away from the reference price are governed by the
elasticity model and their accuracy is a separate question (see
`reports/16_OUT_OF_TIME_PRICE_RESPONSE.md`).

## 2. The elasticities in play

| source | value |
| --- | --- |
| native ML implied elasticity (finite difference, Phase F) | **-3.10** |
| pooled controlled (UPC x store FE + promo/season/trend, training only) | **{eu['pooled']:.3f}** |
| shrunk per-UPC, median over sampled contexts | **{eu['shrunk_median']:.3f}** |
| shrunk per-UPC, p10 / p90 | {eu['shrunk_p10']:.3f} / {eu['shrunk_p90']:.3f} |
| contexts served by a product-specific (shrunk) estimate | {100*eu['share_product_specific']:.1f}% |

## 3. Recommendation behaviour by method

| method | actionable | keep current | review required | median change | share increases | median internal uplift | mean price |
| --- | --- | --- | --- | --- | --- | --- | --- |
{method_rows}

![change distribution](../artifacts/figures/compare_change_distribution.png)

## 4. How much do the methods disagree?

| pair | mean abs price gap | median | p90 | mean gap as % of price | identical price | same decision state | opposite direction |
| --- | --- | --- | --- | --- | --- | --- | --- |
{pair_rows}

Disagreement of this size is the headline finding: the *same* baseline demand
model, the *same* constraints and the *same* cost produce materially different
prices depending only on which price-response assumption is used. Any single
number quoted without stating the price-response method is meaningless.

![curves](../artifacts/figures/compare_demand_profit_curves.png)

## 5. Elasticity sensitivity (Task 5)

Candidate profit-maximising price recomputed for representative contexts under fixed elasticity
scenarios. **These are scenarios, not estimates** - nobody can measure the true
elasticity from this observational data.

{md(sens[["descrip", "store", "assumed_elasticity", "current_price", "unit_cost", "candidate_price_constrained", "candidate_price_wide", "analytic_ce_optimum", "predicted_units", "expected_gross_profit", "internal_uplift_pct", "decision"]], "{:,.3f}")}

Two regimes are shown on purpose:

* `candidate_price_constrained` - under the production guardrails
  (**{args.profile}** profile). Median spread across the whole scenario range:
  **{payload['sensitivity_price_spread_pct_median_constrained']:.2f}%**.
* `candidate_price_wide` - under deliberately wide what-if guardrails
  (+/-60% change, +/-60% extrapolation, no margin floor, no materiality).
  Median spread: **{payload['sensitivity_price_spread_pct_median_wide']:.2f}%**.
* `analytic_ce_optimum` - the textbook constant-elasticity optimum
  `p* = c * e / (1 + e)`, for reference.

The gap between the two regimes is itself the finding: under production
guardrails the **constraints**, not the elasticity, usually determine the
recommended price, which is exactly why the guardrails deserve as much
scrutiny as the model. Remove them and the elasticity assumption moves the
optimum substantially.

![sensitivity](../artifacts/figures/compare_elasticity_sensitivity.png)

Reading: with inelastic assumptions the optimizer pushes price up to the
guardrail; with elastic assumptions it moves less, or cuts. The recommendation
is therefore a function of an assumption, and the assumption deserves at least
as much scrutiny as the model.

## 6. Which method should be the default?

**`shrunk` (per-UPC empirical-Bayes elasticity).** Reasons:

1. Its price response comes from an estimator built for exactly that question,
   with controls for product, store, promotion, seasonality and trend - not
   from a forecaster's incidental price coefficient.
2. It is anchored on evidence per product, but noisy products are pulled back
   to the category estimate, so a thin series cannot drive a large price move.
3. It sits between the two extremes: less steep than the native ML response
   (-3.10) and more product-aware than a single pooled number.
4. Its provenance is auditable: every recommendation records the elasticity
   used, its source (`shrunk_product` or `pooled_fallback`) and the estimation
   window.

The native ML response is **kept** as `--method ml` and remains the benchmark
in this report. It is not deleted, because it is the honest comparison point
and because a future model whose implied elasticity matches the econometric
evidence would deserve to be reconsidered.

## 7. Honest limitations

* All three methods are **observational**. None identifies a causal price
  effect; see `docs/CAUSAL_LIMITATIONS.md`.
* The uplift columns are **model-internal estimates**: the same fitted response
  both proposes and scores the price.
* Shrinkage improves stability, not identification. If the pooled elasticity is
  biased by promotion contamination, every shrunk estimate inherits part of
  that bias.

Runtime: {payload['runtime_seconds']}s.
"""
    out = cfg.path("reports_dir") / "08_PRICE_RESPONSE_COMPARISON.md"
    out.write_text(report, encoding="utf-8")
    print(f"wrote {out}")
    for p in pairs:
        print(f"  {p['pair']:18s} mean |dP| ${p['mean_abs_price_gap']:.3f} "
              f"({100*p['mean_abs_gap_pct_of_price']:.2f}% of price), "
              f"same decision {100*p['share_same_decision_state']:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
