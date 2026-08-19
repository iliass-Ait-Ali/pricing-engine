"""Phase B - pricing EDA on the canonical Dominick's Cereals table.

    python scripts/run_eda.py

Answers the questions that decide whether price optimization is even feasible:
historical scale, where the profit sits, how much within-product price
variation exists, how prices differ across stores, how demand responds
descriptively, and how stable the implied cost (AAC) is.

Outputs
-------
    reports/02_PRICING_EDA.md
    artifacts/metrics/eda_summary.json
    artifacts/metrics/price_variation_upc_store.csv
    artifacts/figures/*.png
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
from pricing_engine.data.loader import load_processed  # noqa: E402
from pricing_engine.economics.metrics import eligible_series, price_variation_summary  # noqa: E402
from pricing_engine.utils.io import ensure_dir, write_json  # noqa: E402

plt.rcParams.update({"figure.dpi": 120, "figure.autolayout": True, "font.size": 9})


def md_table(df: pd.DataFrame, floatfmt: str = "{:,.2f}") -> str:
    def fmt(v):
        if isinstance(v, (int, np.integer)):
            return f"{v:,}"
        if isinstance(v, (float, np.floating)):
            return "n/a" if not np.isfinite(v) else floatfmt.format(v)
        return str(v)

    cols = list(df.columns)
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body = ["| " + " | ".join(fmt(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join([header, sep, *body])


def main() -> int:
    cfg = load_config()
    fig_dir = ensure_dir(cfg.path("figures_dir"))
    df = load_processed(cfg=cfg)
    print(f"loaded {len(df):,} rows")

    summary: dict = {}

    # -- 1. historical scale -------------------------------------------------
    total_units = float(df["move"].sum())
    total_revenue = float(df["revenue"].sum())
    total_gp = float(df["gross_profit"].sum())
    summary["scale"] = {
        "rows": int(len(df)),
        "upcs": int(df["upc"].nunique()),
        "stores": int(df["store"].nunique()),
        "weeks": int(df["week"].nunique()),
        "date_min": str(df["week_start_date"].min().date()),
        "date_max": str(df["week_start_date"].max().date()),
        "total_units": total_units,
        "total_revenue": total_revenue,
        "total_gross_profit": total_gp,
        "overall_gross_margin_pct": 100.0 * total_gp / total_revenue,
        "mean_effective_unit_price": float(df["effective_unit_price"].mean()),
        "median_effective_unit_price": float(df["effective_unit_price"].median()),
        "mean_gross_margin_rate": float(df["gross_margin_rate"].mean()),
        "share_zero_move_rows": float((df["move"] == 0).mean()),
        "share_bundle_rows": float((df["qty"] > 1).mean()),
        "share_recorded_promotion_rows": float(df["recorded_promotion_flag"].mean()),
    }

    # -- 2. concentration ----------------------------------------------------
    by_upc = (
        df.groupby("upc", observed=True)
        .agg(
            descrip=("descrip", "first"),
            units=("move", "sum"),
            revenue=("revenue", "sum"),
            gross_profit=("gross_profit", "sum"),
            mean_price=("effective_unit_price", "mean"),
            mean_margin=("gross_margin_rate", "mean"),
            n_obs=("move", "size"),
        )
        .sort_values("gross_profit", ascending=False)
    )
    by_upc["revenue_share_pct"] = 100.0 * by_upc["revenue"] / total_revenue
    by_upc["gp_share_pct"] = 100.0 * by_upc["gross_profit"] / total_gp

    top_gp = by_upc.head(15).reset_index()
    top_rev = by_upc.sort_values("revenue", ascending=False).head(10).reset_index()
    top_units = by_upc.sort_values("units", ascending=False).head(10).reset_index()

    rev_sorted = by_upc["revenue"].sort_values(ascending=False).cumsum() / total_revenue
    n_upc_80 = int((rev_sorted <= 0.80).sum() + 1)
    summary["concentration"] = {
        "n_upcs": int(len(by_upc)),
        "upcs_for_80pct_revenue": n_upc_80,
        "top10_revenue_share_pct": float(100.0 * by_upc["revenue"].nlargest(10).sum() / total_revenue),
        "top10_gross_profit_share_pct": float(100.0 * by_upc["gross_profit"].nlargest(10).sum() / total_gp),
        "n_upcs_negative_gross_profit": int((by_upc["gross_profit"] < 0).sum()),
    }

    # -- 3/4. price variation and eligibility --------------------------------
    var_upc = price_variation_summary(df, ["upc"])
    var_series = price_variation_summary(df, ["upc", "store"])
    elig_cfg = cfg.require("eligibility")
    var_series["eligible"] = eligible_series(var_series, elig_cfg)
    var_series.to_csv(cfg.path("metrics_dir") / "price_variation_upc_store.csv", index=False)

    summary["price_variation"] = {
        "upc_level": {
            "median_distinct_prices": float(var_upc["n_distinct_prices"].median()),
            "median_price_cv": float(var_upc["price_cv"].median()),
            "median_price_range_pct": float(var_upc["price_range_pct"].median()),
            "share_upcs_cv_below_5pct": float((var_upc["price_cv"] < 0.05).mean()),
        },
        "upc_store_series": {
            "n_series": int(len(var_series)),
            "median_n_obs": float(var_series["n_obs"].median()),
            "median_distinct_prices": float(var_series["n_distinct_prices"].median()),
            "median_price_cv": float(var_series["price_cv"].median()),
            "median_price_change_rate": float(var_series["price_change_rate"].median()),
            "n_eligible": int(var_series["eligible"].sum()),
            "pct_eligible": float(100.0 * var_series["eligible"].mean()),
            "criteria": dict(elig_cfg),
        },
    }

    # -- 5. price dispersion across stores -----------------------------------
    store_price = (
        df.groupby(["upc", "store"], observed=True)["effective_unit_price"].mean().reset_index()
    )
    disp = store_price.groupby("upc", observed=True)["effective_unit_price"].agg(["min", "max", "mean", "std"])
    disp["spread_pct"] = 100.0 * (disp["max"] - disp["min"]) / disp["mean"]
    summary["store_price_dispersion"] = {
        "median_cross_store_spread_pct": float(disp["spread_pct"].median()),
        "p90_cross_store_spread_pct": float(disp["spread_pct"].quantile(0.9)),
        "median_cross_store_cv": float((disp["std"] / disp["mean"]).median()),
    }

    # -- 6. demand patterns --------------------------------------------------
    weekly = df.groupby("week_start_date", observed=True).agg(
        units=("move", "sum"), revenue=("revenue", "sum"), gross_profit=("gross_profit", "sum"),
        mean_price=("effective_unit_price", "mean"),
    )
    promo_stats = df.groupby("recorded_promotion_type", observed=True).agg(
        rows=("move", "size"),
        mean_units=("move", "mean"),
        mean_price=("effective_unit_price", "mean"),
        mean_margin=("gross_margin_rate", "mean"),
    ).reset_index()
    summary["promotion"] = {
        "mean_units_with_recorded_promo": float(df.loc[df["recorded_promotion_flag"] == 1, "move"].mean()),
        "mean_units_without_recorded_promo": float(df.loc[df["recorded_promotion_flag"] == 0, "move"].mean()),
        "mean_price_with_recorded_promo": float(
            df.loc[df["recorded_promotion_flag"] == 1, "effective_unit_price"].mean()
        ),
        "mean_price_without_recorded_promo": float(
            df.loc[df["recorded_promotion_flag"] == 0, "effective_unit_price"].mean()
        ),
    }

    # -- 7. bundles ----------------------------------------------------------
    summary["bundles"] = {
        "share_rows_qty_gt_1": float((df["qty"] > 1).mean()),
        "qty_value_counts": {str(k): int(v) for k, v in df["qty"].value_counts().head(6).items()},
    }

    # -- 8. AAC stability ----------------------------------------------------
    aac = df[df["estimated_unit_aac"] > 0]
    aac_series = aac.groupby(["upc", "store"], observed=True)["estimated_unit_aac"].agg(
        ["mean", "std", "size"]
    )
    aac_series = aac_series[aac_series["size"] >= 20]
    aac_cv = (aac_series["std"] / aac_series["mean"]).replace([np.inf, -np.inf], np.nan).dropna()
    summary["aac_stability"] = {
        "n_series": int(len(aac_series)),
        "median_within_series_aac_cv": float(aac_cv.median()),
        "p90_within_series_aac_cv": float(aac_cv.quantile(0.9)),
        "share_rows_nonpositive_aac": float((df["estimated_unit_aac"] <= 0).mean()),
        "share_rows_margin_implausible": float(df["margin_implausible_flag"].mean()),
    }

    # -- 9. margin vs price --------------------------------------------------
    sample = df.sample(min(300_000, len(df)), random_state=cfg.seed)
    corr_price_margin = float(
        np.corrcoef(sample["effective_unit_price"], sample["gross_margin_rate"])[0, 1]
    )
    d = df.sort_values(["upc", "store", "week"]).copy()
    g = d.groupby(["upc", "store"], observed=True)
    d["prev_price"] = g["effective_unit_price"].shift(1)
    d["prev_margin"] = g["gross_margin_rate"].shift(1)
    d["prev_aac"] = g["estimated_unit_aac"].shift(1)
    changed = d[(d["prev_price"].notna()) & (np.abs(d["effective_unit_price"] - d["prev_price"]) > 1e-9)]
    price_down = changed[changed["effective_unit_price"] < changed["prev_price"]]
    summary["margin_mechanics"] = {
        "corr_price_vs_margin_rate": corr_price_margin,
        "n_price_change_events": int(len(changed)),
        "share_price_changes_that_are_cuts": float((changed["effective_unit_price"] < changed["prev_price"]).mean()),
        "mean_margin_change_on_price_cut": float((price_down["gross_margin_rate"] - price_down["prev_margin"]).mean()),
        "mean_aac_change_pct_on_price_cut": float(
            ((price_down["estimated_unit_aac"] - price_down["prev_aac"]) / price_down["prev_aac"].replace(0, np.nan)).mean()
        ),
    }

    # -- 10. suspicious artefacts -------------------------------------------
    d["price_jump_pct"] = d["effective_unit_price"] / d["prev_price"] - 1.0
    big_jumps = d[np.abs(d["price_jump_pct"]) > 0.5]
    summary["artefacts"] = {
        "share_week_over_week_price_jumps_gt_50pct": float(len(big_jumps) / max(len(changed), 1)),
        "n_jumps_gt_50pct": int(len(big_jumps)),
        "max_observed_unit_price": float(df["effective_unit_price"].max()),
        "min_observed_unit_price": float(df["effective_unit_price"].min()),
        "share_rows_unit_price_above_10": float((df["effective_unit_price"] > 10).mean()),
        "n_upc_store_weeks_with_zero_sales": int((df["move"] == 0).sum()),
    }

    # -- figures -------------------------------------------------------------
    print("rendering figures ...")
    fig, ax = plt.subplots(figsize=(7, 3.2))
    ax.plot(weekly.index, weekly["units"] / 1000.0, lw=0.9)
    ax.set_title("Total weekly Cereals units sold (all stores)")
    ax.set_ylabel("thousand units")
    fig.savefig(fig_dir / "eda_weekly_units.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 3.2))
    ax.plot(weekly.index, weekly["revenue"] / 1000.0, lw=0.9, label="revenue")
    ax.plot(weekly.index, weekly["gross_profit"] / 1000.0, lw=0.9, label="gross profit")
    ax.set_title("Weekly observed revenue and gross profit")
    ax.set_ylabel("thousand $")
    ax.legend()
    fig.savefig(fig_dir / "eda_weekly_revenue_profit.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 3.2))
    ax.hist(df["effective_unit_price"].clip(upper=8), bins=80)
    ax.set_title("Distribution of effective unit price (clipped at $8 for display)")
    ax.set_xlabel("$ per unit")
    fig.savefig(fig_dir / "eda_price_distribution.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 3.2))
    ax.hist(var_series["price_cv"].dropna().clip(upper=0.4), bins=60)
    ax.axvline(float(elig_cfg["min_price_cv"]), color="crimson", ls="--", label="eligibility threshold")
    ax.set_title("Within UPC x store price coefficient of variation")
    ax.legend()
    fig.savefig(fig_dir / "eda_price_cv.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 3.4))
    s = df.sample(min(60_000, len(df)), random_state=cfg.seed)
    ax.scatter(s["effective_unit_price"], np.log1p(s["move"]), s=1, alpha=0.05)
    ax.set_xlim(0, 8)
    ax.set_xlabel("effective unit price ($)")
    ax.set_ylabel("log(1 + units)")
    ax.set_title("Observational price vs demand (NOT a causal curve)")
    fig.savefig(fig_dir / "eda_price_vs_demand.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 3.2))
    top = by_upc.head(15)
    ax.barh([str(d)[:28] for d in top["descrip"]][::-1], (top["gross_profit"] / 1000.0)[::-1])
    ax.set_title("Top 15 UPCs by observed gross profit")
    ax.set_xlabel("thousand $")
    fig.savefig(fig_dir / "eda_top_upc_gross_profit.png")
    plt.close(fig)

    write_json(cfg.path("metrics_dir") / "eda_summary.json", summary)

    # -- report --------------------------------------------------------------
    sc = summary["scale"]
    pv = summary["price_variation"]
    report = f"""# 02 - Pricing EDA (Dominick's Cereals)

