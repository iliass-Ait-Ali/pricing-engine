"""Phase D - descriptive and regression price-response analysis.

    python scripts/run_elasticity.py

Outputs
-------
    reports/03_ELASTICITY_ANALYSIS.md
    artifacts/metrics/elasticity.json
    artifacts/metrics/elasticity_by_upc.csv
    artifacts/figures/elasticity_*.png

Nothing here is causal. See docs/CAUSAL_LIMITATIONS.md.
"""

from __future__ import annotations

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
from pricing_engine.economics.elasticity import (  # noqa: E402
    elasticity_by_group,
    fit_loglog,
    pairwise_arc_elasticities,
)
from pricing_engine.utils.io import ensure_dir, write_json  # noqa: E402

plt.rcParams.update({"figure.dpi": 120, "figure.autolayout": True, "font.size": 9})

SAMPLE = 1_200_000


def main() -> int:
    cfg = load_config()
    fig_dir = ensure_dir(cfg.path("figures_dir"))
    seed = cfg.seed

    feats = pd.read_parquet(
        cfg.path("features_table"),
        columns=[
            "upc", "store", "week", "week_start_date", "move", "effective_unit_price",
            "recorded_promotion_flag", "week_of_year", "time_index", "descrip",
            "lag_price_1", "series_reference_price",
        ],
    )
    print(f"loaded {len(feats):,} rows")

    d = feats.copy()
    d["sin52"] = np.sin(2 * np.pi * d["week_of_year"] / 52.0)
    d["cos52"] = np.cos(2 * np.pi * d["week_of_year"] / 52.0)
    d["trend"] = d["time_index"].astype("float64") / 100.0

    results: dict = {}

    # -- 1. descriptive arc elasticity --------------------------------------
    print("computing week-over-week arc elasticities ...")
    arc = pairwise_arc_elasticities(d, ["upc", "store"])
    valid_arc = arc["arc_elasticity"].replace([np.inf, -np.inf], np.nan).dropna()
    trimmed = valid_arc[(valid_arc > valid_arc.quantile(0.01)) & (valid_arc < valid_arc.quantile(0.99))]
    results["arc"] = {
        "n_consecutive_week_pairs": int(len(arc)),
        "n_usable_pairs": int(len(valid_arc)),
        "pct_pairs_undefined": float(100.0 * (1 - len(valid_arc) / max(len(arc), 1))),
        "median": float(valid_arc.median()),
        "mean_trimmed_1_99": float(trimmed.mean()),
        "p25": float(valid_arc.quantile(0.25)),
        "p75": float(valid_arc.quantile(0.75)),
        "share_negative": float((valid_arc < 0).mean()),
        "share_below_minus_one": float((valid_arc < -1).mean()),
    }

    fig, ax = plt.subplots(figsize=(6, 3.2))
    ax.hist(valid_arc.clip(-15, 15), bins=100)
    ax.axvline(-1, color="crimson", ls="--", label="unit elastic")
    ax.set_title("Week-over-week arc elasticities (clipped to [-15, 15])")
    ax.legend()
    fig.savefig(fig_dir / "elasticity_arc_hist.png")
    plt.close(fig)

    # -- 2. log-log regressions ---------------------------------------------
    print("fitting log-log demand models ...")
    fits = []
    fits.append(fit_loglog(d, name="M1 naive pooled", sample_size=SAMPLE, seed=seed))
    fits.append(
        fit_loglog(d, name="M2 + UPC fixed effects", absorb=["upc"], sample_size=SAMPLE, seed=seed)
    )
    fits.append(
        fit_loglog(
            d, name="M3 + UPC x store fixed effects", absorb=["upc", "store"],
            sample_size=SAMPLE, seed=seed, cluster_col="upc",
        )
    )
    fits.append(
        fit_loglog(
            d,
            name="M4 + promo + seasonality + trend",
            absorb=["upc", "store"],
            controls=["recorded_promotion_flag", "sin52", "cos52", "trend"],
            sample_size=SAMPLE,
            seed=seed,
            cluster_col="upc",
        )
    )
    results["loglog"] = [f.as_dict() for f in fits]

    # -- 3. heterogeneity ----------------------------------------------------
    print("estimating per-UPC elasticities ...")
    by_upc = elasticity_by_group(d, ["upc"], min_obs=200, min_distinct_prices=10)
    by_upc = by_upc.merge(
        d.groupby("upc", observed=True).agg(descrip=("descrip", "first")).reset_index(), on="upc"
    )
    by_upc.to_csv(cfg.path("metrics_dir") / "elasticity_by_upc.csv", index=False)

    by_store = elasticity_by_group(d, ["store"], min_obs=500, min_distinct_prices=10)

    results["heterogeneity"] = {
        "n_upcs_estimated": int(len(by_upc)),
        "upc_elasticity_median": float(by_upc["elasticity"].median()),
        "upc_elasticity_p10": float(by_upc["elasticity"].quantile(0.10)),
        "upc_elasticity_p90": float(by_upc["elasticity"].quantile(0.90)),
        "share_upcs_negative": float((by_upc["elasticity"] < 0).mean()),
        "share_upcs_elastic": float((by_upc["elasticity"] < -1).mean()),
        "share_upcs_wrong_sign_significant": float(
            ((by_upc["elasticity"] > 0) & (by_upc["t_stat"] > 1.96)).mean()
        ),
        "n_stores_estimated": int(len(by_store)),
        "store_elasticity_median": float(by_store["elasticity"].median()),
        "store_elasticity_min": float(by_store["elasticity"].min()),
        "store_elasticity_max": float(by_store["elasticity"].max()),
    }

    fig, ax = plt.subplots(figsize=(6, 3.2))
    ax.hist(by_upc["elasticity"].clip(-8, 4), bins=60)
    ax.axvline(-1, color="crimson", ls="--", label="unit elastic")
    ax.axvline(0, color="black", lw=0.8)
    ax.set_title(f"Per-UPC log-log price elasticity (n={len(by_upc)})")
    ax.legend()
    fig.savefig(fig_dir / "elasticity_by_upc_hist.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 3.2))
    labels = [str(x)[:26] for x in by_upc.nlargest(12, "n_obs")["descrip"]]
    vals = by_upc.nlargest(12, "n_obs")["elasticity"]
    ax.barh(labels[::-1], vals.to_numpy()[::-1])
    ax.axvline(-1, color="crimson", ls="--")
    ax.set_title("Elasticity of the 12 most-observed UPCs")
    fig.savefig(fig_dir / "elasticity_top_upcs.png")
    plt.close(fig)

    # -- 4. promotion confound ----------------------------------------------
    promo_split = {}
    for label, sub in (("recorded promo", d[d["recorded_promotion_flag"] == 1]),
                       ("no recorded promo", d[d["recorded_promotion_flag"] == 0])):
        fit = fit_loglog(
            sub, name=label, absorb=["upc", "store"], sample_size=min(SAMPLE, len(sub)), seed=seed
        )
        promo_split[label] = fit.as_dict()
    results["promotion_split"] = promo_split

    write_json(cfg.path("metrics_dir") / "elasticity.json", results)

    # -- report --------------------------------------------------------------
    def fit_row(f):
        return (
            f"| {f['model']} | {f['price_elasticity']:.3f} | {f['std_error']:.4f} | "
            f"[{f['ci_low']:.3f}, {f['ci_high']:.3f}] | {f['r_squared']:.4f} | {f['n_obs']:,} | {f['controls']} |"
        )

    arc_r = results["arc"]
    het = results["heterogeneity"]
    top_elastic = by_upc.nsmallest(8, "elasticity")[["upc", "descrip", "elasticity", "n_obs"]]
    least_elastic = by_upc.nlargest(8, "elasticity")[["upc", "descrip", "elasticity", "n_obs"]]

    def table(df: pd.DataFrame) -> str:
        head = "| upc | description | elasticity | n_obs |"
        sep = "| --- | --- | --- | --- |"
        rows = [
            f"| {int(r.upc)} | {str(r.descrip)[:30]} | {r.elasticity:.3f} | {int(r.n_obs):,} |"
            for r in df.itertuples()
        ]
        return "\n".join([head, sep, *rows])

    report = f"""# 03 - Price-response / elasticity analysis

Generated by `python scripts/run_elasticity.py`.

> **These are observational price-response estimates, not causal effects.**
> Dominick's prices were chosen by the retailer in response to demand,
> competition, promotion calendars and costs. See
> `docs/CAUSAL_LIMITATIONS.md`.

## 1. Descriptive arc (midpoint) elasticity

For consecutive weeks of the same UPC x store series:

```text
E = ((Q2 - Q1) / ((Q1 + Q2)/2)) / ((P2 - P1) / ((P1 + P2)/2))
```

Undefined cases (equal prices, price changes below 0.01%, zero midpoints,
invalid quantities) return NaN rather than a fabricated number.

| metric | value |
| --- | --- |
| consecutive week pairs | {arc_r['n_consecutive_week_pairs']:,} |
| usable (defined) pairs | {arc_r['n_usable_pairs']:,} |
| pairs undefined (mostly: price did not change) | {arc_r['pct_pairs_undefined']:.1f}% |
| median arc elasticity | {arc_r['median']:.3f} |
| trimmed mean (1-99%) | {arc_r['mean_trimmed_1_99']:.3f} |
| inter-quartile range | [{arc_r['p25']:.2f}, {arc_r['p75']:.2f}] |
| share negative (price up -> units down) | {100*arc_r['share_negative']:.1f}% |
| share below -1 (elastic) | {100*arc_r['share_below_minus_one']:.1f}% |

![arc elasticity](../artifacts/figures/elasticity_arc_hist.png)

Pairwise elasticities are extremely dispersed. They mix price effects with
promotion, seasonality, stock-outs and feature/display activity, so they are
used here only as a descriptive sanity check - never as the pricing input.

## 2. Log-log demand regressions

Model: `log(units) = a + b * log(effective unit price) + controls`.
`b` is the estimated price elasticity. Zero-sales weeks: the canonical table
contains none after the documented validity filters, so no log(0) issue arises.
Fixed effects are absorbed by within-transformation (not dummy expansion), and
standard errors are heteroskedasticity-robust (HC1) or clustered by UPC where
noted.

| model | elasticity | std err | 95% CI | R2 (within) | n | controls |
| --- | --- | --- | --- | --- | --- | --- |
{chr(10).join(fit_row(f) for f in results['loglog'])}

**Reading this table is the whole point of the phase.** The naive pooled
coefficient mixes cross-product price levels (expensive premium cereals sell
differently from cheap bulk brands) with within-product price response. Adding
UPC and UPC x store fixed effects removes that composition and moves the
estimate materially. That movement is exactly the "correlation is not the
causal effect" story, made quantitative.

## 3. Heterogeneity across products and stores

| metric | value |
| --- | --- |
| UPCs with an estimated elasticity (>= 200 obs, >= 10 distinct prices) | {het['n_upcs_estimated']:,} |
| median per-UPC elasticity | {het['upc_elasticity_median']:.3f} |
| p10 / p90 | {het['upc_elasticity_p10']:.3f} / {het['upc_elasticity_p90']:.3f} |
| share of UPCs with negative elasticity | {100*het['share_upcs_negative']:.1f}% |
| share of UPCs elastic (< -1) | {100*het['share_upcs_elastic']:.1f}% |
| share of UPCs with a **significant positive** (wrong-sign) coefficient | {100*het['share_upcs_wrong_sign_significant']:.1f}% |
| stores estimated | {het['n_stores_estimated']} |
| store elasticity median (min .. max) | {het['store_elasticity_median']:.3f} ({het['store_elasticity_min']:.2f} .. {het['store_elasticity_max']:.2f}) |

![per-upc elasticity](../artifacts/figures/elasticity_by_upc_hist.png)
![top upcs](../artifacts/figures/elasticity_top_upcs.png)

Most price-elastic UPCs:

{table(top_elastic)}

Least price-elastic (or wrong-signed) UPCs:

{table(least_elastic)}

Wrong-signed products are not deleted. They are a finding: for those series the
observational association cannot support a price recommendation, which is why
the optimizer carries `INSUFFICIENT_PRICE_VARIATION` / `LOW_CONFIDENCE` reason
codes and a keep-current default.

## 4. The promotion confound, quantified

Same specification (UPC x store fixed effects) fitted separately on weeks with
and without a recorded promotion code:

| subsample | elasticity | std err | n |
| --- | --- | --- | --- |
| recorded promotion | {promo_split['recorded promo']['price_elasticity']:.3f} | {promo_split['recorded promo']['std_error']:.4f} | {promo_split['recorded promo']['n_obs']:,} |
| no recorded promotion | {promo_split['no recorded promo']['price_elasticity']:.3f} | {promo_split['no recorded promo']['std_error']:.4f} | {promo_split['no recorded promo']['n_obs']:,} |

Because promotion coding is incomplete, the "no recorded promotion" subsample
still contains unrecorded promotions, feature advertising and displays. The gap
between the two coefficients is therefore a **lower bound** on how much
promotion activity contaminates a pure price coefficient.

## 5. What this means for the engine

1. A single global elasticity is not a usable pricing input: the per-UPC spread
   ({het['upc_elasticity_p10']:.2f} to {het['upc_elasticity_p90']:.2f} between p10 and p90) is far too wide.
2. Fixed effects matter more than functional form. Any pricing claim built on
   the naive pooled coefficient would be indefensible.
3. Products with wrong-signed or unstable estimates must be excluded from
   recommendation, not "fixed" by clipping.
4. The ML demand model in Phase E has to earn its price response, which is why
   Phase F validates the model's price-response curves before any optimization.
"""
    out = cfg.path("reports_dir") / "03_ELASTICITY_ANALYSIS.md"
    out.write_text(report, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
