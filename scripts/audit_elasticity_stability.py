"""Phase M / Task 7 - is the price response stable over time?

    python scripts/audit_elasticity_stability.py

Re-estimates the whole elasticity stack on several historical windows, all of
them strictly inside the approved training period, and compares them. The final
test window is never used.

The question is whether treating one elasticity estimate as persistent is
defensible, or whether the risk layer has to assume it decays.

Outputs
-------
    reports/17_ELASTICITY_STABILITY.md
    artifacts/metrics/elasticity_stability.json
    artifacts/metrics/elasticity_stability_by_upc.csv
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
from pricing_engine.economics.elasticity_store import build_elasticity_table  # noqa: E402
from pricing_engine.features.build import training_frame  # noqa: E402
from pricing_engine.models.demand_model import load_model  # noqa: E402
from pricing_engine.utils.io import write_json  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    t0 = time.time()
    cfg = load_config()
    meta = load_model(cfg.path("models_dir") / "demand_model.joblib", cfg=cfg).metadata
    lo, hi = int(meta.train_weeks[0]), int(meta.train_weeks[1])
    mid = lo + (hi - lo) // 2
    early_hi = lo + (hi - lo) // 3

    feats = pd.read_parquet(cfg.path("features_table"))
    usable = training_frame(feats)

    windows = {
        f"W1 early ({lo}-{early_hi})": (lo, early_hi),
        f"W2 middle ({early_hi + 1}-{mid})": (early_hi + 1, mid),
        f"W3 late ({mid + 1}-{hi})": (mid + 1, hi),
        f"W4 first half, expanding ({lo}-{mid})": (lo, mid),
        f"W5 full approved training window ({lo}-{hi})": (lo, hi),
    }

    tables, summaries = {}, {}
    for name, (a, b) in windows.items():
        rows = usable[(usable["week"] >= a) & (usable["week"] <= b)]
        if rows["week"].max() > hi:
            raise SystemExit("leakage guard: a stability window exceeds the training window")
        print(f"{name}: {len(rows):,} rows ...")
        table = build_elasticity_table(rows, cfg=cfg, training_weeks=(a, b))
        tables[name] = table
        prods = table.products
        u = prods["usable_for_pricing"].astype(bool)
        summaries[name] = {
            "weeks": [a, b],
            "n_rows": int(len(rows)),
            "pooled": float(table.metadata["pooled_elasticity"]),
            "pooled_se_two_way": float(table.metadata["pooled_se_two_way_cluster"]),
            "fe": float(table.metadata["fe_elasticity"]),
            "tau2": float(table.metadata["tau2_between_product_variance"]),
            "mean_weight": float(table.metadata["shrinkage_mean_weight"]),
            "n_products": int(len(prods)),
            "n_usable": int(u.sum()),
            "share_usable": float(u.mean()),
            "median_shrunk": float(prods.loc[u, "elasticity_final"].median()) if u.any() else None,
            "p10_shrunk": float(prods.loc[u, "elasticity_final"].quantile(0.10)) if u.any() else None,
            "p90_shrunk": float(prods.loc[u, "elasticity_final"].quantile(0.90)) if u.any() else None,
            "share_wrong_sign": float((prods["reject_reason"] == "wrong_sign").mean()),
        }

    # ---- per-product comparison across the three disjoint windows ------------
    disjoint = [k for k in windows if k.startswith(("W1", "W2", "W3"))]
    frames = []
    for name in disjoint:
        p = tables[name].products
        frames.append(
            p.loc[p["usable_for_pricing"].astype(bool), ["upc", "elasticity_raw", "elasticity_final", "std_error"]]
            .set_index("upc")
            .add_suffix(f"__{name[:2]}")
        )
    joined = frames[0].join(frames[1:], how="inner")
    out_csv = cfg.path("metrics_dir") / "elasticity_stability_by_upc.csv"
    joined.to_csv(out_csv)

    pair_rows = []
    pairs = {}
    for i, a in enumerate(disjoint):
        for b in disjoint[i + 1:]:
            ca, cb = f"elasticity_final__{a[:2]}", f"elasticity_final__{b[:2]}"
            ra, rb = f"elasticity_raw__{a[:2]}", f"elasticity_raw__{b[:2]}"
            spearman = float(joined[ca].corr(joined[cb], method="spearman"))
            pearson = float(joined[ca].corr(joined[cb]))
            sign_stable = float(((joined[ra] < 0) == (joined[rb] < 0)).mean())
            mad = float((joined[ca] - joined[cb]).abs().median())
            pairs[f"{a[:2]} vs {b[:2]}"] = {
                "n_common_products": int(len(joined)),
                "spearman_rank_corr_shrunk": spearman,
                "pearson_corr_shrunk": pearson,
                "sign_stability_raw": sign_stable,
                "median_abs_difference_shrunk": mad,
            }
            pair_rows.append([
                f"{a[:2]} vs {b[:2]}", f"{len(joined):,}", f"{spearman:+.3f}",
                f"{pearson:+.3f}", f"{100*sign_stable:.1f}%", f"{mad:.3f}",
            ])

    window_rows = [
        [
            name, f"{s['weeks'][0]}-{s['weeks'][1]}", f"{s['n_rows']:,}",
            f"{s['pooled']:+.3f}", f"{s['pooled_se_two_way']:.3f}", f"{s['fe']:+.3f}",
            f"{s['tau2']:.3f}", f"{s['mean_weight']:.3f}",
            f"{s['n_usable']}/{s['n_products']}",
            f"{s['median_shrunk']:+.2f}" if s["median_shrunk"] is not None else "-",
            f"[{s['p10_shrunk']:+.2f}, {s['p90_shrunk']:+.2f}]" if s["p10_shrunk"] is not None else "-",
        ]
        for name, s in summaries.items()
    ]

    pooled_values = [s["pooled"] for s in summaries.values()]
    pooled_range = max(pooled_values) - min(pooled_values)

    # Is the spread across windows bigger than the sampling noise of the
    # individual windows? Compare the disjoint windows pairwise.
    z_rows, z_stats = [], {}
    for i, a in enumerate(disjoint):
        for b in disjoint[i + 1:]:
            sa, sb = summaries[a], summaries[b]
            diff = sa["pooled"] - sb["pooled"]
            se = float(np.hypot(sa["pooled_se_two_way"], sb["pooled_se_two_way"]))
            z = diff / se if se else float("nan")
            z_stats[f"{a[:2]} vs {b[:2]}"] = {"difference": diff, "se_of_difference": se, "z": z}
            z_rows.append([
                f"{a[:2]} vs {b[:2]}", f"{sa['pooled']:+.3f}", f"{sb['pooled']:+.3f}",
                f"{diff:+.3f}", f"{se:.3f}", f"{z:+.2f}",
                "yes" if abs(z) > 1.96 else "no",
            ])
    max_z = max(abs(v["z"]) for v in z_stats.values())
    median_spearman = float(np.median([p["spearman_rank_corr_shrunk"] for p in pairs.values()]))
    median_sign = float(np.median([p["sign_stability_raw"] for p in pairs.values()]))

    summary = {
        "approved_training_window": [lo, hi],
        "windows": summaries,
        "pairwise": pairs,
        "pooled_elasticity_range_across_windows": float(pooled_range),
        "pairwise_pooled_z_tests": z_stats,
        "max_abs_z_between_disjoint_windows": float(max_z),
        "median_pairwise_rank_correlation": median_spearman,
        "median_pairwise_sign_stability": median_sign,
        "runtime_seconds": round(time.time() - t0, 1),
    }
    write_json(cfg.path("metrics_dir") / "elasticity_stability.json", summary)

    verdict = (
        "stable enough at the category level, unstable at the product level"
        if pooled_range < 0.5 and median_spearman < 0.6
        else "stable at both levels"
        if pooled_range < 0.5
        else "unstable"
    )

    report = f"""# 17. Elasticity stability over time

