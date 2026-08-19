"""Phase M / Task 5 - robust inference for the elasticity estimates.

    python scripts/audit_elasticity_inference.py

Re-estimates the pooled, fixed-effects and per-UPC elasticities and reports the
coefficient under every defensible covariance assumption: conventional,
heteroskedasticity-robust, and cluster-robust at each level where the panel
actually has dependence.

Outputs
-------
    reports/15_ELASTICITY_INFERENCE_AUDIT.md
    artifacts/metrics/elasticity_inference.json
    artifacts/metrics/elasticity_per_upc_inference.csv
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
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from _phase_m import md_table  # noqa: E402

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.economics.elasticity import prepare_loglog_frame  # noqa: E402
from pricing_engine.economics.inference import (  # noqa: E402
    cluster_cov,
    demean,
    elasticity_inference,
    hc1_cov,
    ols_fit,
    std_errors,
)
from pricing_engine.features.build import training_frame  # noqa: E402
from pricing_engine.models.demand_model import load_model  # noqa: E402
from pricing_engine.utils.io import write_json  # noqa: E402

CONTROLS = ["recorded_promotion_flag", "sin52", "cos52", "trend"]


def add_controls(d: pd.DataFrame) -> pd.DataFrame:
    out = d.copy()
    woy = out["week_of_year"].astype("float64")
    out["sin52"] = np.sin(2 * np.pi * woy / 52.0)
    out["cos52"] = np.cos(2 * np.pi * woy / 52.0)
    out["trend"] = out["week"].astype("float64") / 100.0
    if "recorded_promotion_flag" not in out.columns:
        out["recorded_promotion_flag"] = 0.0
    return out


def run_spec(d: pd.DataFrame, *, controls: list[str], name: str) -> dict:
    """One pooled specification with UPC x store fixed effects absorbed."""
    cols = ["log_p"] + controls
    X = d[cols].to_numpy(dtype="float64")
    y = d["log_q"].to_numpy(dtype="float64")

    panel = pd.factorize(
        pd.Series(d["upc"].astype("int64").astype(str) + "_" + d["store"].astype("int64").astype(str))
    )[0]
    X = demean(X, panel)
    y = demean(y, panel)
    X = np.column_stack([np.ones(len(y)), X])

    clusters = {
        "UPC": pd.factorize(d["upc"])[0],
        "store": pd.factorize(d["store"])[0],
        "week": pd.factorize(d["week"])[0],
        "UPC x store panel": panel,
    }
    res = elasticity_inference(
        X, y,
        cluster_specs=clusters,
        two_way=("UPC", "week"),
        n_absorbed=int(panel.max()) + 1,
        coefficient_index=1,
    )
    res["name"] = name
    res["controls"] = ", ".join(controls) or "none"
    return res


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=1_200_000,
                        help="Subsample size for the pooled specifications (production uses 1.2M).")
    args = parser.parse_args()

    t0 = time.time()
    cfg = load_config()
    feats = pd.read_parquet(cfg.path("features_table"))
    usable = training_frame(feats)
    meta = load_model(cfg.path("models_dir") / "demand_model.joblib", cfg=cfg).metadata
    lo, hi = meta.train_weeks
    train = usable[(usable["week"] >= lo) & (usable["week"] <= hi)]
    print(f"training weeks {lo}-{hi}: {len(train):,} rows")

    d, note = prepare_loglog_frame(add_controls(train), zero_handling="drop")
    print(f"log-log frame: {len(d):,} rows ({note})")

    full = d
    sub = d.sample(args.sample, random_state=cfg.seed) if len(d) > args.sample else d

    specs = [
        run_spec(sub, controls=CONTROLS, name="A. pooled controlled (UPC x store FE + promo/season/trend), 1.2M subsample"),
        run_spec(sub, controls=[], name="B. UPC x store fixed effects only, 1.2M subsample"),
        run_spec(full, controls=CONTROLS, name="C. pooled controlled, FULL training sample"),
    ]

    # ---- per-UPC: HC1 vs clustered ------------------------------------------
    ecfg = cfg.require("elasticity")
    min_obs = int(ecfg["min_obs_per_upc"])
    min_prices = int(ecfg["min_distinct_prices_per_upc"])
    rows = []
    for upc, g in full.groupby("upc", observed=True):
        if len(g) < min_obs or g["effective_unit_price"].nunique() < min_prices:
            continue
        cols = ["log_p"] + CONTROLS
        X = g[cols].to_numpy(dtype="float64")
        y = g["log_q"].to_numpy(dtype="float64")
        store_codes = pd.factorize(g["store"])[0]
        n_stores = int(store_codes.max()) + 1
        if n_stores > 1:
            X = demean(X, store_codes)
            y = demean(y, store_codes)
        X = np.column_stack([np.ones(len(y)), X])
        try:
            fit = ols_fit(X, y, n_absorbed=max(n_stores - 1, 0))
        except Exception:  # noqa: BLE001 - a singular product is a finding
            continue
        beta = float(fit.beta[1])
        se_hc1 = float(std_errors(hc1_cov(fit))[1])
        week_codes = pd.factorize(g["week"])[0]
        try:
            se_store = float(std_errors(cluster_cov(fit, store_codes))[1]) if n_stores > 1 else np.nan
        except Exception:  # noqa: BLE001
            se_store = np.nan
        try:
            se_week = float(std_errors(cluster_cov(fit, week_codes))[1])
        except Exception:  # noqa: BLE001
            se_week = np.nan
        rows.append({
            "upc": int(upc),
            "n_obs": int(len(g)),
            "n_stores": n_stores,
            "n_weeks": int(week_codes.max()) + 1,
            "elasticity": beta,
            "se_hc1": se_hc1,
            "se_cluster_store": se_store,
            "se_cluster_week": se_week,
            "se_robust": float(np.nanmax([se_store, se_week])),
        })
    per_upc = pd.DataFrame(rows)
    per_upc["ratio_store"] = per_upc["se_cluster_store"] / per_upc["se_hc1"]
    per_upc["ratio_week"] = per_upc["se_cluster_week"] / per_upc["se_hc1"]
    per_upc["ratio_robust"] = per_upc["se_robust"] / per_upc["se_hc1"]
    out_csv = cfg.path("metrics_dir") / "elasticity_per_upc_inference.csv"
    per_upc.to_csv(out_csv, index=False)
    print(f"per-UPC inference: {len(per_upc)} products -> {out_csv}")

    summary = {
        "training_weeks": [int(lo), int(hi)],
        "n_training_rows": int(len(train)),
        "n_loglog_rows": int(len(d)),
        "pooled_specifications": specs,
        "per_upc": {
            "n_products": int(len(per_upc)),
            "median_se_hc1": float(per_upc["se_hc1"].median()),
            "median_se_cluster_store": float(per_upc["se_cluster_store"].median()),
            "median_se_cluster_week": float(per_upc["se_cluster_week"].median()),
            "median_ratio_store": float(per_upc["ratio_store"].median()),
            "median_ratio_week": float(per_upc["ratio_week"].median()),
            "median_ratio_robust": float(per_upc["ratio_robust"].median()),
            "p90_ratio_robust": float(per_upc["ratio_robust"].quantile(0.90)),
            "share_significant_hc1": float((per_upc["elasticity"].abs() / per_upc["se_hc1"] > 1.96).mean()),
            "share_significant_robust": float((per_upc["elasticity"].abs() / per_upc["se_robust"] > 1.96).mean()),
        },
        "runtime_seconds": round(time.time() - t0, 1),
    }
    write_json(cfg.path("metrics_dir") / "elasticity_inference.json", summary)

    # ---- report --------------------------------------------------------------
    spec_tables = []
    for spec in specs:
        rows_md = []
        for name, s in spec["specifications"].items():
            rows_md.append([
                name,
                f"{spec['coefficient']:.4f}",
                f"{s['std_error']:.4f}",
                f"{s['ratio_to_classical']:.2f}x",
                f"[{s['ci_low']:.3f}, {s['ci_high']:.3f}]",
                f"{s['t_stat']:.1f}",
                f"{s['n_clusters']:,}" if s["n_clusters"] else "-",
            ])
        spec_tables.append(
            f"### {spec['name']}\n\n"
            f"n = {spec['n_obs']:,} | absorbed FE groups = {spec['n_absorbed']:,} | "
            f"controls: {spec['controls']}\n\n"
            + md_table(rows_md,
                       ["Covariance", "Elasticity", "SE", "vs conventional", "95% CI", "t", "Clusters"],
                       ["---", "---:", "---:", "---:", "---:", "---:", "---:"])
        )

    pu = summary["per_upc"]
    a = specs[0]["specifications"]
    worst = max(a.items(), key=lambda kv: kv[1]["std_error"])

    report = f"""# 15. Robust inference for the elasticity estimates

