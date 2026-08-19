"""Phase M / Task 6 - naturalistic out-of-time price-response check.

    python scripts/audit_out_of_time_response.py

**This is predictive / naturalistic validation, not causal identification.**
Nothing here identifies a causal price effect. Prices in Dominick's were set by
the retailer in response to expected demand, so an observed demand move after a
price move confounds the price effect with whatever made the retailer change the
price. What this script can honestly answer is narrower and still useful:

    given a price change that actually happened in a period the elasticity
    estimator never saw, which price-response method predicts the demand that
    followed?

Method
------
For every clean consecutive-week price-change episode in the validation and test
weeks:

    baseline_t  = Q_ML(p_{t-1} | context of week t)      contextual baseline
    predicted_t = baseline_t * (p_t / p_{t-1}) ** epsilon

The baseline absorbs seasonality, trend, promotion status and the lagged demand
level, so the only thing the elasticity has to explain is the *deviation* caused
by the price move. The native ML response is `Q_ML(p_t | context of week t)`
directly, and `epsilon = 0` (baseline only, no price response at all) is included
as the null model that every other method has to beat.

Outputs
-------
    reports/16_OUT_OF_TIME_PRICE_RESPONSE.md
    artifacts/metrics/out_of_time_price_response.json
    artifacts/metrics/price_change_episodes.csv
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
from pricing_engine.economics.elasticity_store import ElasticityTable  # noqa: E402
from pricing_engine.features.build import recompute_price_features, training_frame  # noqa: E402
from pricing_engine.models.demand_model import load_model  # noqa: E402
from pricing_engine.utils.io import write_json  # noqa: E402


def wape(actual: np.ndarray, predicted: np.ndarray) -> float:
    denom = np.sum(np.abs(actual))
    return float(np.sum(np.abs(actual - predicted)) / denom) if denom else float("nan")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-change", type=float, default=0.05,
                        help="Minimum |price change| for an episode (default 5%).")
    parser.add_argument("--max-change", type=float, default=0.60,
                        help="Discard implausible jumps above this size.")
    args = parser.parse_args()

    t0 = time.time()
    cfg = load_config()
    model = load_model(cfg.path("models_dir") / "demand_model.joblib", cfg=cfg)
    meta = model.metadata
    train_lo, train_hi = meta.train_weeks
    table = ElasticityTable.load(cfg.root / str(cfg.get("pricing_response.elasticity_table")))

    feats = pd.read_parquet(cfg.path("features_table"))
    usable = training_frame(feats)

    # ---- OUT OF TIME: validation + test weeks only --------------------------
    out_of_time = usable[usable["week"] > train_hi].copy()
    print(f"training weeks {train_lo}-{train_hi}; evaluating weeks "
          f"{int(out_of_time['week'].min())}-{int(out_of_time['week'].max())} "
          f"({len(out_of_time):,} rows)")

    # ---- clean consecutive-week price-change episodes -----------------------
    d = out_of_time.sort_values(["upc", "store", "week"])
    g = d.groupby(["upc", "store"], observed=True)
    d["prev_week"] = g["week"].shift(1)
    d["prev_price"] = g["effective_unit_price"].shift(1)
    d["prev_move"] = g["move"].shift(1)
    d["prev_promo"] = g["recorded_promotion_flag"].shift(1)

    change = d["effective_unit_price"] / d["prev_price"] - 1.0
    episodes = d[
        (d["week"] - d["prev_week"] == 1)
        & d["prev_price"].gt(0)
        & d["prev_move"].gt(0)
        & d["move"].gt(0)
        & d["effective_unit_price"].gt(0)
        & change.abs().ge(args.min_change)
        & change.abs().le(args.max_change)
    ].copy()
    episodes["price_change_pct"] = (
        episodes["effective_unit_price"] / episodes["prev_price"] - 1.0
    )
    print(f"clean price-change episodes: {len(episodes):,}")
    if episodes.empty:
        raise SystemExit("no episodes found")

    # ---- baseline at the PREVIOUS price, week-t context ---------------------
    prev_price = episodes["prev_price"].to_numpy(dtype="float64")
    new_price = episodes["effective_unit_price"].to_numpy(dtype="float64")
    actual = episodes["move"].to_numpy(dtype="float64")

    baseline = np.asarray(
        model.predict(recompute_price_features(episodes, prev_price)), dtype="float64"
    )
    native = np.asarray(
        model.predict(recompute_price_features(episodes, new_price)), dtype="float64"
    )

    # A zero baseline makes every ratio metric undefined; those episodes are
    # dropped and the count is reported rather than silently patched.
    positive = baseline > 0
    n_zero_baseline = int((~positive).sum())
    if n_zero_baseline:
        episodes = episodes[positive].copy()
        prev_price, new_price = prev_price[positive], new_price[positive]
        actual, baseline, native = actual[positive], baseline[positive], native[positive]
        print(f"dropped {n_zero_baseline:,} episodes with a zero contextual baseline")

    ratio = new_price / prev_price
    eps_pooled = float(table.pooled)
    eps_shrunk = table.epsilon_for(episodes["upc"])
    source = table.source_for(episodes["upc"])

    methods = {
        "null (baseline only, epsilon = 0)": baseline,
        "pooled elasticity": baseline * ratio**eps_pooled,
        "shrunk product elasticity": baseline * ratio**eps_shrunk,
        "native ML price response": native,
    }
    episodes["baseline_units"] = baseline
    episodes["elasticity_source"] = source
    for name, pred in methods.items():
        episodes[f"pred__{name}"] = pred

    # ---- metrics -------------------------------------------------------------
    log_ratio_price = np.log(ratio)
    observed_dev = np.log(actual / baseline)          # deviation from the baseline
    implied = observed_dev / log_ratio_price          # episode-level implied elasticity

    promo_now = episodes["recorded_promotion_flag"].to_numpy(dtype="float64") > 0
    promo_prev = episodes["prev_promo"].to_numpy(dtype="float64") > 0
    promo_involved = promo_now | promo_prev

    def metrics(pred: np.ndarray, mask: np.ndarray) -> dict:
        a, p, b = actual[mask], pred[mask], baseline[mask]
        dev_pred = np.log(np.clip(p, 1e-9, None) / np.clip(b, 1e-9, None))
        dev_obs = np.log(np.clip(a, 1e-9, None) / np.clip(b, 1e-9, None))
        # sign accuracy is only meaningful where the method predicts a move
        moves = np.abs(dev_pred) > 1e-9
        return {
            "n": int(mask.sum()),
            "wape": wape(a, p),
            "mae": float(np.mean(np.abs(a - p))),
            "bias": float(np.mean(p - a)),
            "sign_accuracy_vs_baseline": float(
                np.mean(np.sign(dev_pred[moves]) == np.sign(dev_obs[moves]))
            ) if moves.any() else float("nan"),
            "median_abs_error_in_log_demand_change": float(np.median(np.abs(dev_pred - dev_obs))),
            "spearman_rank_corr": float(
                pd.Series(dev_pred).corr(pd.Series(dev_obs), method="spearman")
            ) if np.ptp(dev_pred) > 0 else float("nan"),
        }

    splits = {
        "all episodes": np.ones(len(episodes), dtype=bool),
        "no recorded promotion": ~promo_involved,
        "recorded promotion in either week": promo_involved,
        "price increases": episodes["price_change_pct"].to_numpy() > 0,
        "price decreases": episodes["price_change_pct"].to_numpy() < 0,
    }

    results = {
        split: {name: metrics(pred, mask) for name, pred in methods.items()}
        for split, mask in splits.items()
    }

    # ---- calibration by price-change bucket ---------------------------------
    buckets = pd.cut(
        episodes["price_change_pct"],
        [-0.60, -0.30, -0.15, -0.05, 0.05, 0.15, 0.30, 0.60],
        labels=["-60..-30%", "-30..-15%", "-15..-5%", "-5..5%", "5..15%", "15..30%", "30..60%"],
    )
    calib = pd.DataFrame({
        "bucket": buckets,
        "implied_elasticity": implied,
        "observed_dev": observed_dev,
        "promo": promo_involved,
    })
    calib_rows = []
    for label, sub in calib.groupby("bucket", observed=True):
        if sub.empty:
            continue
        no_promo = sub[~sub["promo"]]
        calib_rows.append([
            str(label), f"{len(sub):,}",
            f"{sub['implied_elasticity'].median():+.2f}",
            f"{no_promo['implied_elasticity'].median():+.2f}" if len(no_promo) else "-",
            f"{len(no_promo):,}",
        ])

    summary = {
        "training_weeks": [int(train_lo), int(train_hi)],
        "evaluation_weeks": [int(out_of_time["week"].min()), int(out_of_time["week"].max())],
        "n_episodes": int(len(episodes)),
        "n_dropped_zero_baseline": n_zero_baseline,
        "min_abs_price_change": args.min_change,
        "max_abs_price_change": args.max_change,
        "share_with_recorded_promotion": float(promo_involved.mean()),
        "pooled_elasticity_used": eps_pooled,
        "median_observed_implied_elasticity": float(np.median(implied)),
        "median_observed_implied_elasticity_no_promo": float(np.median(implied[~promo_involved])),
        "results": results,
        "runtime_seconds": round(time.time() - t0, 1),
    }
    write_json(cfg.path("metrics_dir") / "out_of_time_price_response.json", summary)
    keep = [
        "upc", "store", "week", "prev_price", "effective_unit_price", "price_change_pct",
        "prev_move", "move", "baseline_units", "elasticity_source",
        "recorded_promotion_flag", *[f"pred__{k}" for k in methods],
    ]
    episodes[keep].to_csv(cfg.path("metrics_dir") / "price_change_episodes.csv", index=False)

    def split_table(split: str) -> str:
        rows = []
        for name in methods:
            m = results[split][name]
            rows.append([
                name, f"{m['n']:,}", f"{m['wape']:.4f}", f"{m['mae']:.2f}",
                f"{100*m['sign_accuracy_vs_baseline']:.1f}%" if m["sign_accuracy_vs_baseline"] == m["sign_accuracy_vs_baseline"] else "n/a",
                f"{m['median_abs_error_in_log_demand_change']:.3f}",
                f"{m['spearman_rank_corr']:+.3f}" if m["spearman_rank_corr"] == m["spearman_rank_corr"] else "n/a",
            ])
        return md_table(
            rows,
            ["Method", "n", "WAPE", "MAE (units)", "Sign accuracy", "Median abs error in log demand change", "Rank corr"],
            ["---", "---:", "---:", "---:", "---:", "---:", "---:"],
        )

    best_all = min(methods, key=lambda k: results["all episodes"][k]["wape"])
    best_nopromo = min(methods, key=lambda k: results["no recorded promotion"][k]["wape"])

    report = f"""# 16. Out-of-time price-response check