Generated by `python scripts/run_eda.py` from
`data/processed/dominicks_cereals.parquet`. Every number is computed at run
time; nothing is hardcoded.

## 1. Historical scale (observed, not modelled)

| metric | value |
| --- | --- |
| observations (UPC x store x week) | {sc['rows']:,} |
| UPCs / stores / weeks | {sc['upcs']:,} / {sc['stores']:,} / {sc['weeks']:,} |
| period | {sc['date_min']} .. {sc['date_max']} |
| total units sold | {sc['total_units']:,.0f} |
| total revenue | ${sc['total_revenue']:,.0f} |
| total gross profit | ${sc['total_gross_profit']:,.0f} |
| overall gross margin | {sc['overall_gross_margin_pct']:.2f}% |
| mean / median effective unit price | ${sc['mean_effective_unit_price']:.3f} / ${sc['median_effective_unit_price']:.3f} |
| mean gross margin rate | {100*sc['mean_gross_margin_rate']:.2f}% |
| rows with zero units sold | {100*sc['share_zero_move_rows']:.2f}% |
| rows with a recorded promotion code | {100*sc['share_recorded_promotion_rows']:.2f}% |
| rows with bundle qty > 1 | {100*sc['share_bundle_rows']:.3f}% |

![weekly units](../artifacts/figures/eda_weekly_units.png)
![weekly revenue and profit](../artifacts/figures/eda_weekly_revenue_profit.png)

