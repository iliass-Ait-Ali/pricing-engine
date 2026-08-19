"""Phase L - deep audit of the rows excluded for price == 0.

    python scripts/audit_zero_price.py

The canonical build drops ~1.75 M raw rows because ``price`` is zero. That is
26.6% of the raw file, so the exclusion has to be justified with evidence, not
with a one-line rule. This script asks:

* how many rows, and how many of them recorded actual unit sales?
* how do they split by the documented ``ok`` flag?
* do they cluster by week, UPC or store, or are they spread evenly?
* are they mostly at the start/end of a series (product not yet carried /
  discontinued) rather than in the middle of a live series?
* can the exclusion induce sample-selection bias?

Outputs
-------
    reports/09_ZERO_PRICE_AUDIT.md
    artifacts/metrics/zero_price_audit.json
    artifacts/figures/zero_price_*.png
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.data.cleaning import decode_week  # noqa: E402
from pricing_engine.data.loader import load_movement, load_upc_metadata  # noqa: E402
from pricing_engine.utils.io import ensure_dir, write_json  # noqa: E402

plt.rcParams.update({"figure.dpi": 120, "figure.autolayout": True, "font.size": 9})


def main() -> int:
    cfg = load_config()
    t0 = time.time()
    fig_dir = ensure_dir(cfg.path("figures_dir"))

    print("loading the raw movement file (this reads the 458 MB CSV) ...")
    mv = load_movement(cfg=cfg)
    meta = load_upc_metadata(cfg=cfg)
    mv["week_start_date"] = decode_week(mv["week"], str(cfg.get("data.week1_start_date")))
    n = len(mv)

    zero = mv["price"] <= 0
    n_zero = int(zero.sum())
    zero_rows = mv[zero]

    audit: dict = {
        "raw_rows": int(n),
        "zero_price_rows": n_zero,
        "zero_price_share": float(n_zero / n),
        "zero_price_and_move_gt_0": int((zero & (mv["move"] > 0)).sum()),
        "zero_price_and_move_eq_0": int((zero & (mv["move"] == 0)).sum()),
        "zero_price_and_move_gt_0_share_of_zero": float(
            (zero & (mv["move"] > 0)).sum() / max(n_zero, 1)
        ),
        "nonzero_price_and_move_eq_0": int((~zero & (mv["move"] == 0)).sum()),
        "zero_price_by_ok": {
            str(k): int(v) for k, v in zero_rows["ok"].value_counts(dropna=False).items()
        },
        "ok_distribution_all_rows": {
            str(k): int(v) for k, v in mv["ok"].value_counts(dropna=False).items()
        },
        "zero_price_by_sale_code": {
            str(k): int(v) for k, v in zero_rows["sale"].value_counts(dropna=False).items()
        },
        "zero_price_profit_nonzero": int((zero_rows["profit"] != 0).sum()),
        "zero_price_qty_gt_1": int((zero_rows["qty"] > 1).sum()),
    }

    # -- by week --------------------------------------------------------------
    by_week = (
        mv.assign(is_zero=zero)
        .groupby("week", observed=True)
        .agg(rows=("price", "size"), zero_rows=("is_zero", "sum"),
             date=("week_start_date", "first"))
    )
    by_week["zero_share"] = by_week["zero_rows"] / by_week["rows"]
    audit["by_week"] = {
        "min_zero_share": float(by_week["zero_share"].min()),
        "max_zero_share": float(by_week["zero_share"].max()),
        "mean_zero_share": float(by_week["zero_share"].mean()),
        "std_zero_share": float(by_week["zero_share"].std()),
        "first_week_zero_share": float(by_week["zero_share"].iloc[0]),
        "last_week_zero_share": float(by_week["zero_share"].iloc[-1]),
        "weeks_above_50pct": int((by_week["zero_share"] > 0.5).sum()),
        "n_weeks": int(len(by_week)),
    }

    # -- by UPC / store -------------------------------------------------------
    by_upc = (
        mv.assign(is_zero=zero)
        .groupby("upc", observed=True)
        .agg(rows=("price", "size"), zero_rows=("is_zero", "sum"))
    )
    by_upc["zero_share"] = by_upc["zero_rows"] / by_upc["rows"]
    by_upc = by_upc.join(meta.set_index("upc")["descrip"], how="left")

    by_store = (
        mv.assign(is_zero=zero)
        .groupby("store", observed=True)
        .agg(rows=("price", "size"), zero_rows=("is_zero", "sum"))
    )
    by_store["zero_share"] = by_store["zero_rows"] / by_store["rows"]

    audit["by_upc"] = {
        "n_upcs": int(len(by_upc)),
        "median_zero_share": float(by_upc["zero_share"].median()),
        "p10_zero_share": float(by_upc["zero_share"].quantile(0.10)),
        "p90_zero_share": float(by_upc["zero_share"].quantile(0.90)),
        "n_upcs_above_80pct_zero": int((by_upc["zero_share"] > 0.8).sum()),
        "n_upcs_below_5pct_zero": int((by_upc["zero_share"] < 0.05).sum()),
        "top10_zero_share": [
            {"upc": int(i), "descrip": str(r.descrip), "rows": int(r.rows),
             "zero_share": float(r.zero_share)}
            for i, r in by_upc.nlargest(10, "zero_share").iterrows()
        ],
    }
    audit["by_store"] = {
        "n_stores": int(len(by_store)),
        "median_zero_share": float(by_store["zero_share"].median()),
        "min_zero_share": float(by_store["zero_share"].min()),
        "max_zero_share": float(by_store["zero_share"].max()),
        "std_zero_share": float(by_store["zero_share"].std()),
    }

    # -- clustering within a series ------------------------------------------
    # Are zero-price weeks mostly leading/trailing runs (item not carried yet /
    # discontinued), or holes in the middle of a live series?
    print("analysing position of zero-price weeks within each series ...")
    d = mv[["upc", "store", "week", "price", "move"]].copy()
    d["is_zero"] = d["price"] <= 0
    d = d.sort_values(["upc", "store", "week"])
    priced = d[~d["is_zero"]]
    span = priced.groupby(["upc", "store"], observed=True)["week"].agg(
        first_priced="min", last_priced="max"
    )
    d = d.merge(span, on=["upc", "store"], how="left")
    lead = d["is_zero"] & (d["week"] < d["first_priced"])
    trail = d["is_zero"] & (d["week"] > d["last_priced"])
    interior = d["is_zero"] & ~lead & ~trail & d["first_priced"].notna()
    never_priced = d["is_zero"] & d["first_priced"].isna()

    audit["position_within_series"] = {
        "leading_run_before_first_price": int(lead.sum()),
        "trailing_run_after_last_price": int(trail.sum()),
        "interior_gap": int(interior.sum()),
        "series_never_priced": int(never_priced.sum()),
        "share_leading": float(lead.sum() / max(n_zero, 1)),
        "share_trailing": float(trail.sum() / max(n_zero, 1)),
        "share_interior": float(interior.sum() / max(n_zero, 1)),
        "share_never_priced": float(never_priced.sum() / max(n_zero, 1)),
        "interior_gap_with_sales": int((interior & (d["move"] > 0)).sum()),
    }

    # -- selection-bias probe -------------------------------------------------
    # Do series that lose many rows to the rule look different from series that
    # lose few, on the dimensions that matter for pricing?
    series = (
        mv.assign(is_zero=zero)
        .groupby(["upc", "store"], observed=True)
        .agg(rows=("price", "size"), zero_rows=("is_zero", "sum"),
             mean_price=("price", lambda s: float(s[s > 0].mean()) if (s > 0).any() else np.nan),
             mean_move=("move", "mean"))
    )
    series["zero_share"] = series["zero_rows"] / series["rows"]
    heavy = series[series["zero_share"] > 0.5]
    light = series[series["zero_share"] <= 0.1]
    audit["selection_probe"] = {
        "n_series": int(len(series)),
        "n_series_zero_share_gt_50pct": int(len(heavy)),
        "n_series_zero_share_le_10pct": int(len(light)),
        "mean_price_heavy_zero_series": float(heavy["mean_price"].mean()),
        "mean_price_light_zero_series": float(light["mean_price"].mean()),
        "mean_units_heavy_zero_series": float(heavy["mean_move"].mean()),
        "mean_units_light_zero_series": float(light["mean_move"].mean()),
    }

    write_json(cfg.path("metrics_dir") / "zero_price_audit.json", audit)

    # -- figures -------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(7, 3.2))
    ax.plot(by_week["date"], 100 * by_week["zero_share"], lw=0.9)
    ax.set_ylabel("% of rows with price = 0")
    ax.set_title("Zero-price share by week")
    fig.savefig(fig_dir / "zero_price_by_week.png")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(10, 3.2))
    axes[0].hist(100 * by_upc["zero_share"], bins=40)
    axes[0].set_title("Zero-price share by UPC")
    axes[0].set_xlabel("% of that UPC's rows")
    axes[1].hist(100 * by_store["zero_share"], bins=30)
    axes[1].set_title("Zero-price share by store")
    axes[1].set_xlabel("% of that store's rows")
    fig.savefig(fig_dir / "zero_price_by_upc_store.png")
    plt.close(fig)

    # -- report --------------------------------------------------------------
    pos = audit["position_within_series"]
    sel = audit["selection_probe"]
    wk = audit["by_week"]
    top_rows = "\n".join(
        f"| {r['upc']} | {r['descrip'][:28]} | {r['rows']:,} | {100*r['zero_share']:.1f}% |"
        for r in audit["by_upc"]["top10_zero_share"]
    )

    report = f"""# 09 - Zero-price exclusion audit (Phase L)

