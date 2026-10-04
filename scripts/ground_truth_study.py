"""Ground-truth recovery study: how far is the engine from the truth when the truth is known?

    python scripts/ground_truth_study.py                 # 7 scenarios x 5 seeds
    python scripts/ground_truth_study.py --quick         # 2 scenarios x 1 seed (tests)

On Dominick's the true elasticity is unknown, so estimator bias cannot be
measured; only an experiment could. Here the UNCHANGED estimators and
optimizer run on synthetic panels whose true elasticities and costs are known
(``pricing_engine.data.synthetic``), under controlled confounding. Each
scenario switches on one mechanism that real retail data plausibly has:

* store pricing     - high-demand stores also charge more
* seasonal promos   - promotions cluster in high-demand weeks
* promo half / none - half or all promotions carry no sale code
* endogenous price  - the retailer partly prices on demand shocks it sees

Reported per scenario (mean over seeds):

* bias of the naive, fixed-effects and pooled-controlled elasticity against
  the observation-weighted true mean elasticity;
* per-product bias, RMSE and 95% interval coverage, raw and after shrinkage;
* decisions: profit regret of the engine's final price against the true
  profit-maximising price inside the same guardrail bounds, direction
  agreement, and the engine's self-scored (model-internal) uplift against the
  true uplift of the same prices.

This measures recovery under a known synthetic process. It is NOT evidence
about Dominick's, and the data-generating process uses the same
constant-elasticity form the engine assumes, which flatters the engine.

Outputs
-------
    artifacts/metrics/ground_truth_study.json
    artifacts/metrics/ground_truth_study_runs.csv
    artifacts/report_figures/fig_15_ground_truth_recovery.png
    reports/25_GROUND_TRUTH_STUDY.md
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import warnings
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(os.cpu_count() or 1))

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.data.cleaning import build_canonical  # noqa: E402
from pricing_engine.data.synthetic import SyntheticSpec, generate_panel, oracle_price  # noqa: E402
from pricing_engine.economics.elasticity import fit_loglog  # noqa: E402
from pricing_engine.economics.elasticity_store import build_elasticity_table  # noqa: E402
from pricing_engine.economics.metrics import price_variation_summary  # noqa: E402
from pricing_engine.features.build import (  # noqa: E402
    build_feature_table,
    temporal_split,
    training_frame,
)
from pricing_engine.models.demand_model import DemandModel  # noqa: E402
from pricing_engine.models.hybrid import HybridPricingModel  # noqa: E402
from pricing_engine.optimization.optimizer import optimize_price_batch  # noqa: E402
from pricing_engine.serving import STAT_COLUMNS  # noqa: E402
from pricing_engine.utils.io import utc_now, write_json  # noqa: E402

CLEAN = dict(store_confounding=0.0, promo_record_rate=1.0, promo_seasonality=0.0,
             price_endogeneity=0.0)
SCENARIOS: dict[str, dict] = {
    "clean": {},
    "store pricing": {"store_confounding": 1.0},
    "seasonal promos": {"promo_seasonality": 0.8},
    "promos half unrecorded": {"promo_record_rate": 0.5},
    "promos unrecorded": {"promo_record_rate": 0.0},
    "endogenous price": {"price_endogeneity": 0.5},
    "combined": {"store_confounding": 0.5, "promo_record_rate": 0.5, "promo_seasonality": 0.5,
                 "price_endogeneity": 0.3},
}
QUICK = ("clean", "promos unrecorded")


def profit(p, cost, eps, p0, q0):
    """True gross profit under the synthetic constant-elasticity truth."""
    return (p - cost) * q0 * np.power(p / p0, eps)


def run_one(spec: SyntheticSpec, cfg) -> dict:
    panel = generate_panel(spec)
    canonical, _ = build_canonical(panel.movement, panel.upc_meta, cfg=cfg)
    usable = training_frame(build_feature_table(canonical, cfg=cfg))
    train, _, test, split = temporal_split(usable, cfg=cfg)
    lo, hi = split["train_weeks"]

    truth = panel.truth.set_index("upc")["true_elasticity"]
    n_train = train.groupby("upc").size()
    weights = n_train.reindex(truth.index).fillna(0)
    true_mean = float(np.average(truth, weights=weights))

    table = build_elasticity_table(train, cfg=cfg, training_weeks=(int(lo), int(hi)))
    naive = fit_loglog(train, name="naive", absorb=None, controls=None, seed=cfg.seed)
    prods = table.products.set_index("upc").join(truth)
    used = prods[prods["usable_for_pricing"]]
    raw_err = used["elasticity_raw"] - used["true_elasticity"]
    fin_err = prods["elasticity_final"] - prods["true_elasticity"]
    covered = (raw_err.abs() <= 1.96 * used["std_error"]).mean() if len(used) else np.nan

    # --- decisions in the last test week -------------------------------------------
    model = HybridPricingModel(DemandModel("ridge_loglog", cfg=cfg).fit(train), method="shrunk",
                               elasticity_table=table, cfg=cfg)
    week = int(test["week"].max())
    stats = price_variation_summary(canonical, ["upc", "store"])
    ctx = test[test["week"] == week].merge(stats[["upc", "store", *STAT_COLUMNS]],
                                           on=["upc", "store"], how="left").reset_index(drop=True)
    recs = optimize_price_batch(model, ctx, cfg=cfg, policy_profile="standard",
                                series_stats=ctx[STAT_COLUMNS].to_dict(orient="records"))
    d = pd.DataFrame([r.as_dict() for r in recs])
    cost_now = panel.cost_path[panel.cost_path["week"] == week][["upc", "store", "true_unit_cost"]]
    d = d.merge(cost_now, on=["upc", "store"], how="left")
    d["true_elasticity"] = d["upc"].map(truth)
    bounds = d["constraints"].map(lambda c: (c or {}).get("bounds"))
    d = d[bounds.notna() & d["predicted_units_current"].notna()].copy()
    d[["lo", "hi"]] = pd.DataFrame(bounds[d.index].tolist(), index=d.index)

    p0 = d["current_price"].to_numpy(float)
    c = d["true_unit_cost"].to_numpy(float)
    e = d["true_elasticity"].to_numpy(float)
    q0 = d["predicted_units_current"].to_numpy(float)
    p_engine = np.where(d["actionable"], d["final_recommended_price"], d["current_price"]).astype(float)
    p_star = np.clip(oracle_price(c, e), d["lo"].to_numpy(float), d["hi"].to_numpy(float))

    gp0, gp_eng, gp_star = (profit(p, c, e, p0, q0) for p in (p0, p_engine, p_star))
    attainable = gp_star.sum() - gp0.sum()
    moved = np.abs(p_engine - p0) > 1e-9
    agree = np.sign(p_engine - p0) == np.sign(p_star - p0)
    gp_model_now = d["expected_gross_profit_current"].sum()
    gp_model_new = np.where(d["actionable"], d["expected_gross_profit_recommended"],
                            d["expected_gross_profit_current"]).sum()

    return {
        "true_mean_elasticity": true_mean,
        "naive_bias": naive.price_elasticity - true_mean,
        "fe_bias": table.metadata["fe_elasticity"] - true_mean,
        "pooled_bias": table.pooled - true_mean,
        "products_usable_share": float(prods["usable_for_pricing"].mean()),
        "raw_product_bias": float(raw_err.mean()) if len(used) else np.nan,
        "raw_product_rmse": float(np.sqrt((raw_err**2).mean())) if len(used) else np.nan,
        "final_product_rmse": float(np.sqrt((fin_err**2).mean())),
        "raw_ci95_coverage": float(covered),
        "contexts": int(len(d)),
        "share_actionable": float(d["actionable"].mean()),
        "share_of_attainable_gain_captured": float((gp_eng.sum() - gp0.sum()) / attainable)
        if attainable > 0 else np.nan,
        "median_regret_vs_bounded_truth": float(np.median(1 - gp_eng / gp_star)),
        "direction_agreement_when_moved": float(agree[moved].mean()) if moved.any() else np.nan,
        "engine_self_scored_uplift": float(gp_model_new / gp_model_now - 1),
        "true_uplift_of_engine_prices": float(gp_eng.sum() / gp0.sum() - 1),
        "true_uplift_bounded_best": float(gp_star.sum() / gp0.sum() - 1),
    }


def make_figure(summary: pd.DataFrame, out: Path) -> None:
    import matplotlib.pyplot as plt

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 3.8))
    order = summary.index.tolist()
    y = np.arange(len(order))
    for off, col, label, colour in ((-0.27, "naive_bias", "naive (no controls)", "#718096"),
                                     (0.0, "fe_bias", "UPC x store FE", "#dd6b20"),
                                     (0.27, "pooled_bias", "pooled controlled (engine)", "#2b6cb0")):
        a1.barh(y + off, summary[col], height=0.26, color=colour, label=label)
    a1.axvline(0, color="black", lw=0.8)
    a1.set_yticks(y, order)
    a1.invert_yaxis()
    a1.set_xlabel("estimate minus true mean elasticity")
    a1.set_title("Elasticity bias by confounder")
    a1.legend(fontsize=7.5, loc="lower right")

    a2.barh(y - 0.2, 100 * summary["engine_self_scored_uplift"], height=0.38, color="#2b6cb0",
            label="engine's self-scored uplift (model-internal)")
    a2.barh(y + 0.2, 100 * summary["true_uplift_of_engine_prices"], height=0.38, color="#2f855a",
            label="true uplift of the same prices")
    a2.axvline(0, color="black", lw=0.8)
    a2.set_yticks(y, [""] * len(order))
    a2.invert_yaxis()
    a2.set_xlabel("portfolio gross-profit uplift (%)")
    a2.set_title("What the engine claims vs what the truth gives")
    a2.legend(fontsize=7.5, loc="lower right")
    fig.suptitle("Ground-truth recovery on synthetic panels (known elasticities)", fontsize=10)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)


def fmt(x, pct=False, signed=True):
    if x is None or not np.isfinite(x):
        return "-"
    return (f"{100 * x:+.1f}%" if signed else f"{100 * x:.1f}%") if pct else f"{x:+.2f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--quick", action="store_true", help="2 scenarios, 1 seed, smaller panels")
    parser.add_argument("--out-dir", default=None, help="Write outputs here instead of the repo.")
    args = parser.parse_args()

    cfg = load_config(REPO_ROOT / "configs" / "demo.yaml")
    names = list(QUICK) if args.quick else list(SCENARIOS)
    seeds = [101] if args.quick else [101 + i for i in range(args.seeds)]
    base = SyntheticSpec(n_upcs=12 if args.quick else 20, n_stores=10 if args.quick else 15,
                         n_weeks=160, multipack_share=0.0, bad_row_share=0.0, **CLEAN)

    rows = []
    t0 = time.time()
    warnings.filterwarnings("ignore")
    for name in names:
        for seed in seeds:
            spec = replace(base, seed=seed, **SCENARIOS[name])
            res = run_one(spec, cfg)
            rows.append({"scenario": name, "seed": seed, **res})
            print(f"{name:<24} seed {seed}: pooled bias {res['pooled_bias']:+.2f} | "
                  f"self-scored {100 * res['engine_self_scored_uplift']:+.1f}% vs true "
                  f"{100 * res['true_uplift_of_engine_prices']:+.1f}%", flush=True)
    runs = pd.DataFrame(rows)
    summary = runs.drop(columns="seed").groupby("scenario", sort=False).mean()
    spread = runs.drop(columns="seed").groupby("scenario", sort=False).std()

    out_root = Path(args.out_dir) if args.out_dir else REPO_ROOT
    metrics = out_root / "artifacts" / "metrics"
    payload = {
        "generated_at_utc": utc_now(),
        "label": "synthetic recovery study; not evidence about Dominick's",
        "scenarios": {k: v for k, v in SCENARIOS.items() if k in names},
        "seeds": seeds,
        "panel": {"n_upcs": base.n_upcs, "n_stores": base.n_stores, "n_weeks": base.n_weeks},
        "runtime_seconds": round(time.time() - t0, 1),
        "summary": summary.reset_index().to_dict("records"),
        "seed_std": spread.reset_index().to_dict("records"),
    }
    write_json(metrics / "ground_truth_study.json", payload)
    runs.to_csv(metrics / "ground_truth_study_runs.csv", index=False)
    fig_path = out_root / "artifacts" / "report_figures" / "fig_15_ground_truth_recovery.png"
    make_figure(summary, fig_path)

    s = summary
    est_rows = "\n".join(
        f"| {n} | {fmt(r.naive_bias)} | {fmt(r.fe_bias)} | {fmt(r.pooled_bias)} | "
        f"{fmt(r.raw_product_rmse, signed=False).lstrip('+')} | "
        f"{fmt(r.final_product_rmse, signed=False).lstrip('+')} | {fmt(r.raw_ci95_coverage, True, False)} |"
        for n, r in s.iterrows()
    )
    dec_rows = "\n".join(
        f"| {n} | {fmt(r.share_actionable, True, False)} | {fmt(r.direction_agreement_when_moved, True, False)} | "
        f"{fmt(r.share_of_attainable_gain_captured, True, False)} | {fmt(r.engine_self_scored_uplift, True)} | "
        f"{fmt(r.true_uplift_of_engine_prices, True)} | {fmt(r.true_uplift_bounded_best, True)} |"
        for n, r in s.iterrows()
    )
    clean = s.loc["clean"] if "clean" in s.index else None
    single = s.drop(index=[n for n in ("clean", "combined") if n in s.index])
    worst = (single if len(single) else s)["pooled_bias"].abs().idxmax()
    gap = s["engine_self_scored_uplift"] - s["true_uplift_of_engine_prices"]
    report = f"""# 25 - Ground-truth recovery study (synthetic, known elasticities)

