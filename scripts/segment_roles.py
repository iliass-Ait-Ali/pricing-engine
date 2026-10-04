"""Product roles: which products the engine would reprice, by commercial role.

    python scripts/segment_roles.py

A category manager does not price every product the same way. High-volume
products that almost every store carries shape shoppers' price image (traffic
drivers, often called key-value items); less price-sensitive, higher-margin
products are where margin is usually built. The v1.0 engine has ONE set of
guardrails for all of them. This analysis does not change that: it assigns
each product a transparent role and reports what the engine's recommendations
look like per role, so the question "are we raising prices on the products
shoppers notice most?" has an answer.

Roles, from the most recent 52 weeks of sales (rules, not a model):

* ``traffic driver`` - top 15% of products by units AND carried by at least
  90% of stores
* ``tail``           - the smallest products that together make the last 5%
  of revenue
* ``margin builder`` - neither of the above, with an above-median gross
  margin rate AND a below-median price sensitivity (|elasticity|)
* ``core``           - everything else

Outputs
-------
    artifacts/metrics/product_roles.json
    artifacts/metrics/product_roles.csv     (one row per product: aggregates only)
    reports/24_PRODUCT_ROLES.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from _commercial import applied_prices, context_elasticity, rescore, score_week  # noqa: E402

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.data.loader import load_processed  # noqa: E402
from pricing_engine.economics.elasticity_store import ElasticityTable  # noqa: E402
from pricing_engine.utils.io import utc_now, write_json  # noqa: E402

ROLE_ORDER = ["traffic driver", "core", "margin builder", "tail"]
TRAFFIC_TOP_SHARE = 0.15
TRAFFIC_MIN_STORE_COVERAGE = 0.90
TAIL_REVENUE_SHARE = 0.05
#: Price-increase cap used in the what-if below (the conservative profile's cap).
WHAT_IF_TRAFFIC_CAP = 0.03


def product_table(cfg, elasticity_path: Path) -> pd.DataFrame:
    panel = load_processed(cfg=cfg)
    recent = panel[panel["week"] > panel["week"].max() - 52]
    n_stores = recent["store"].nunique()
    prod = recent.groupby("upc").agg(
        descrip=("descrip", "first"),
        units=("move", "sum"),
        revenue=("revenue", "sum"),
        gross_profit=("gross_profit", "sum"),
        stores=("store", "nunique"),
    ).reset_index()
    prod["gross_margin_rate"] = prod["gross_profit"] / prod["revenue"]
    prod["store_coverage"] = prod["stores"] / n_stores
    prod["private_label"] = prod["descrip"].astype(str).str.startswith("DOM ")

    table = ElasticityTable.load(elasticity_path)
    prod["elasticity"] = table.epsilon_for(prod["upc"].to_numpy())
    prod["elasticity_source"] = table.source_for(prod["upc"].to_numpy())

    prod = prod.sort_values("units", ascending=False).reset_index(drop=True)
    prod["unit_rank_share"] = (np.arange(len(prod)) + 1) / len(prod)
    by_rev = prod.sort_values("revenue", ascending=False)
    cum = by_rev["revenue"].cumsum() / by_rev["revenue"].sum()
    tail_upcs = set(by_rev.loc[cum.shift(fill_value=0.0) >= 1.0 - TAIL_REVENUE_SHARE, "upc"])

    traffic = (prod["unit_rank_share"] <= TRAFFIC_TOP_SHARE) & (
        prod["store_coverage"] >= TRAFFIC_MIN_STORE_COVERAGE
    )
    tail = prod["upc"].isin(tail_upcs) & ~traffic
    rest = ~traffic & ~tail
    margin = (
        rest
        & (prod["gross_margin_rate"] > prod.loc[rest, "gross_margin_rate"].median())
        & (prod["elasticity"].abs() < prod.loc[rest, "elasticity"].abs().median())
    )
    prod["role"] = np.select([traffic, tail, margin], ["traffic driver", "tail", "margin builder"], "core")
    return prod


def role_summary(prod: pd.DataFrame, recs: pd.DataFrame) -> pd.DataFrame:
    recs = recs.merge(prod[["upc", "role"]], on="upc", how="left")
    recs["role"] = recs["role"].fillna("tail")
    eps = context_elasticity(recs)
    gain = rescore(recs, eps, applied_prices(recs)) - rescore(recs, eps, recs["current_price"].to_numpy(float))
    recs["gp_gain"] = gain
    total_gain = float(np.nansum(gain))

    rows = []
    for role in ROLE_ORDER:
        p = prod[prod["role"] == role]
        r = recs[recs["role"] == role]
        act = r[r["actionable"]]
        rows.append({
            "role": role,
            "products": int(len(p)),
            "revenue_share": float(p["revenue"].sum() / prod["revenue"].sum()),
            "median_elasticity": float(p["elasticity"].median()) if len(p) else np.nan,
            "median_gross_margin_rate": float(p["gross_margin_rate"].median()) if len(p) else np.nan,
            "private_label_products": int(p["private_label"].sum()),
            "contexts": int(len(r)),
            "share_actionable": float(r["actionable"].mean()) if len(r) else np.nan,
            "share_review_required": float((r["decision"] == "REVIEW_REQUIRED").mean()) if len(r) else np.nan,
            "share_increases_among_actionable": float((act["price_change_pct"] > 0).mean()) if len(act) else np.nan,
            "median_change_among_actionable": float(act["price_change_pct"].median()) if len(act) else np.nan,
            "share_of_estimated_gain": float(np.nansum(r["gp_gain"]) / total_gain) if total_gain else np.nan,
        })
    return pd.DataFrame(rows), recs


def traffic_cap_what_if(recs: pd.DataFrame, cap: float) -> dict:
    """Value given up if traffic-driver increases were capped at ``cap``."""
    eps = context_elasticity(recs)
    p0 = recs["current_price"].to_numpy(float)
    full = applied_prices(recs)
    capped = np.where(recs["role"].eq("traffic driver"), np.minimum(full, p0 * (1 + cap)), full)
    base = float(np.nansum(rescore(recs, eps, p0)))
    gain_full = float(np.nansum(rescore(recs, eps, full))) - base
    gain_capped = float(np.nansum(rescore(recs, eps, capped))) - base
    return {
        "cap": cap,
        "uplift_full_pct": gain_full / base,
        "uplift_capped_pct": gain_capped / base,
        "share_of_gain_given_up": (gain_full - gain_capped) / gain_full if gain_full else np.nan,
        "traffic_driver_contexts_affected": int(
            (recs["role"].eq("traffic driver") & (full > p0 * (1 + cap) + 1e-9)).sum()
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=int, default=3000)
    parser.add_argument("--profile", default="standard")
    args = parser.parse_args()

    cfg = load_config()
    elasticity_path = cfg.root / str(cfg.get("pricing_response.elasticity_table"))
    prod = product_table(cfg, elasticity_path)
    recs, week = score_week(cfg, batch=args.batch, profile=args.profile)
    summary, recs = role_summary(prod, recs)
    what_if = traffic_cap_what_if(recs, WHAT_IF_TRAFFIC_CAP)

    out_cols = ["upc", "descrip", "role", "units", "revenue", "gross_margin_rate", "store_coverage",
                "private_label", "elasticity", "elasticity_source"]
    prod[out_cols].to_csv(cfg.path("metrics_dir") / "product_roles.csv", index=False)
    payload = {
        "generated_at_utc": utc_now(),
        "data_mode": cfg.data_mode,
        "data_label": cfg.data_label,
        "decision_week": week,
        "policy_profile": args.profile,
        "rules": {
            "traffic_top_share_by_units": TRAFFIC_TOP_SHARE,
            "traffic_min_store_coverage": TRAFFIC_MIN_STORE_COVERAGE,
            "tail_revenue_share": TAIL_REVENUE_SHARE,
            "margin_builder": "above-median margin rate and below-median |elasticity| among the rest",
        },
        "roles": summary.to_dict("records"),
        "traffic_cap_what_if": what_if,
        "label": "descriptive segmentation; estimated gains are model-internal",
    }
    write_json(cfg.path("metrics_dir") / "product_roles.json", payload)

    def pct(x: float, signed: bool = False) -> str:
        if x is None or (isinstance(x, float) and np.isnan(x)):
            return "-"
        return f"{100 * x:+.1f}%" if signed else f"{100 * x:.1f}%"

    table = "\n".join(
        f"| **{r.role}** | {r.products} | {pct(r.revenue_share)} | {r.median_elasticity:.2f} | "
        f"{pct(r.median_gross_margin_rate)} | {r.contexts:,} | {pct(r.share_actionable)} | "
        f"{pct(r.share_increases_among_actionable)} | {pct(r.median_change_among_actionable, True)} | "
        f"{pct(r.share_of_estimated_gain)} |"
        for r in summary.itertuples()
    )
    traffic = summary.set_index("role").loc["traffic driver"]
    examples = [
        " ".join(w.capitalize() for w in name.split())
        for name in prod.loc[prod["role"] == "traffic driver", "descrip"].astype(str).drop_duplicates()
    ]
    report = f"""# 24 - Product roles: what the engine does to the products shoppers notice

