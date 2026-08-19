"""Phase M / Task 8 - the product-level elasticity eligibility funnel.

    python scripts/audit_eligibility_funnel.py

Explains every UPC that falls out between "exists in the processed data" and
"is priced with its own shrunk elasticity", with a reason for each transition,
and then carries the funnel through to the decision contexts that actually get
priced.

Outputs
-------
    reports/18_ELASTICITY_ELIGIBILITY_FUNNEL.md
    artifacts/metrics/elasticity_funnel.json
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from _phase_m import load_decision_pool, load_models, md_table, promotion_share  # noqa: E402

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.economics.elasticity import prepare_loglog_frame  # noqa: E402
from pricing_engine.economics.elasticity_store import ElasticityTable  # noqa: E402
from pricing_engine.features.build import training_frame  # noqa: E402
from pricing_engine.models.demand_model import load_model  # noqa: E402
from pricing_engine.optimization.attribution import (  # noqa: E402
    Thresholds,
    contexts_from_frame,
    evaluate,
)
from pricing_engine.utils.io import write_json  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    t0 = time.time()
    cfg = load_config()
    ecfg = cfg.require("elasticity")
    min_obs = int(ecfg["min_obs_per_upc"])
    min_prices = int(ecfg["min_distinct_prices_per_upc"])
    max_se = float(ecfg["max_se_for_product_estimate"])

    meta = load_model(cfg.path("models_dir") / "demand_model.joblib", cfg=cfg).metadata
    lo, hi = int(meta.train_weeks[0]), int(meta.train_weeks[1])

    processed = pd.read_parquet(cfg.path("processed_table"), columns=["upc", "week", "move"])
    n_processed_upcs = int(processed["upc"].nunique())

    feats = pd.read_parquet(cfg.path("features_table"))
    modelling = training_frame(feats)
    n_modelling_upcs = int(modelling["upc"].nunique())

    train = modelling[(modelling["week"] >= lo) & (modelling["week"] <= hi)]
    n_train_upcs = int(train["upc"].nunique())

    prepared, zero_note = prepare_loglog_frame(train, zero_handling="drop")
    n_positive_upcs = int(prepared["upc"].nunique())

    per_upc = prepared.groupby("upc", observed=True).agg(
        n_obs=("log_q", "size"), n_prices=("effective_unit_price", "nunique")
    )
    enough_history = per_upc[per_upc["n_obs"] >= min_obs]
    enough_prices = enough_history[enough_history["n_prices"] >= min_prices]

    table = ElasticityTable.load(cfg.root / str(cfg.get("pricing_response.elasticity_table")))
    prods = table.products
    estimated = prods[prods["usable"].astype(bool)]
    reasons = prods["reject_reason"].fillna("")
    n_wrong_sign = int((reasons == "wrong_sign").sum())
    n_se_too_large = int((reasons == "standard_error_too_large").sum())
    n_regression_failed = int(reasons.str.startswith("regression_failed").sum())
    n_no_variation = int((reasons == "no_price_variation_after_fixed_effects").sum())
    usable = prods[prods["usable_for_pricing"].astype(bool)]

    funnel = [
        ("UPCs in the processed dataset", n_processed_upcs,
         "every cereal UPC that survived the raw data audit"),
        ("... present in the modelling table", n_modelling_upcs,
         "needs at least one lagged price and one lagged demand observation"),
        ("... observed in the training weeks", n_train_upcs,
         f"elasticity estimation is restricted to weeks {lo}-{hi} (no validation or test outcomes)"),
        ("... with positive-sales training weeks", n_positive_upcs,
         f"log demand is undefined at zero units ({zero_note})"),
        (f"... with >= {min_obs} training observations", int(len(enough_history)),
         "a per-product regression on fewer rows is not worth reporting"),
        (f"... and >= {min_prices} distinct prices", int(len(enough_prices)),
         "the price coefficient is identified from price variation; without it there is nothing to fit"),
        ("... successfully estimated", int(len(estimated)),
         "the regression converged and produced a finite coefficient and standard error"),
        ("... after removing wrong-signed coefficients", int(len(estimated)) - n_wrong_sign,
         "a positive price coefficient is evidence of endogeneity, not of upward-sloping demand"),
        ("... after removing imprecise coefficients", int(len(usable)),
         f"panel-robust standard error must be <= {max_se}"),
    ]
    funnel_rows = []
    prev = None
    for label, n, why in funnel:
        drop = "" if prev is None else f"-{prev - n:,}"
        funnel_rows.append([label, f"{n:,}", drop, f"{100*n/n_processed_upcs:.1f}%", why])
        prev = n

    reject_rows = [
        ["insufficient observations or price variation",
         int((reasons == "insufficient_observations_or_price_variation").sum()),
         f"fewer than {min_obs} positive-sales training rows or fewer than {min_prices} distinct prices"],
        ["wrong sign", n_wrong_sign, "estimated coefficient >= 0"],
        ["standard error too large", n_se_too_large, f"panel-robust SE > {max_se}"],
        ["no price variation after fixed effects", n_no_variation,
         "log price is collinear with the store fixed effects inside that product"],
        ["regression failed", n_regression_failed, "singular design matrix"],
    ]
    reject_rows = [[a, f"{b:,}", c] for a, b, c in reject_rows if b]

    # ---- carry the funnel through to real decision contexts -----------------
    pool, week = load_decision_pool(cfg)
    _, model = load_models(cfg)
    thresholds = Thresholds.from_config(cfg)
    policy = cfg.policy("standard")
    contexts = contexts_from_frame(model, pool, promotion_share=promotion_share(cfg, pool))
    evs = [evaluate(c, policy, thresholds) for c in contexts]
    ctx = pd.DataFrame({
        "upc": [c.upc for c in contexts],
        "source": [c.elasticity_source for c in contexts],
        "decision": [e.decision for e in evs],
    })
    n_ctx = len(ctx)
    source_counts = ctx["source"].value_counts()
    decision_by_source = (
        ctx.groupby(["source", "decision"], observed=True).size().unstack(fill_value=0)
    )

    ctx_rows = [
        [k, f"{v:,}", f"{100*v/n_ctx:.1f}%"] for k, v in source_counts.items()
    ]

    summary = {
        "training_weeks": [lo, hi],
        "thresholds": {"min_obs_per_upc": min_obs, "min_distinct_prices_per_upc": min_prices,
                       "max_se_for_product_estimate": max_se},
        "funnel": [{"stage": a, "n_upcs": b, "reason": c} for a, b, c in funnel],
        "rejections": {a: int(b.replace(",", "")) for a, b, _ in reject_rows},
        "decision_week": int(week),
        "n_decision_contexts": int(n_ctx),
        "elasticity_source_counts": source_counts.to_dict(),
        "share_shrunk_product": float(source_counts.get("shrunk_product", 0) / n_ctx),
        "share_pooled_fallback": float(source_counts.get("pooled_fallback", 0) / n_ctx),
        "runtime_seconds": round(time.time() - t0, 1),
    }
    write_json(cfg.path("metrics_dir") / "elasticity_funnel.json", summary)

    report = f"""# 18. Product-level elasticity eligibility funnel