Generated by `python scripts/ground_truth_study.py{' --quick' if args.quick else ''}` on
{payload['generated_at_utc'][:10]}: {len(names)} scenarios x {len(seeds)} seed(s), panels of
{base.n_upcs} products x {base.n_stores} stores x {base.n_weeks} weeks, runtime
{payload['runtime_seconds']:.0f}s.

> **What this is and is not.** The unchanged estimators and optimizer run on
> synthetic panels where the true elasticities and costs are known. It measures
> how much each confounding mechanism biases the engine *when that mechanism is
> the only problem*. It is not evidence about Dominick's. The synthetic demand
> also has the same constant-elasticity form the engine assumes, which
> flatters the engine. Real data can only be checked by an experiment.

## Answer first

* **With no confounding** the engine's pooled elasticity is off by
  {fmt(clean.pooled_bias) if clean is not None else '-'} and the naive estimate by
  {fmt(clean.naive_bias) if clean is not None else '-'}.
* **The most damaging single mechanism is "{worst}"**: pooled bias
  {fmt(s.loc[worst, 'pooled_bias'])}. The fixed effects and controls remove the
  confounders they can see (store pricing, recorded promotions, seasonality),
  but nothing removes **unrecorded promotions** or **pricing on demand shocks
  the retailer saw and the data did not**.
