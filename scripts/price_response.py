"""Phase F - validate the selected model's price response before optimising.

    python scripts/price_response.py [--n-contexts 400]

An accurate forecast is not a licence to price. This script holds every
non-price feature fixed, sweeps the candidate price, and checks the shape of
the resulting demand curve:

* is predicted demand decreasing in price?
* is the response flat (model ignores price)?
* how steep is the implied elasticity, compared with the controlled
  econometric estimates?
* what happens outside the observed price support?

Outputs
-------
    reports/05_PRICE_RESPONSE_VALIDATION.md
    artifacts/metrics/price_response.json
    artifacts/figures/price_response_*.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.features.build import training_frame  # noqa: E402
from pricing_engine.models.demand_model import load_model  # noqa: E402
from pricing_engine.simulation.counterfactual import (  # noqa: E402
    demand_curve_diagnostics,
    simulate_price_grid,
)
from pricing_engine.utils.io import ensure_dir, write_json  # noqa: E402

plt.rcParams.update({"figure.dpi": 120, "figure.autolayout": True, "font.size": 9})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-contexts", type=int, default=400)
    parser.add_argument("--sweep-pct", type=float, default=0.30)
    args = parser.parse_args()

    cfg = load_config()
    fig_dir = ensure_dir(cfg.path("figures_dir"))
    model = load_model(cfg.path("models_dir") / "demand_model.joblib", cfg=cfg)
    meta = model.metadata

    feats = pd.read_parquet(cfg.path("features_table"))
    usable = training_frame(feats)
    test_weeks = meta.test_weeks if meta else [int(usable["week"].max()) - 10, int(usable["week"].max())]
    contexts_pool = usable[usable["week"].between(test_weeks[0], test_weeks[1])]

    var = pd.read_csv(cfg.path("metrics_dir") / "price_variation_upc_store.csv")
    eligible = var[var["eligible"]][["upc", "store", "n_obs", "n_distinct_prices", "price_cv", "price_min", "price_max"]]
    pool = contexts_pool.merge(eligible, on=["upc", "store"], how="inner")
    pool = pool[pool["week"] == pool["week"].max()]
    print(f"{len(pool):,} eligible contexts in the final test week {int(pool['week'].max())}")

    sample = pool.sample(min(args.n_contexts, len(pool)), random_state=cfg.seed).reset_index(drop=True)

    diagnostics = []
    curves = []
    for i in range(len(sample)):
        row = sample.iloc[[i]]
        p0 = float(row["effective_unit_price"].iloc[0])
        cost = float(row["decision_time_unit_cost"].iloc[0])
        grid = np.round(np.linspace(p0 * (1 - args.sweep_pct), p0 * (1 + args.sweep_pct), 25), 2)
        grid = np.unique(grid[grid > 0])
        sim = simulate_price_grid(model, row, grid, unit_cost=cost if np.isfinite(cost) and cost > 0 else None)
        d = demand_curve_diagnostics(sim)
        d.update(
            {
                "upc": int(row["upc"].iloc[0]),
                "store": int(row["store"].iloc[0]),
                "current_price": p0,
                "hist_price_min": float(row["price_min"].iloc[0]),
                "hist_price_max": float(row["price_max"].iloc[0]),
                "sweep_low_outside_support": bool(grid.min() < float(row["price_min"].iloc[0])),
                "sweep_high_outside_support": bool(grid.max() > float(row["price_max"].iloc[0])),
                "profit_optimum_price": float(
                    sim.loc[sim["expected_gross_profit"].idxmax(), "candidate_price"]
                )
                if sim["expected_gross_profit"].notna().any()
                else np.nan,
                "revenue_optimum_price": float(
                    sim.loc[sim["expected_revenue"].idxmax(), "candidate_price"]
                ),
            }
        )
        diagnostics.append(d)
        if i < 6:
            curves.append((row, sim))

    diag = pd.DataFrame(diagnostics)

    summary = {
        "n_contexts": int(len(diag)),
        "decision_week": int(sample["week"].max()),
        "sweep_pct": args.sweep_pct,
        "share_monotone_decreasing": float(diag["monotone_decreasing"].mean()),
        "share_with_any_increasing_segment": float((diag["share_segments_increasing"] > 0).mean()),
        "mean_share_increasing_segments": float(diag["share_segments_increasing"].mean()),
        "share_flat_response": float(diag["flat_response"].mean()),
        "median_local_elasticity": float(diag["median_local_elasticity"].median()),
        "p10_local_elasticity": float(diag["median_local_elasticity"].quantile(0.10)),
        "p90_local_elasticity": float(diag["median_local_elasticity"].quantile(0.90)),
        "share_negative_predicted_units": float((diag["min_predicted_units"] < 0).mean()),
        "median_relative_demand_span": float(diag["relative_demand_span"].median()),
        "share_profit_optimum_below_current": float(
            (diag["profit_optimum_price"] < diag["current_price"]).mean()
        ),
        "share_profit_optimum_at_grid_edge": float(
            (
                (diag["profit_optimum_price"] <= diag["current_price"] * (1 - args.sweep_pct) + 1e-6)
                | (diag["profit_optimum_price"] >= diag["current_price"] * (1 + args.sweep_pct) - 1e-6)
            ).mean()
        ),
        "share_revenue_optimum_below_current": float(
            (diag["revenue_optimum_price"] < diag["current_price"]).mean()
        ),
        "model_implied_elasticity_valid_window": (meta.metrics if meta else {}),
    }
    write_json(cfg.path("metrics_dir") / "price_response.json", {"summary": summary,
                                                                "contexts": diag.to_dict("records")[:100]})

    # -- figures -------------------------------------------------------------
    fig, axes = plt.subplots(2, 3, figsize=(11, 5.6))
    for ax, (row, sim) in zip(axes.ravel(), curves, strict=False):
        ax.plot(sim["candidate_price"], sim["predicted_units"], marker="o", ms=2.5, lw=1)
        ax.axvline(float(row["effective_unit_price"].iloc[0]), color="crimson", ls="--", lw=0.9)
        ax.set_title(f"UPC {int(row['upc'].iloc[0])} store {int(row['store'].iloc[0])}", fontsize=8)
        ax.set_xlabel("price ($)")
        ax.set_ylabel("predicted units")
    fig.suptitle("Predicted demand curves at fixed context (red = current price)", fontsize=10)
    fig.savefig(fig_dir / "price_response_demand_curves.png")
    plt.close(fig)

    row, sim = curves[0]
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    ax.plot(sim["candidate_price"], sim["expected_revenue"], label="expected revenue", lw=1.2)
    ax.plot(sim["candidate_price"], sim["expected_gross_profit"], label="expected gross profit", lw=1.2)
    ax.axvline(float(row["effective_unit_price"].iloc[0]), color="crimson", ls="--", lw=0.9, label="current price")
    ax.set_xlabel("candidate price ($)")
    ax.set_ylabel("$ per store-week")
    ax.set_title(
        f"Model-internal estimated economics, UPC {int(row['upc'].iloc[0])} store {int(row['store'].iloc[0])}"
    )
    ax.legend()
    fig.savefig(fig_dir / "price_response_economics.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 3.2))
    ax.hist(diag["median_local_elasticity"].clip(-12, 4), bins=50)
    ax.axvline(-1, color="crimson", ls="--", label="unit elastic")
    ax.set_title("Model-implied local elasticity across sampled contexts")
    ax.legend()
    fig.savefig(fig_dir / "price_response_elasticity_hist.png")
    plt.close(fig)

    # -- report --------------------------------------------------------------
    report = f"""# 05 - Price-response validation (native ML response)