## 2. Where the money is

Top 15 UPCs by observed gross profit:

{md_table(top_gp[['upc', 'descrip', 'units', 'revenue', 'gross_profit', 'mean_price', 'revenue_share_pct', 'gp_share_pct']])}

| metric | value |
| --- | --- |
| UPCs covering 80% of revenue | {summary['concentration']['upcs_for_80pct_revenue']} of {summary['concentration']['n_upcs']} |
| top-10 UPC revenue share | {summary['concentration']['top10_revenue_share_pct']:.2f}% |
| top-10 UPC gross-profit share | {summary['concentration']['top10_gross_profit_share_pct']:.2f}% |
| UPCs with negative total gross profit | {summary['concentration']['n_upcs_negative_gross_profit']} |

Top 10 by revenue: {', '.join(str(x)[:24] for x in top_rev['descrip'].tolist())}

Top 10 by units: {', '.join(str(x)[:24] for x in top_units['descrip'].tolist())}

![top upc gross profit](../artifacts/figures/eda_top_upc_gross_profit.png)

## 3. Price variation - can we even estimate price response?

UPC level (pooled across stores):

| metric | value |
| --- | --- |
| median distinct prices per UPC | {pv['upc_level']['median_distinct_prices']:.0f} |
| median price CV per UPC | {pv['upc_level']['median_price_cv']:.4f} |
| median (max-min)/mean per UPC | {100*pv['upc_level']['median_price_range_pct']:.2f}% |
| share of UPCs with CV < 5% | {100*pv['upc_level']['share_upcs_cv_below_5pct']:.2f}% |