Generated by `python scripts/segment_roles.py` on {payload['generated_at_utc'][:10]}.
Data: **{cfg.data_label}**. Decision week {week}, `{args.profile}` policy.

> A descriptive segmentation with transparent rules, not a model. Estimated
> gains are model-internal (scored with each context's own elasticity) and are
> not realised or causal. Nothing here changes the v1.0 engine.

## Answer first

**Traffic drivers** ({int(traffic['products'])} products, {pct(traffic['revenue_share'])} of
revenue; e.g. {", ".join(examples[:5])}) get an actionable change in
{pct(traffic['share_actionable'])} of their contexts, and
{pct(traffic['share_increases_among_actionable'])} of those changes are
**increases** (median {pct(traffic['median_change_among_actionable'], True)}). They carry
{pct(traffic['share_of_estimated_gain'])} of the estimated gain.

The v1.0 engine treats every product the same way. A retailer that protects its
price image would cap increases on these products. Capping traffic-driver
increases at +{100 * WHAT_IF_TRAFFIC_CAP:.0f}% would give up
**{pct(what_if['share_of_gain_given_up'])} of the estimated gain**
(portfolio uplift {pct(what_if['uplift_full_pct'], True)} -> {pct(what_if['uplift_capped_pct'], True)}),
affecting {what_if['traffic_driver_contexts_affected']:,} contexts. That trade-off is a commercial
decision, which is exactly why it belongs to a category manager and not to the
model.

## Roles

| role | products | revenue share | median elasticity | median margin | contexts | actionable | increases (of actionable) | median change | share of estimated gain |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
{table}

## Rules

* **traffic driver**: top {100 * TRAFFIC_TOP_SHARE:.0f}% of products by units over the last 52
  weeks AND carried by at least {100 * TRAFFIC_MIN_STORE_COVERAGE:.0f}% of stores.
* **tail**: the smallest products that together make the last
  {100 * TAIL_REVENUE_SHARE:.0f}% of revenue.
* **margin builder**: neither, with an above-median gross margin rate and a
  below-median |elasticity| among the remaining products.
* **core**: everything else.

Private-label products (Dominick's own `DOM` brand) are counted per role in
`artifacts/metrics/product_roles.json`.

## What this suggests for a next version (not implemented)

1. **Role-based guardrails.** Tighter increase caps for traffic drivers, the
   current caps for core products, wider room for margin builders. This is a
   policy change and has to be justified as one.
2. **Price-gap rules.** Private label priced at a fixed gap below the national
   brand it copies. The engine prices products independently today, so it
   cannot hold a gap; that needs the cross-price work in `FUTURE_WORK.md` 1.1.
3. **Test it.** The pilot in `docs/PRICING_EXPERIMENT.md` should stratify by
   role, so the effect on traffic drivers is measured separately.
"""
    out = cfg.path("reports_dir") / "24_PRODUCT_ROLES.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(summary[["role", "products", "revenue_share", "share_actionable",
                   "share_increases_among_actionable", "share_of_estimated_gain"]].to_string(index=False))
    print(f"traffic cap +{100 * WHAT_IF_TRAFFIC_CAP:.0f}%: gives up "
          f"{100 * what_if['share_of_gain_given_up']:.1f}% of the estimated gain")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