**Question.** The reported elasticity standard errors assume a dependence
structure. Retail scanner panels do not have that structure. How much of the
reported precision survives?

**Data.** Training weeks {lo}-{hi} only ({len(train):,} rows,
{len(d):,} after dropping zero-sales weeks). Nothing here uses validation or
test outcomes.

## 1. Which dependence structures are present

| Source | Why it matters | Cluster level that absorbs it |
| --- | --- | --- |
| Store-level demand shocks persisting over weeks | a store's residuals are serially correlated | `store` |
| Product-level shocks hitting all stores at once | national advertising, competitor launches | `UPC` |
| Category-wide weekly shocks | holidays, weather, chain-wide promotion calendar | `week` |
| Series-level persistence | the same UPC in the same store, week after week | `UPC x store panel` |

A single conventional or HC1 standard error assumes **none** of these exist.

## 2. Pooled and fixed-effects specifications

{chr(10).join(spec_tables)}

The conventional standard error understates the sampling uncertainty of the
pooled elasticity by a factor of
**{worst[1]['ratio_to_classical']:.1f}x** at the widest defensible clustering
(`{worst[0]}`).

## 3. Per-UPC estimates

The per-UPC regressions absorb store fixed effects inside each product and are
reported in the production table with HC1 standard errors. Clustering them:

| Statistic | Value |
| --- | ---: |
| Products estimated | {pu['n_products']:,} |
| Median HC1 standard error | {pu['median_se_hc1']:.4f} |
| Median SE clustered by store | {pu['median_se_cluster_store']:.4f} |
| Median SE clustered by week | {pu['median_se_cluster_week']:.4f} |
| Median inflation, store clustering | {pu['median_ratio_store']:.2f}x |
| Median inflation, week clustering | {pu['median_ratio_week']:.2f}x |
| Median inflation, worst of the two | {pu['median_ratio_robust']:.2f}x |
| p90 inflation, worst of the two | {pu['p90_ratio_robust']:.2f}x |
| Share significant at 5% under HC1 | {100*pu['share_significant_hc1']:.1f}% |
| Share significant at 5% under the robust SE | {100*pu['share_significant_robust']:.1f}% |

## 4. Choice of estimator, and why

**Adopted for the production elasticity table: the larger of the store-clustered
and week-clustered standard error, per product.**

Reasoning:

* Two-way clustering by `(store, week)` is the textbook answer for this panel,
  but inside a *single product's* regression the number of week clusters
  (~{int(per_upc['n_weeks'].median())}) and store clusters
  (~{int(per_upc['n_stores'].median())}) is modest, and the
  Cameron-Gelbach-Miller subtraction is unstable at that size. Taking the max of
  the two one-way estimators is the conservative reading of the same evidence
  and never reports a smaller interval than either.
* At the pooled level the sample is large enough that two-way clustering is
  stable, so it is reported above and used to describe the category estimate.
* Clustering by `UPC x store panel` is *not* adopted for the per-UPC table: it
  is the finest of the candidate levels and would leave the week-level common
  shock uncontrolled.

**Consequence for shrinkage.** Larger `se_i` mechanically reduces the empirical
Bayes weight `w_i = tau^2 / (tau^2 + se_i^2)`, so the elasticity table shrinks
harder toward the category estimate. That is the correction described in
`reports/14_SHRINKAGE_AUDIT.md`, and it is the main reason the mean shrinkage
weight moved away from 0.95.

## 5. What this does and does not change

It does **not** make the elasticity causal. Wider intervals on an observational
price-response coefficient are a more honest observational coefficient. Price
endogeneity - the retailer setting prices in response to expected demand - is a
separate problem and is discussed in `docs/CAUSAL_LIMITATIONS.md`.

---

*Generated by `scripts/audit_elasticity_inference.py` in {summary['runtime_seconds']}s.*
"""
    out = cfg.path("reports_dir") / "15_ELASTICITY_INFERENCE_AUDIT.md"
    out.write_text(report, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