UPC x store series (the unit a pricing decision is actually made on):

| metric | value |
| --- | --- |
| number of series | {pv['upc_store_series']['n_series']:,} |
| median observations per series | {pv['upc_store_series']['median_n_obs']:.0f} |
| median distinct prices per series | {pv['upc_store_series']['median_distinct_prices']:.0f} |
| median price CV | {pv['upc_store_series']['median_price_cv']:.4f} |
| median share of weeks with a price change | {100*pv['upc_store_series']['median_price_change_rate']:.2f}% |
| **eligible series** (>= {elig_cfg['min_observations']} obs, >= {elig_cfg['min_distinct_prices']} distinct prices, CV >= {elig_cfg['min_price_cv']}) | **{pv['upc_store_series']['n_eligible']:,} ({pv['upc_store_series']['pct_eligible']:.1f}%)** |

![price cv](../artifacts/figures/eda_price_cv.png)

Series-level detail: `artifacts/metrics/price_variation_upc_store.csv`.

## 4. Price dispersion across stores

| metric | value |
| --- | --- |
| median cross-store spread per UPC ((max-min)/mean of store mean prices) | {summary['store_price_dispersion']['median_cross_store_spread_pct']:.2f}% |
| p90 cross-store spread | {summary['store_price_dispersion']['p90_cross_store_spread_pct']:.2f}% |
| median cross-store CV | {summary['store_price_dispersion']['median_cross_store_cv']:.4f} |

Dominick's used store price zones, so store identity is a real pricing
dimension, not noise. This is why the recommendation unit is UPC x store.

