"""What would the recommendations be worth? A value range, not a point.

    python scripts/size_value.py
    python scripts/size_value.py --batch 3000 --profile standard

Re-scores the same decision-week sample as ``scripts/optimize.py`` (same seed,
same contexts, same decisions; nothing is appended to the audit log) and turns
the portfolio uplift into annual gross-profit dollars for the category.

Why a range. The engine's own uplift is model-internal: the price response
that chose each price also scores it. So the same recommended prices are
re-scored under every elasticity the project has evidence for, from the
steepest (the native ML response) to the flattest sensitivity scenario. Prices
are held at what the engine recommended; only the assumed demand response
changes. The spread between the scenarios is the honest uncertainty band, and
the only way to collapse it is the randomised pilot in
``docs/PRICING_EXPERIMENT.md``.

Outputs
-------
    artifacts/metrics/value_sizing.json
    reports/23_BUSINESS_VALUE.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from _commercial import applied_prices, context_elasticity, rescore, score_week  # noqa: E402

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.utils.io import utc_now, write_json  # noqa: E402

WEEKS_PER_YEAR = 52


def total(values: np.ndarray) -> float:
    return float(np.nansum(values))


def scenario_table(df: pd.DataFrame, cfg, own_uplift: float) -> list[dict]:
    p0 = df["current_price"].to_numpy(dtype=float)
    applied = applied_prices(df)
    base = total(rescore(df, -1.0, p0))  # any elasticity: at p = p0 the volume is Q0
    pooled = float(df["elasticity_used"].median())
    native_ml = None
    pr = cfg.path("metrics_dir") / "price_response.json"
    if pr.exists():
        native_ml = json.loads(pr.read_text(encoding="utf-8")).get("summary", {}).get(
            "median_local_elasticity"
        )

    rows = [{
        "scenario": "engine's own price response",
        "assumed_elasticity": "per product",
        "uplift_pct": own_uplift,
    }]
    candidates: list[tuple[str, float]] = []
    if native_ml is not None:
        candidates.append(("native ML response (steepest evidence)", float(native_ml)))
    for e in cfg.get("elasticity.sensitivity_scenarios", []):
        candidates.append((f"sensitivity scenario {e}", float(e)))
    candidates.append(("median elasticity the engine applied", pooled))
    seen = set()
    for name, e in sorted(candidates, key=lambda x: x[1]):
        if round(e, 2) in seen:
            continue
        seen.add(round(e, 2))
        rows.append({
            "scenario": name,
            "assumed_elasticity": round(e, 3),
            "uplift_pct": total(rescore(df, e, applied)) / base - 1.0,
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=int, default=3000)
    parser.add_argument("--profile", default="standard")
    parser.add_argument("--week", type=int, default=None)
    args = parser.parse_args()

    cfg = load_config()
    df, week = score_week(cfg, batch=args.batch, profile=args.profile, week=args.week)

    gp_now = float(df["expected_gross_profit_current"].sum())
    gp_applied = float(np.where(df["actionable"], df["expected_gross_profit_recommended"],
                                df["expected_gross_profit_current"]).sum())
    own_uplift = gp_applied / gp_now - 1.0
    scenarios = scenario_table(df, cfg, own_uplift)

    # Upside if every REVIEW_REQUIRED proposal were approved by a human,
    # scored with the elasticity each context used.
    review = df["decision"] == "REVIEW_REQUIRED"
    with_review = np.where(review, df["proposed_candidate_price"], applied_prices(df)).astype(float)
    eps = context_elasticity(df)
    gp_base = total(rescore(df, -1.0, df["current_price"].to_numpy(dtype=float)))
    review_only = (total(rescore(df, eps, with_review)) - total(rescore(df, eps, applied_prices(df)))) / gp_base

    eda = json.loads((cfg.path("metrics_dir") / "eda_summary.json").read_text(encoding="utf-8"))["scale"]
    annual_gp = eda["total_gross_profit"] * WEEKS_PER_YEAR / eda["weeks"]
    annual_revenue = eda["total_revenue"] * WEEKS_PER_YEAR / eda["weeks"]
    for row in scenarios:
        row["annual_gross_profit_usd"] = row["uplift_pct"] * annual_gp
        row["annual_gross_profit_per_store_usd"] = row["uplift_pct"] * annual_gp / eda["stores"]

    uplifts = [r["uplift_pct"] for r in scenarios]
    summary = {
        "generated_at_utc": utc_now(),
        "data_mode": cfg.data_mode,
        "data_label": cfg.data_label,
        "decision_week": week,
        "policy_profile": args.profile,
        "n_contexts_scored": int(len(df)),
        "share_actionable": float(df["actionable"].mean()),
        "share_review_required": float(review.mean()),
        "baseline": {
            "stores": int(eda["stores"]),
            "weeks_observed": int(eda["weeks"]),
            "annual_category_revenue_usd": annual_revenue,
            "annual_category_gross_profit_usd": annual_gp,
            "annual_gross_profit_per_store_usd": annual_gp / eda["stores"],
        },
        "scenarios": scenarios,
        "range": {
            "low_uplift_pct": min(uplifts),
            "engine_uplift_pct": own_uplift,
            "high_uplift_pct": max(uplifts),
            "low_annual_usd": min(uplifts) * annual_gp,
            "engine_annual_usd": own_uplift * annual_gp,
            "high_annual_usd": max(uplifts) * annual_gp,
        },
        "review_queue_upside_pct": float(review_only),
        "label": "model-internal estimated; not realised or causal",
        "assumptions": [
            "The decision-week sample represents the category (same seed and contexts as optimize.py).",
            "Annual baseline = observed category gross profit scaled to 52 weeks.",
            "Dollars are nominal for the data period (1989-1997 for Dominick's), not inflation-adjusted.",
            "Only RECOMMEND_CHANGE prices move; REVIEW_REQUIRED and KEEP_CURRENT stay at today's price.",
            "Unit cost is held fixed at the decision-time AAC proxy; no competitor or substitution response.",
            "Every scenario re-scores the SAME recommended prices; only the assumed elasticity changes.",
        ],
    }
    write_json(cfg.path("metrics_dir") / "value_sizing.json", summary)

    def money(x: float) -> str:
        return f"${x:,.0f}"

    rows = "\n".join(
        f"| {r['scenario']} | {r['assumed_elasticity']} | {100*r['uplift_pct']:+.1f}% | "
        f"{money(r['annual_gross_profit_usd'])} | {money(r['annual_gross_profit_per_store_usd'])} |"
        for r in scenarios
    )
    rng = summary["range"]
    report = f"""# 23 - Business value: what the recommendations could be worth