Generated by `python scripts/price_response.py --n-contexts {args.n_contexts}`.

Selected model: **{meta.name if meta else 'unknown'}** (`{meta.version if meta else 'n/a'}`),
test WAPE {meta.metrics['test']['wape']:.4f} (see `reports/04_MODEL_COMPARISON.md`).

> This report probes the **native ML price response** - the demand model
> answering price counterfactuals directly. Phase L found that response too
> steep and made an elasticity-based hybrid the default pricing method; see
> `reports/08_PRICE_RESPONSE_COMPARISON.md`. The native response is kept as the
> benchmark, and this report is the evidence that motivated the change.

## Why this phase exists

Forecast accuracy is measured at the prices that were actually charged. A
pricing engine asks the model something it was never scored on: *what would
demand be at a price that was not charged?* A model can be accurate and still
have a wrong-signed, flat or discontinuous price response. This report tests
that dimension before the optimizer is allowed to use the model.

## Method

* {summary['n_contexts']} eligible UPC x store contexts from decision week {summary['decision_week']}
  (the final test week), sampled with seed {cfg.seed}.
* For each: hold every context feature fixed, sweep price +/-{100*args.sweep_pct:.0f}% around
  the current price on a 25-point grid, recompute **all** price-dependent
  features, and predict in a single batched call.