**Question.** Is price sensitivity stable enough over time to justify treating
one elasticity estimate as persistent?

**Method.** The whole elasticity stack - pooled controlled, UPC x store fixed
effects, per-UPC with panel-robust standard errors, REML empirical-Bayes
shrinkage - is re-estimated on five historical windows. Every window lies
strictly inside the approved training period (weeks {lo}-{hi}). **The validation
and test windows are never touched**, and the script raises rather than run if a
window would exceed the training boundary.

Windows W1/W2/W3 are disjoint thirds, so a per-product comparison between them
uses non-overlapping data. W4 and W5 are expanding windows and are reported for
the category-level estimate only.

## 1. Category-level estimates by window

{md_table(window_rows, ["Window", "Weeks", "Rows", "Pooled", "SE (two-way)", "FE", "tau^2", "Mean w", "Usable products", "Median shrunk", "p10 / p90"], ["---", "---:", "---:", "---:", "---:", "---:", "---:", "---:", "---:", "---:", "---:"])}

The pooled elasticity moves across a range of **{pooled_range:.3f}** between the
most extreme windows. Whether that is drift or noise is a testable question, so
here is the test on the three **disjoint** windows, using each window's own
two-way clustered standard error:

{md_table(z_rows, ["Window pair", "Pooled A", "Pooled B", "Difference", "SE of difference", "z", "Significant at 5%"], ["---", "---:", "---:", "---:", "---:", "---:", "---:"])}