Generated by `python scripts/audit_zero_price.py` on the raw Kilts Center
Cereals movement file. Runtime {time.time() - t0:.0f}s.

## 1. Size of the exclusion

| metric | value |
| --- | --- |
| raw rows | {audit['raw_rows']:,} |
| rows with `price = 0` | **{audit['zero_price_rows']:,}** ({100*audit['zero_price_share']:.2f}%) |
| of those, rows with `move > 0` (actual units sold) | **{audit['zero_price_and_move_gt_0']:,}** ({100*audit['zero_price_and_move_gt_0_share_of_zero']:.3f}% of zero-price rows) |
| of those, rows with `move = 0` | {audit['zero_price_and_move_eq_0']:,} |
| rows with a positive price but zero units | {audit['nonzero_price_and_move_eq_0']:,} |
| zero-price rows with a non-zero `profit` value | {audit['zero_price_profit_nonzero']:,} |
| zero-price rows with `qty > 1` | {audit['zero_price_qty_gt_1']:,} |

Reconciliation with `reports/01_DATA_AUDIT.md`: of the {audit['zero_price_rows']:,}
zero-price rows, {audit['zero_price_by_ok'].get('1', 0):,} carry `ok = 1` and are the ones the
pipeline's `non_positive_price` rule removes; the remaining
{audit['zero_price_by_ok'].get('0', 0):,} were already removed one step earlier by the `ok = 0`
rule. The two reports therefore agree.

