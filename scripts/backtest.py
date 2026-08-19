"""Phase H - offline / historical evaluation of pricing policies.

    python scripts/backtest.py [--weeks 12] [--contexts-per-week 300]

What this can and cannot do
---------------------------
For every historical week we observe the outcome **only at the price actually
charged**. Demand at any alternative price was never observed. Therefore this
script does NOT claim that an optimized price would have increased profit. It
reports:

* demand-prediction quality through time (this IS verifiable against outcomes);
* recommendation stability week to week;
* distance from historical price support;
* frequency of no-change recommendations and constraint activation;
* **model-internal estimated** (offline policy simulation) economics per policy.

Outputs
-------
    reports/06_BACKTEST.md
    artifacts/metrics/backtest.json
    artifacts/figures/backtest_*.png
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
from pricing_engine.features.build import recompute_price_features, training_frame  # noqa: E402
from pricing_engine.models.demand_model import load_model  # noqa: E402
from pricing_engine.models.hybrid import attach_reference_price, load_pricing_model  # noqa: E402
from pricing_engine.models.metrics import evaluate  # noqa: E402
from pricing_engine.optimization.policies import (  # noqa: E402
    ElasticityBaselinePolicy,
    HistoricalPricePolicy,
    MLPricingPolicy,
    SimpleMarginPolicy,
)
from pricing_engine.utils.io import ensure_dir, write_json  # noqa: E402

plt.rcParams.update({"figure.dpi": 120, "figure.autolayout": True, "font.size": 9})

STAT_COLUMNS = ["n_obs", "n_distinct_prices", "price_cv", "price_min", "price_max"]


def score_policy_prices(model, contexts: pd.DataFrame, prices: np.ndarray) -> pd.DataFrame:
    """Model-internal estimated economics for policy prices (cost held fixed)."""
    # The reference price is the OBSERVED price of each context, stamped before
    # any policy price is substituted.
    contexts = attach_reference_price(contexts)
    frame = recompute_price_features(contexts, prices)
    units = np.asarray(model.predict(frame), dtype="float64")
    cost = contexts["decision_time_unit_cost"].to_numpy(dtype="float64")
    return pd.DataFrame(
        {
            "price": prices,
            "predicted_units": units,
            "expected_revenue": prices * units,
            "expected_gross_profit": np.where(np.isfinite(cost), (prices - cost) * units, np.nan),
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weeks", type=int, default=12)
    parser.add_argument("--contexts-per-week", type=int, default=300)
    parser.add_argument("--profile", default="standard")
    parser.add_argument("--method", default=None, choices=["ml", "pooled", "shrunk"],
                        help="Price-response method (default: configs/config.yaml).")
    args = parser.parse_args()

    cfg = load_config()
    t0 = time.time()
    fig_dir = ensure_dir(cfg.path("figures_dir"))
    base = load_model(cfg.path("models_dir") / "demand_model.joblib", cfg=cfg)
    model = load_pricing_model(base, cfg=cfg, method=args.method)
    meta = base.metadata
    print(f"price-response method: {model.method.value}")

    feats = pd.read_parquet(cfg.path("features_table"))
    usable = training_frame(feats)
    var = pd.read_csv(cfg.path("metrics_dir") / "price_variation_upc_store.csv")
    stats_table = var[["upc", "store", *STAT_COLUMNS, "eligible"]]
    elas = pd.read_csv(cfg.path("metrics_dir") / "elasticity_by_upc.csv")

    # Only weeks strictly after the model's training window are scored.
    test_lo, test_hi = (meta.test_weeks if meta else [int(usable["week"].max()) - args.weeks, int(usable["week"].max())])
    weeks = sorted(w for w in usable["week"].unique() if test_lo <= w <= test_hi)[-args.weeks:]
    print(f"backtesting weeks {weeks[0]}..{weeks[-1]} (model trained on weeks <= {meta.train_weeks[1] if meta else '?'})")

    policies = [
        HistoricalPricePolicy(),
        SimpleMarginPolicy(target_margin=0.20),
        ElasticityBaselinePolicy(elas),
        MLPricingPolicy(model, cfg, policy_profile=args.profile, objective="gross_profit"),
    ]
    policy_cfg = cfg.policy(args.profile)

    weekly_accuracy = []
    policy_rows = []
    ml_prices_by_week: dict[int, pd.DataFrame] = {}

    # Track the SAME series every week, otherwise week-to-week stability cannot
    # be measured (a fresh random sample shares almost nothing across weeks).
    first = usable[usable["week"] == weeks[0]].merge(stats_table, on=["upc", "store"], how="left")
    first = first[first["decision_time_unit_cost"].notna() & (first["decision_time_unit_cost"] > 0)]
    tracked = (
        first.sample(min(args.contexts_per_week, len(first)), random_state=cfg.seed)[["upc", "store"]]
        .drop_duplicates()
        .reset_index(drop=True)
    )
    print(f"tracking {len(tracked):,} fixed UPC x store series across the window")

    for week in weeks:
        wk = usable[usable["week"] == week].merge(stats_table, on=["upc", "store"], how="left")
        wk = wk[wk["decision_time_unit_cost"].notna() & (wk["decision_time_unit_cost"] > 0)]
        if wk.empty:
            continue

        # (1) verifiable: demand prediction quality against realised units.
        # Accuracy is a property of the BASELINE forecaster at the observed
        # price; the price-response layer only matters for counterfactuals.
        pred = base.predict(wk)
        m = evaluate(wk["move"], pred)
        m["week"] = int(week)
        m["week_start_date"] = str(pd.Timestamp(wk["week_start_date"].iloc[0]).date())
        weekly_accuracy.append(m)

        # (2) policy comparison on a sample of contexts
        sample = wk.merge(tracked, on=["upc", "store"], how="inner").reset_index(drop=True)
        if sample.empty:
            continue
        stats = sample[STAT_COLUMNS]
        for policy in policies:
            result = policy.recommend(sample, policy_cfg=policy_cfg, stats=stats)
            scored = score_policy_prices(model, sample, result.prices)
            change = result.prices / sample["effective_unit_price"].to_numpy(dtype="float64") - 1.0
            outside = (
                (result.prices < sample["price_min"].to_numpy(dtype="float64"))
                | (result.prices > sample["price_max"].to_numpy(dtype="float64"))
            )
            policy_rows.append(
                {
                    "week": int(week),
                    "policy": policy.name,
                    "n": int(len(sample)),
                    "expected_revenue": float(scored["expected_revenue"].sum()),
                    "expected_gross_profit": float(scored["expected_gross_profit"].sum(skipna=True)),
                    "predicted_units": float(scored["predicted_units"].sum()),
                    "mean_price": float(np.mean(result.prices)),
                    "share_no_change": float(np.mean(np.abs(change) < 1e-9)),
                    "median_abs_change_pct": float(np.median(np.abs(change))),
                    "share_outside_historical_support": float(np.mean(outside)),
                    "share_constrained": float(
                        np.mean([1.0 if any(c in r for c in ("PRICE_CHANGE_LIMIT", "OUTSIDE_EXTRAPOLATION_RANGE", "MARGIN_CONSTRAINT", "CLIPPED_TO_GUARDRAILS")) else 0.0 for r in result.reason_codes])
                    ),
                }
            )
            if policy.name == "MLPricingPolicy":
                ml_prices_by_week[int(week)] = pd.DataFrame(
                    {
                        "upc": sample["upc"],
                        "store": sample["store"],
                        "final_price": result.prices,
                        "current_price": sample["effective_unit_price"].to_numpy(dtype="float64"),
                    }
                )
        print(f"  week {week}: WAPE={m['wape']:.4f} ({len(sample)} contexts scored per policy)")

    acc = pd.DataFrame(weekly_accuracy)
    pol = pd.DataFrame(policy_rows)

    # (3) recommendation stability: how often does the recommendation flip?
    stability = []
    weeks_sorted = sorted(ml_prices_by_week)
    for a, b in zip(weeks_sorted, weeks_sorted[1:], strict=False):
        left = ml_prices_by_week[a].set_index(["upc", "store"])
        right = ml_prices_by_week[b].set_index(["upc", "store"])
        joined = left.join(right, how="inner", lsuffix="_prev", rsuffix="_now")
        if joined.empty:
            continue
        rel = (joined["final_price_now"] / joined["final_price_prev"] - 1.0).abs()
        stability.append(
            {
                "week_pair": f"{a}->{b}",
                "n_common_series": int(len(joined)),
                "median_abs_recommendation_change_pct": float(rel.median()),
                "share_recommendation_moved_gt_5pct": float((rel > 0.05).mean()),
            }
        )
    stab = pd.DataFrame(stability)

    totals = pol.groupby("policy", observed=True).agg(
        expected_revenue=("expected_revenue", "sum"),
        expected_gross_profit=("expected_gross_profit", "sum"),
        predicted_units=("predicted_units", "sum"),
        mean_price=("mean_price", "mean"),
        share_no_change=("share_no_change", "mean"),
        median_abs_change_pct=("median_abs_change_pct", "mean"),
        share_outside_support=("share_outside_historical_support", "mean"),
        share_constrained=("share_constrained", "mean"),
    )
    base_gp = float(totals.loc["HistoricalPricePolicy", "expected_gross_profit"])
    base_rev = float(totals.loc["HistoricalPricePolicy", "expected_revenue"])
    totals["model_internal_estimated_gp_vs_historical_pct"] = 100.0 * (totals["expected_gross_profit"] / base_gp - 1.0)
    totals["model_internal_estimated_rev_vs_historical_pct"] = 100.0 * (totals["expected_revenue"] / base_rev - 1.0)

    # Sanity anchor: at the historical price, how well does the model reproduce
    # the observed units? This bounds how much to trust the counterfactuals.
    anchor = {
        "weekly_wape_mean": float(acc["wape"].mean()),
        "weekly_wape_min": float(acc["wape"].min()),
        "weekly_wape_max": float(acc["wape"].max()),
        "weekly_bias_mean": float(acc["bias"].mean()),
    }

    payload = {
        "weeks": [int(w) for w in weeks],
        "model_version": meta.version if meta else None,
        "train_weeks": meta.train_weeks if meta else None,
        "policy_profile": args.profile,
        "price_response_method": model.method.value,
        "accuracy_by_week": acc.to_dict("records"),
        "accuracy_anchor": anchor,
        "policy_totals": totals.reset_index().to_dict("records"),
        "policy_by_week": pol.to_dict("records"),
        "stability": stab.to_dict("records"),
        "runtime_seconds": round(time.time() - t0, 1),
    }
    write_json(cfg.path("metrics_dir") / "backtest.json", payload)

    # -- figures -------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(7, 3.2))
    ax.plot(acc["week"], acc["wape"], marker="o", ms=3)
    ax.set_xlabel("Dominick's week")
    ax.set_ylabel("WAPE")
    ax.set_title("Demand-prediction quality through the backtest window")
    fig.savefig(fig_dir / "backtest_wape_by_week.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 3.4))
    for name, sub in pol.groupby("policy", observed=True):
        ax.plot(sub["week"], sub["expected_gross_profit"], marker="o", ms=3, label=name)
    ax.set_xlabel("Dominick's week")
    ax.set_ylabel("model-internal estimated gross profit ($)")
    ax.set_title("Offline policy simulation (model-internal estimate, NOT realised)")
    ax.legend(fontsize=7)
    fig.savefig(fig_dir / "backtest_policy_profit.png")
    plt.close(fig)

    # -- report --------------------------------------------------------------
    def totals_table() -> str:
        head = ("| policy | model-internal estimated gross profit | vs historical | model-internal estimated revenue | "
                "vs historical | mean price | share no change | median abs change | share outside support | share constrained |")
        sep = "| " + " | ".join("---" for _ in range(10)) + " |"
        rows = []
        for name, r in totals.iterrows():
            rows.append(
                f"| {name} | ${r['expected_gross_profit']:,.0f} | {r['model_internal_estimated_gp_vs_historical_pct']:+.2f}% | "
                f"${r['expected_revenue']:,.0f} | {r['model_internal_estimated_rev_vs_historical_pct']:+.2f}% | "
                f"${r['mean_price']:.2f} | {100*r['share_no_change']:.1f}% | {100*r['median_abs_change_pct']:.2f}% | "
                f"{100*r['share_outside_support']:.2f}% | {100*r['share_constrained']:.1f}% |"
            )
        return "\n".join([head, sep, *rows])

    acc_rows = "\n".join(
        f"| {int(r['week'])} | {r['week_start_date']} | {int(r['n']):,} | {r['mae']:.2f} | {r['rmse']:.2f} | {r['wape']:.4f} | {r['bias']:+.2f} |"
        for _, r in acc.iterrows()
    )
    stab_rows = "\n".join(
        f"| {r['week_pair']} | {int(r['n_common_series']):,} | {100*r['median_abs_recommendation_change_pct']:.2f}% | {100*r['share_recommendation_moved_gt_5pct']:.1f}% |"
        for _, r in stab.iterrows()
    )

    report = f"""# 06 - Offline backtest and policy comparison