Largest absolute z between disjoint windows: **{max_z:.2f}**.
{"At least one pair differs by more than sampling noise, so there is some genuine drift in the category elasticity." if max_z > 1.96 else "No pair differs by more than sampling noise once the panel clustering is admitted, so the category elasticity is stable within the precision this data supports."}

## 2. Product-level stability across disjoint windows

{md_table(pair_rows, ["Window pair", "Common products", "Rank corr (shrunk)", "Pearson (shrunk)", "Sign stability (raw)", "Median abs difference"], ["---", "---:", "---:", "---:", "---:", "---:"])}

Median pairwise rank correlation: **{median_spearman:+.3f}**.
Median pairwise sign stability: **{100*median_sign:.1f}%** (on the
{int(len(joined))} products that clear the usability screen in *all three*
disjoint windows - a small and favourably selected set, so treat the sign figure
as an upper bound).

This is the uncomfortable number. The sign of a product's price response is
{"largely" if median_sign > 0.85 else "not reliably"} reproducible across
disjoint windows, but the *ordering* of products by elasticity is
{"only weakly" if median_spearman < 0.6 else "reasonably"} reproducible. A
product that looks like the most price-sensitive cereal in one two-year window is
not reliably the most price-sensitive in the next.

## 3. Verdict

**{verdict.capitalize()}.**

The consequences already built into the shipped system:

* The empirical-Bayes shrinkage is exactly the right response to this: it pulls
  each product toward the category estimate in proportion to how noisy that
  product's own coefficient is, and the corrected panel-robust standard errors
  (`reports/14`, `reports/15`) make it pull harder than Phase L did. The mean
  weight of {summaries[list(windows)[-1]]['mean_weight']:.2f} is the amount of
  product-specific signal the data actually support.
* The risk layer already routes thin-evidence contexts to MEDIUM or HIGH risk,
  and HIGH risk never produces an automatic price change under the default
  profiles.
* `POOLED_ELASTICITY_FALLBACK` is emitted as a reason code whenever a product
  has no usable estimate, so a reader can see which recommendations rest on the
  category number.

What this rules out: claiming a stable, product-specific price elasticity as a
durable asset. The defensible claim is a **stable category-level price
sensitivity** with product-level deviations that are partially identified and
shrunk accordingly.

## 4. What is NOT tested here

Elasticity drift into the *test* window. Measuring that would require estimating
on test weeks, which would contaminate the artifact used to price them. The
out-of-time behaviour of the shipped estimate is measured indirectly instead, by
its predictive performance on unseen price-change episodes -
`reports/16_OUT_OF_TIME_PRICE_RESPONSE.md`.

---

*Generated by `scripts/audit_elasticity_stability.py` in {summary['runtime_seconds']}s.
Per-product data: `artifacts/metrics/elasticity_stability_by_upc.csv`.*
"""
    out = cfg.path("reports_dir") / "17_ELASTICITY_STABILITY.md"
    out.write_text(report, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