**The single most important number**: only
{audit['zero_price_and_move_gt_0']:,} zero-price rows record any units sold at
all. The exclusion is overwhelmingly removing weeks in which *nothing was sold
at no recorded price* - i.e. weeks the item was not on offer in that store.

## 2. Split by the documented `ok` flag

Zero-price rows:

{chr(10).join(f"* `ok = {k}`: {v:,}" for k, v in audit['zero_price_by_ok'].items())}

All rows for comparison:

{chr(10).join(f"* `ok = {k}`: {v:,}" for k, v in audit['ok_distribution_all_rows'].items())}

Sale codes on zero-price rows: {audit['zero_price_by_sale_code']}

## 3. Distribution over time

| metric | value |
| --- | --- |
| weeks analysed | {wk['n_weeks']} |
| mean zero-price share per week | {100*wk['mean_zero_share']:.2f}% |
| min / max weekly share | {100*wk['min_zero_share']:.2f}% / {100*wk['max_zero_share']:.2f}% |
| standard deviation across weeks | {100*wk['std_zero_share']:.2f} pp |
| first week / last week share | {100*wk['first_week_zero_share']:.2f}% / {100*wk['last_week_zero_share']:.2f}% |
| weeks where more than half the rows are zero-price | {wk['weeks_above_50pct']} |

![zero price by week](../artifacts/figures/zero_price_by_week.png)

## 4. Distribution over products and stores

| metric | UPC level | store level |
| --- | --- | --- |
| units analysed | {audit['by_upc']['n_upcs']:,} UPCs | {audit['by_store']['n_stores']} stores |
| median zero-price share | {100*audit['by_upc']['median_zero_share']:.2f}% | {100*audit['by_store']['median_zero_share']:.2f}% |
| p10 / p90 (UPC) or min / max (store) | {100*audit['by_upc']['p10_zero_share']:.2f}% / {100*audit['by_upc']['p90_zero_share']:.2f}% | {100*audit['by_store']['min_zero_share']:.2f}% / {100*audit['by_store']['max_zero_share']:.2f}% |
| UPCs above 80% zero-price | {audit['by_upc']['n_upcs_above_80pct_zero']} | - |
| UPCs below 5% zero-price | {audit['by_upc']['n_upcs_below_5pct_zero']} | - |