* **The engine's self-scored uplift vs the truth.** Across scenarios, the
  model-internal uplift minus the true uplift of the same prices ranges from
  {100 * gap.min():+.1f} to {100 * gap.max():+.1f} percentage points (positive = the engine
  overstates its own value, negative = it understates it). An elasticity
  biased steeper than the truth makes the engine too timid and too pessimistic
  at the same time. This is the size of the "circularity" caveat that every
  uplift figure in this repository carries, measured where the truth is
  known.

## Elasticity recovery

| scenario | naive bias | FE bias | pooled (engine) bias | per-product RMSE raw | after shrinkage | raw 95% CI coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
{est_rows}

Bias = estimate minus the observation-weighted true mean elasticity (negative
means the estimate is steeper than the truth). Coverage is the share of
usable products whose true elasticity lies inside raw +/- 1.96 x the
panel-robust standard error; 95% means the uncertainty is honestly stated.

## Decisions (last test week, standard policy)

| scenario | actionable | direction agrees with truth (when moved) | share of attainable gain captured | engine self-scored uplift | true uplift of engine prices | true uplift, best price within the same bounds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
{dec_rows}

"Best price within the same bounds" applies the true elasticity and true cost
inside the engine's own guardrail interval, so the comparison isolates the
estimation error from the policy.

## How to use this in the interview

"How do you know your elasticity is not biased?" On real data I don't, and the
repository says so. What I can show is how large the bias is under each
mechanism when the truth is known, which of them my design removes, and that
the engine's own uplift numbers miss the truth by a measurable margin in both directions.
That is why every dollar figure is a range and the next step is a randomised
pilot.

Per-seed results: `artifacts/metrics/ground_truth_study_runs.csv`.
Figure: `artifacts/report_figures/fig_15_ground_truth_recovery.png`.
"""
    out = out_root / "reports" / "25_GROUND_TRUTH_STUDY.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