Generated by `python scripts/size_value.py --batch {args.batch} --profile {args.profile}`
on {summary['generated_at_utc'][:10]}. Data: **{cfg.data_label}**.

> **Every figure here is a model-internal estimate.** Demand at prices that were
> never charged was never observed. The range below is the spread across every
> demand response this project has evidence for; it is not a confidence
> interval, and none of it is realised or causal uplift. Only the store-level
> randomised pilot in `docs/PRICING_EXPERIMENT.md` can turn it into a measured
> number.

## Answer first

Applying the engine's actionable recommendations (decision week {week},
`{args.profile}` policy) is worth **{100*rng['low_uplift_pct']:+.1f}% to
{100*rng['high_uplift_pct']:+.1f}%** of category gross profit, depending on how
price-sensitive cereal buyers really are. That is
**{money(rng['low_annual_usd'])} to {money(rng['high_annual_usd'])} a year**
across {eda['stores']} stores, with the engine's own estimate at
{100*own_uplift:+.1f}% ({money(rng['engine_annual_usd'])}).

## Baseline

| metric | value |
| --- | --- |
| stores | {eda['stores']} |
| annual category revenue (observed, scaled to 52 weeks) | {money(annual_revenue)} |
| annual category gross profit | {money(annual_gp)} |
| per store | {money(annual_gp / eda['stores'])} |
| contexts scored in the decision week | {len(df):,} |
| actionable (`RECOMMEND_CHANGE`) | {100*summary['share_actionable']:.1f}% |
| escalated to a human (`REVIEW_REQUIRED`) | {100*summary['share_review_required']:.1f}% |

## The same prices under every evidenced demand response

| scenario | assumed elasticity | portfolio uplift | annual gross-profit gain | per store |
| --- | --- | ---: | ---: | ---: |
{rows}

Steeper (more negative) elasticity means buyers walk away faster when prices
rise. Because most actionable changes are increases, a steeper response
shrinks the value; a flatter one grows it.

**Review-queue upside.** If a human approved every `REVIEW_REQUIRED`
proposal, scored with each context's own elasticity, the uplift would change
by a further {100*summary['review_queue_upside_pct']:+.1f} percentage points.
That value exists only if someone works the queue.

## Assumptions

{chr(10).join(f"* {a}" for a in summary['assumptions'])}

## What would turn this into a measured number

A store-randomised pilot (`docs/PRICING_EXPERIMENT.md`): treatment stores take
the engine's actionable prices on a block of cereal products, control stores
keep current pricing, and gross profit per store-week is compared by
difference-in-differences. The width of the range above is the reason to run
it before any rollout.
"""
    out = cfg.path("reports_dir") / "23_BUSINESS_VALUE.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"engine uplift {100*own_uplift:+.2f}% | range {100*rng['low_uplift_pct']:+.2f}% .. "
          f"{100*rng['high_uplift_pct']:+.2f}% | annual {money(rng['low_annual_usd'])} .. "
          f"{money(rng['high_annual_usd'])}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