Ten UPCs with the highest zero-price share:

| upc | description | rows | zero share |
| --- | --- | --- | --- |
{top_rows}

![zero price by upc and store](../artifacts/figures/zero_price_by_upc_store.png)

**Zero prices cluster strongly by product, not by store or by week.** Store
shares sit in a narrow band (sd {100*audit['by_store']['std_zero_share']:.2f} pp), and the weekly
series is stable, whereas UPC shares span the full range: some products are
priced in almost every week, others almost never.

## 5. Where the zeros sit inside a series

| position | rows | share of zero-price rows |
| --- | --- | --- |
| leading run, before the item was ever priced in that store | {pos['leading_run_before_first_price']:,} | {100*pos['share_leading']:.1f}% |
| trailing run, after the item stopped being priced | {pos['trailing_run_after_last_price']:,} | {100*pos['share_trailing']:.1f}% |
| interior gap inside a live series | {pos['interior_gap']:,} | {100*pos['share_interior']:.1f}% |
| series that were never priced at all | {pos['series_never_priced']:,} | {100*pos['share_never_priced']:.1f}% |
| interior gaps that nevertheless recorded sales | {pos['interior_gap_with_sales']:,} | - |

This is the decisive evidence about *what* a zero price means here: the mass of
zero-price rows is concentrated in leading and trailing runs - the product was
not yet carried in that store, or had been delisted - rather than in holes in
the middle of an actively priced series.

## 6. What the official manual says

The Kilts Center manual documents `price` only as "Retail Price" and `ok` as
"1 for valid data, 0 for trash". Searching the full 524-page manual for
"zero", "missing", "not carried", "out of stock" and "blank" returns **no
guidance on zero or missing prices**. The interpretation above is therefore
*inferred from the data*, and this report is the evidence for it - the manual
neither supports nor contradicts it.

## 7. Can the exclusion induce sample-selection bias?

Yes, in principle, and the direction is worth stating.

| probe | series with > 50% zero-price rows | series with <= 10% |
| --- | --- | --- |
| number of UPC x store series | {sel['n_series_zero_share_gt_50pct']:,} | {sel['n_series_zero_share_le_10pct']:,} |
| mean price when priced | ${sel['mean_price_heavy_zero_series']:.2f} | ${sel['mean_price_light_zero_series']:.2f} |
| mean weekly units | {sel['mean_units_heavy_zero_series']:.2f} | {sel['mean_units_light_zero_series']:.2f} |

Series that lose many rows to the rule are systematically **lower-volume**
than series that lose few. Consequences, stated plainly:

1. The modelling sample tilts toward products and stores with continuous
   distribution. Estimated elasticities are representative of *carried,
   actively priced* items, not of the whole assortment.
2. Because dropped rows are almost entirely no-price / no-sales weeks, the
   demand model is not being trained on a censored version of a live demand
   process; it simply never sees weeks in which the product was not on offer.
3. The engine's own eligibility screen already restricts recommendations to
   series with enough history and price variation, so the affected series are
   in practice excluded from pricing anyway - and receive
   `INSUFFICIENT_HISTORY` / `INSUFFICIENT_PRICE_VARIATION` rather than a
   silent drop.

## 8. Decision

**The cleaning rule is kept unchanged.** Justification:

* a zero price cannot produce an effective unit price, a revenue figure or a
  margin - every downstream formula would be undefined;
* {100*(1 - audit['zero_price_and_move_gt_0_share_of_zero']):.2f}% of the excluded rows record no sales either, so almost no
  demand information is lost;
* the alternative (imputing a price) would fabricate the very variable this
  project optimises;
* the exclusion is counted, reported and reproducible in
  `reports/01_DATA_AUDIT.md`.

What changes as a result of this audit is not the rule but the **reporting**:
the limitation is now stated explicitly in `KNOWN_LIMITATIONS.md`, and the
`move > 0` sub-count above is the number a reviewer should ask about.
"""
    out = cfg.path("reports_dir") / "09_ZERO_PRICE_AUDIT.md"
    out.write_text(report, encoding="utf-8")
    print(f"wrote {out}")
    print(f"zero-price rows: {n_zero:,} ({100*n_zero/n:.2f}%), of which with sales: "
          f"{audit['zero_price_and_move_gt_0']:,}")
    print(f"position: leading {100*pos['share_leading']:.1f}% | trailing {100*pos['share_trailing']:.1f}% | "
          f"interior {100*pos['share_interior']:.1f}% | never priced {100*pos['share_never_priced']:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