Generated by `python scripts/backtest.py --weeks {args.weeks} --contexts-per-week {args.contexts_per_week}`.

Model: `{meta.version if meta else 'n/a'}`, trained on weeks
{meta.train_weeks[0] if meta else '?'}-{meta.train_weeks[1] if meta else '?'}.
Backtest weeks: **{weeks[0]}-{weeks[-1]}** - strictly after the training window.

## 1. What is verifiable and what is not

| question | verifiable from data? |
| --- | --- |
| how accurately does the model predict demand at the prices actually charged? | **yes** - realised units are observed |
| how stable are the recommendations over time? | **yes** - property of the policy |
| how far do recommendations sit from historical price support? | **yes** |
| how often does the engine leave the price alone? | **yes** |
| would the recommended prices have earned more profit? | **no** - demand at unobserved prices was never measured |
| is the policy comparison an unbiased policy value? | **no** - the same fitted response proposes and scores the prices |

Everything in section 3 is therefore labelled **model-internal estimated /
offline policy simulation**, and the same demand model both chooses and scores the
prices, which is circular by construction. It is reported because it shows the
*direction and magnitude* the policy pushes toward, not because it proves gain.

## 2. Demand-prediction quality through time (verifiable)

| week | date | rows | MAE | RMSE | WAPE | bias |
| --- | --- | --- | --- | --- | --- | --- |
{acc_rows}