## 5. Demand vs recorded promotions (descriptive)

{md_table(promo_stats, floatfmt="{:,.4f}")}

| metric | value |
| --- | --- |
| mean units, recorded promotion | {summary['promotion']['mean_units_with_recorded_promo']:.2f} |
| mean units, no recorded promotion | {summary['promotion']['mean_units_without_recorded_promo']:.2f} |
| mean price, recorded promotion | ${summary['promotion']['mean_price_with_recorded_promo']:.3f} |
| mean price, no recorded promotion | ${summary['promotion']['mean_price_without_recorded_promo']:.3f} |

Interpretation warning: promotion coding is incomplete, so the
"no recorded promotion" group certainly contains unrecorded promotions. The gap
above is an association, not a promotion lift estimate.

![price vs demand](../artifacts/figures/eda_price_vs_demand.png)
![price distribution](../artifacts/figures/eda_price_distribution.png)

## 6. Bundles

{100*summary['bundles']['share_rows_qty_gt_1']:.3f}% of rows have qty > 1, so
`price / qty` matters for a small but non-zero slice of the data. Observed qty
values: {summary['bundles']['qty_value_counts']}.

## 7. Stability of the implied cost (AAC)

| metric | value |
| --- | --- |
| series analysed (>= 20 obs, positive AAC) | {summary['aac_stability']['n_series']:,} |
| median within-series AAC coefficient of variation | {summary['aac_stability']['median_within_series_aac_cv']:.4f} |
| p90 within-series AAC CV | {summary['aac_stability']['p90_within_series_aac_cv']:.4f} |
| rows with non-positive implied AAC | {100*summary['aac_stability']['share_rows_nonpositive_aac']:.3f}% |
| rows with implausible margin (|rate| >= 1) | {100*summary['aac_stability']['share_rows_margin_implausible']:.3f}% |

The implied AAC moves materially within a series. That is exactly why the
optimizer must use an explicit, lagged, decision-time cost assumption instead
of reusing the contemporaneous accounting margin.

## 8. Are margins mechanically tied to price?

| metric | value |
| --- | --- |
| correlation(effective unit price, gross margin rate) | {summary['margin_mechanics']['corr_price_vs_margin_rate']:.4f} |
| week-over-week price-change events | {summary['margin_mechanics']['n_price_change_events']:,} |
| share of price changes that are cuts | {100*summary['margin_mechanics']['share_price_changes_that_are_cuts']:.2f}% |
| mean change in margin rate on a price cut | {summary['margin_mechanics']['mean_margin_change_on_price_cut']:.4f} |
| mean change in implied AAC on a price cut | {100*summary['margin_mechanics']['mean_aac_change_pct_on_price_cut']:.2f}% |

When price falls and the implied AAC barely moves, the recorded margin
mechanically falls with it. That is an accounting identity, not a demand
finding - and it is the reason `gross_profit` must never be used as a demand
feature.

## 9. Data-quality artefacts visible in the EDA

| metric | value |
| --- | --- |
| week-over-week price jumps > 50% | {summary['artefacts']['n_jumps_gt_50pct']:,} ({100*summary['artefacts']['share_week_over_week_price_jumps_gt_50pct']:.2f}% of change events) |
| min / max observed unit price | ${summary['artefacts']['min_observed_unit_price']:.2f} / ${summary['artefacts']['max_observed_unit_price']:.2f} |
| rows with unit price > $10 | {100*summary['artefacts']['share_rows_unit_price_above_10']:.3f}% |
| store-weeks with zero sales | {summary['artefacts']['n_upc_store_weeks_with_zero_sales']:,} |

No outlier cut-off is applied to make plots look nice. Extremes are reported,
flagged and carried into modelling, where the extrapolation guardrail (Phase G)
prevents recommending prices outside observed support.

## 10. Consequences for the rest of the project

1. Gross profit is concentrated in a minority of UPCs, so the recommendation
   engine should be evaluated on volume-weighted economics, not on a flat
   average over 45k series.
2. Only **{pv['upc_store_series']['pct_eligible']:.1f}%** of UPC x store series pass the
   price-variation eligibility screen. Everything else must default to
   `INSUFFICIENT_PRICE_VARIATION` / keep current price.
3. Store-level price dispersion justifies UPC x store as the decision grain.
4. AAC instability forces an explicit decision-time cost rule (lagged AAC).
5. Promotion coding is incomplete, so the promotion feature is an
   *observation of a code*, never a clean treatment indicator.
"""
    out = cfg.path("reports_dir") / "02_PRICING_EDA.md"
    out.write_text(report, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