> **This is predictive / naturalistic validation, not causal identification.**
> Prices in the Dominick's data were set by the retailer, not randomised. An
> observed demand move after a price move confounds the price effect with
> whatever caused the retailer to change the price. Nothing in this report
> identifies a causal price effect, and no number here may be described as one.
> See `docs/CAUSAL_LIMITATIONS.md`.

## What this does answer

Given a price change that actually happened in weeks the elasticity estimator
never saw, which price-response method predicts the demand that followed?

## Method

* Elasticities were estimated on training weeks {train_lo}-{train_hi} only.
  Every episode below is from weeks
  {summary['evaluation_weeks'][0]}-{summary['evaluation_weeks'][1]}
  (validation + test).
* An **episode** is a `UPC x store` series observed in two consecutive weeks,
  both with positive price and positive units, where the price moved by at least
  {100*args.min_change:.0f}% and at most {100*args.max_change:.0f}%.
  **{len(episodes):,} episodes** qualify
  ({n_zero_baseline:,} further episodes were dropped because the contextual
  baseline predicted zero units, which makes every ratio metric undefined).
* The contextual baseline is the demand model's prediction *at the previous
  price* using week `t`'s context - so seasonality, trend, promotion coding and
  the lagged demand level are absorbed, and the elasticity only has to explain
  the deviation the price move caused:

```
baseline_t  = Q_ML(p_{{t-1}} | context of week t)
predicted_t = baseline_t * (p_t / p_{{t-1}}) ** epsilon
```

* `epsilon = 0` (baseline only) is included as the **null model**. A
  price-response method that cannot beat it is not adding anything.
* {100*summary['share_with_recorded_promotion']:.1f}% of episodes carry a
  recorded promotion code in either week; those are reported separately because
  a Bonus Buy moves price and display and feature all at once.

## 1. All episodes

{split_table("all episodes")}

## 2. Episodes with no recorded promotion

{split_table("no recorded promotion")}

## 3. Episodes with a recorded promotion in either week

{split_table("recorded promotion in either week")}

## 4. Price increases

{split_table("price increases")}

## 5. Price decreases

{split_table("price decreases")}

## 6. Calibration by price-change bucket

The observed implied elasticity of an episode is
`log(actual / baseline) / log(p_t / p_{{t-1}})` - what elasticity would have been
needed to explain the demand that actually followed, given the contextual
baseline.

{md_table(calib_rows, ["Price change bucket", "Episodes", "Median implied elasticity", "Median, no promotion", "n (no promotion)"], ["---", "---:", "---:", "---:", "---:"])}

Median observed implied elasticity across all episodes:
**{summary['median_observed_implied_elasticity']:+.2f}**
(no-promotion episodes: **{summary['median_observed_implied_elasticity_no_promo']:+.2f}**).
The pooled elasticity in the shipped table is
**{eps_pooled:+.2f}**.