Every UPC that drops out between "exists in the processed data" and "is priced
with its own shrunk elasticity", with the reason for each transition. Estimation
window: training weeks {lo}-{hi} only.

## 1. The funnel

{md_table(funnel_rows, ["Stage", "UPCs", "Lost", "Share of all UPCs", "Why the transition happens"], ["---", "---:", "---:", "---:", "---"])}

## 2. Rejection reasons in the estimation step

{md_table(reject_rows, ["Reason", "UPCs", "Rule"], ["---", "---:", "---"])}

The largest single loss is the observation / price-variation screen. That is not
a modelling failure: a cereal UPC that appears in a handful of store-weeks, or
that never moved off one price point in the training period, contains no
identifying variation for a price coefficient. Fitting one anyway and shipping it
would be the failure.

## 3. What the remaining UPCs actually get

A UPC that fails the screen is **not** dropped from pricing. It falls back to the
pooled category elasticity, and the recommendation carries the
`POOLED_ELASTICITY_FALLBACK` reason code so a reader can see it. The risk layer
also downgrades those contexts from LOW to MEDIUM risk, which tightens their
price-change cap.

For the {n_ctx:,} `UPC x store` decision contexts of week {week}:

{md_table(ctx_rows, ["Elasticity source", "Contexts", "Share"], ["---", "---:", "---:"])}

Decision states by elasticity source:

{decision_by_source.to_markdown()}

## 4. Reading

* **{100*summary['share_shrunk_product']:.1f}%** of decision contexts are priced
  with a product-specific shrunk elasticity;
  **{100*summary['share_pooled_fallback']:.1f}%** use the pooled category
  estimate.
* The product-count funnel and the context-share funnel differ sharply, and in
  the useful direction: the UPCs that survive the screen are the ones sold in
  many stores over many weeks, so they account for a much larger share of
  decision contexts than of distinct UPCs.
* "Product-specific" is a matter of degree, not a binary. Even inside the
  `shrunk_product` group the mean empirical-Bayes weight is about 0.78
  (`reports/14`), so roughly a fifth of each of those elasticities is the
  category estimate.

---

*Generated by `scripts/audit_eligibility_funnel.py` in {summary['runtime_seconds']}s.*
"""
    out = cfg.path("reports_dir") / "18_ELASTICITY_ELIGIBILITY_FUNNEL.md"
    out.write_text(report, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