Mean weekly WAPE **{anchor['weekly_wape_mean']:.4f}** (min {anchor['weekly_wape_min']:.4f},
max {anchor['weekly_wape_max']:.4f}), mean bias {anchor['weekly_bias_mean']:+.2f} units.
This is the anchor for how much weight the counterfactual numbers below deserve:
a model that is ~{100*anchor['weekly_wape_mean']:.0f}% off in aggregate at *observed*
prices cannot be trusted to a fraction of a percent at unobserved ones.

![wape by week](../artifacts/figures/backtest_wape_by_week.png)

## 3. Policy comparison (offline policy simulation - model-internal estimate)

Each policy prices the same sampled contexts; the same demand model and the
same fixed decision-time cost then score every policy identically.

{totals_table()}

![policy profit](../artifacts/figures/backtest_policy_profit.png)

Reading:

* **HistoricalPricePolicy** is the reference: it is what actually happened.
* **SimpleMarginPolicy** ignores demand entirely; it moves prices toward a flat
  cost-plus target and is clipped by the same guardrails.
* **ElasticityBaselinePolicy** applies the textbook constant-elasticity markup
  using the observational per-UPC elasticities from Phase D.
* **MLPricingPolicy** is this project's constrained optimizer.

## 4. Recommendation stability

| week pair | common series | median absolute change in recommendation | share moving > 5% |
| --- | --- | --- | --- |
{stab_rows}

Unstable recommendations are operationally unusable even when they look
profitable: store staff cannot re-tag shelves every week on a model's whim.

## 5. Separation of concerns

Predictive accuracy (section 2) and policy quality (section 3) are different
things and are reported separately on purpose. A model can be the most accurate
forecaster available and still make bad pricing decisions - for example if its
implied elasticity is steeper than reality, as
`reports/05_PRICE_RESPONSE_VALIDATION.md` finds here.

Runtime: {payload['runtime_seconds']}s.
"""
    out = cfg.path("reports_dir") / "06_BACKTEST.md"
    out.write_text(report, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