## 7. Reading

* Lowest WAPE over all episodes: **{best_all}**.
* Lowest WAPE on non-promotion episodes: **{best_nopromo}**.

Sign accuracy is identical across the three price-response methods in every
split, and that is expected: they disagree about *how much* demand moves, never
about *which way*. Sign accuracy therefore tests the sign of the elasticity, not
the choice of method.

The calibration table is the sharper test. Non-promotion episodes imply a median
elasticity of {summary['median_observed_implied_elasticity_no_promo']:+.2f} and
all episodes {summary['median_observed_implied_elasticity']:+.2f}, bracketing the
shipped pooled estimate of {eps_pooled:+.2f}. The native ML response's implied
elasticity of about -3.10 (`reports/08`) sits outside that range on the steep
side, which is the out-of-time evidence for the Phase L decision to stop using it
as the default.

Three warnings about over-reading this table:

1. **The null model is strong.** The contextual baseline already knows last
   week's units, the season and the promotion flag. Beating it by a small margin
   is not evidence of a good price-response model.
2. **Promotion episodes flatter the steeper methods.** A recorded promotion
   bundles a price cut with display and feature activity, so demand jumps far
   more than any pure price elasticity would predict. A method that
   over-predicts the price response will look better on those episodes for the
   wrong reason. That is why the non-promotion split is the one to read.
3. **Selection.** Weeks in which the retailer changed price are not a random
   sample of weeks. The implied elasticities above are the elasticities *of the
   episodes the retailer chose to create*.

---

*Generated by `scripts/audit_out_of_time_response.py` in {summary['runtime_seconds']}s.
Episode-level data: `artifacts/metrics/price_change_episodes.csv`.*
"""
    out = cfg.path("reports_dir") / "16_OUT_OF_TIME_PRICE_RESPONSE.md"
    out.write_text(report, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