* Cost is held fixed at the decision-time (lagged) AAC for the whole sweep.

## Results

| check | value | verdict |
| --- | --- | --- |
| curves strictly monotone decreasing | {100*summary['share_monotone_decreasing']:.1f}% | {"PASS" if summary['share_monotone_decreasing'] > 0.9 else "REVIEW"} |
| curves with at least one increasing segment | {100*summary['share_with_any_increasing_segment']:.1f}% | {"PASS" if summary['share_with_any_increasing_segment'] < 0.1 else "REVIEW"} |
| mean share of increasing segments per curve | {100*summary['mean_share_increasing_segments']:.2f}% | - |
| flat curves (demand span < 1% of mean) | {100*summary['share_flat_response']:.2f}% | {"PASS" if summary['share_flat_response'] < 0.05 else "REVIEW"} |
| negative predicted demand anywhere | {100*summary['share_negative_predicted_units']:.2f}% | {"PASS" if summary['share_negative_predicted_units'] == 0 else "FAIL"} |
| median demand span across the sweep | {100*summary['median_relative_demand_span']:.1f}% of mean units | - |

### Implied local elasticity

| statistic | value |
| --- | --- |
| median | {summary['median_local_elasticity']:.3f} |
| p10 | {summary['p10_local_elasticity']:.3f} |
| p90 | {summary['p90_local_elasticity']:.3f} |

Compare with the controlled econometric estimates in
`reports/03_ELASTICITY_ANALYSIS.md`: -2.42 (UPC x store fixed effects) and
-1.91 (adding promotion, seasonality and trend). **The model's implied price
response is steeper than the controlled econometric estimate.** That is a
material caveat, not a detail: a model that over-estimates price sensitivity
will systematically recommend price cuts. It is one of the reasons the
optimizer is constrained to small moves, is bounded by observed price support,
and requires a materiality threshold before it changes anything.

### Where the optimum lands

| statistic | value |
| --- | --- |
| profit optimum below the current price | {100*summary['share_profit_optimum_below_current']:.1f}% of contexts |
| revenue optimum below the current price | {100*summary['share_revenue_optimum_below_current']:.1f}% of contexts |
| profit optimum sitting at the edge of the sweep | {100*summary['share_profit_optimum_at_grid_edge']:.1f}% of contexts |

A high share of edge solutions means the unconstrained optimum lies outside the
swept range - exactly the situation where extrapolation guardrails matter,
because the model has no data there.

![demand curves](../artifacts/figures/price_response_demand_curves.png)
![economics](../artifacts/figures/price_response_economics.png)
![implied elasticity](../artifacts/figures/price_response_elasticity_hist.png)

## Decisions taken as a result

1. No monotonicity constraint is imposed on the model. Forcing monotone price
   response would hide the diagnostic rather than fix it; instead the optimizer
   records `DEMAND_CURVE_NOT_DECREASING` when a curve misbehaves for a series.
2. The extrapolation guardrail is kept: candidate prices stay inside the
   observed support of the series, expanded by the configured tolerance.
3. Because implied elasticity is steeper than the econometric benchmark, the
   default policy profile keeps `max_price_change_pct` small and the
   materiality threshold non-zero.
4. Recommendations are reported as **model-internal estimates**, never as realised
   uplift.
"""
    out = cfg.path("reports_dir") / "05_PRICE_RESPONSE_VALIDATION.md"
    out.write_text(report, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
