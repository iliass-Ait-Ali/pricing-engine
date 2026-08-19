"""Phase M / Task 1 - constraint-binding attribution.

    python scripts/audit_constraints.py

Answers, for every decision context of the final decision week:

* what price would the estimated demand response choose with no guardrails?
* what price does it choose inside the guardrails?
* which constraint stopped it first?
* which constraints are merely present, which remove candidates, which bind at
  the optimum, and which actually change the final decision?

Outputs
-------
    reports/11_CONSTRAINT_ATTRIBUTION.md
    artifacts/metrics/constraint_attribution.json
    artifacts/metrics/constraint_attribution.csv
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

from _phase_m import load_decision_pool, load_models, md_table, promotion_share  # noqa: E402

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.optimization.attribution import (  # noqa: E402
    CONSTRAINTS,
    Thresholds,
    attribute,
    contexts_from_frame,
)
from pricing_engine.utils.io import write_json  # noqa: E402

CONSTRAINT_LABELS = {
    "MAX_PRICE_CHANGE": "Max price change",
    "MIN_PRICE": "Absolute minimum price",
    "MAX_PRICE": "Absolute maximum price",
    "MIN_MARGIN": "Minimum gross margin",
    "COST_FLOOR": "Cost floor (p >= c)",
    "EXTRAPOLATION": "Extrapolation guardrail",
    "ROUNDING": "Price rounding / grid step",
    "MATERIALITY": "Materiality threshold",
    "RISK_GATE": "Risk gate (HIGH / MEDIUM)",
    "HISTORICAL_SUPPORT": "Historical support (eligibility)",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="standard")
    parser.add_argument("--limit", type=int, default=None, help="Cap the pool (default: all).")
    args = parser.parse_args()

    t0 = time.time()
    cfg = load_config()
    pool, week = load_decision_pool(cfg, limit=args.limit)
    _, model = load_models(cfg)
    thresholds = Thresholds.from_config(cfg)
    policy = cfg.policy(args.profile)

    print(f"decision week {week}: {len(pool):,} contexts | method={model.method.value}")
    contexts = contexts_from_frame(model, pool, promotion_share=promotion_share(cfg, pool))
    records = [attribute(c, policy, thresholds) for c in contexts]

    flat = []
    for r in records:
        row = {k: v for k, v in r.items() if k != "constraints"}
        for name, info in r["constraints"].items():
            for key, value in info.items():
                row[f"{name}__{key}"] = value
        flat.append(row)
    df = pd.DataFrame(flat)

    n = len(df)
    out_csv = cfg.path("metrics_dir") / "constraint_attribution.csv"
    df.to_csv(out_csv, index=False)

    # ---- per-constraint table ------------------------------------------------
    table = []
    per_constraint = {}
    for name in CONSTRAINTS:
        present = int(df[f"{name}__present"].sum())
        removed = int((df[f"{name}__removed_candidates"] > 0).sum())
        removed_median = float(df.loc[df[f"{name}__removed_candidates"] > 0, f"{name}__removed_candidates"].median()) if removed else 0.0
        binding = int(df[f"{name}__binding_at_optimum"].sum())
        changed_dec = int(df[f"{name}__changed_decision_state"].sum())
        changed_price = int(df[f"{name}__changed_final_price"].sum())
        per_constraint[name] = {
            "present": present,
            "contexts_removing_candidates": removed,
            "median_candidates_removed_when_active": removed_median,
            "binding_at_optimum": binding,
            "changed_decision_state": changed_dec,
            "changed_final_price": changed_price,
        }
        table.append([
            CONSTRAINT_LABELS[name],
            f"{present:,} ({100*present/n:.1f}%)",
            f"{removed:,} ({100*removed/n:.1f}%)",
            f"{binding:,} ({100*binding/n:.1f}%)",
            f"{changed_dec:,} ({100*changed_dec/n:.1f}%)",
            f"{changed_price:,} ({100*changed_price/n:.1f}%)",
        ])

    first = df["first_binding_constraint"].replace("", "NONE").value_counts()
    inside = int(df["unconstrained_optimum_inside_bounds"].sum())
    proposal_matches = int(df["proposal_matches_unconstrained_optimum"].sum())
    survives = int(df["model_optimum_survives_policy"].sum())
    decisions = df["decision"].value_counts().to_dict()
    round_dist = df["rounding_distance"].dropna()

    # distance between the unconstrained and the final price
    gap = (df["unconstrained_optimum"] / df["current_price"] - 1.0).replace([np.inf, -np.inf], np.nan)
    final_gap = df["price_change_pct"]

    summary = {
        "decision_week": int(week),
        "policy_profile": args.profile,
        "price_response_method": model.method.value,
        "n_contexts": int(n),
        "decision_counts": decisions,
        "n_unconstrained_optimum_inside_bounds": inside,
        "share_unconstrained_optimum_inside_bounds": float(inside / n),
        "n_proposal_matches_unconstrained_optimum": proposal_matches,
        "share_proposal_matches_unconstrained_optimum": float(proposal_matches / n),
        "n_model_optimum_survives_policy": survives,
        "share_model_optimum_survives_policy": float(survives / n),
        "median_rounding_distance_dollars": float(round_dist.median()) if len(round_dist) else None,
        "p90_rounding_distance_dollars": float(round_dist.quantile(0.9)) if len(round_dist) else None,
        "first_binding_constraint_counts": first.to_dict(),
        "median_unconstrained_price_change_pct": float(gap.median()),
        "p10_unconstrained_price_change_pct": float(gap.quantile(0.10)),
        "p90_unconstrained_price_change_pct": float(gap.quantile(0.90)),
        "median_final_price_change_pct": float(final_gap.median()),
        "share_unconstrained_optimum_above_current": float((gap > 0).mean()),
        "per_constraint": per_constraint,
        "runtime_seconds": round(time.time() - t0, 1),
    }
    write_json(cfg.path("metrics_dir") / "constraint_attribution.json", summary)

    # ---- report --------------------------------------------------------------
    first_rows = [
        [k, f"{v:,}", f"{100*v/n:.1f}%"]
        for k, v in first.items()
    ]

    decisions_rows = [[k, f"{v:,}", f"{100*v/n:.1f}%"] for k, v in decisions.items()]

    binding_any = int(df["all_binding_constraints"].astype(str).str.len().gt(0).sum())

    # ---- mutually exclusive attribution of the FINAL recommendation ----------
    reasons = df["reason_codes"].astype(str)
    blocked_pre = reasons.str.contains("INSUFFICIENT_HISTORY|INSUFFICIENT_PRICE_VARIATION|COST_UNAVAILABLE|NO_FEASIBLE_PRICE")
    high_risk_keep = reasons.str.contains("HIGH_RISK_KEEP_CURRENT")
    review = df["decision"].eq("REVIEW_REQUIRED")
    non_material = reasons.str.contains("NON_MATERIAL_UPLIFT")
    at_corner = ~df["unconstrained_optimum_inside_bounds"]

    bucket = pd.Series("learned signal (interior optimum, actionable)", index=df.index)
    bucket[at_corner] = "guardrail corner (optimum outside the feasible interval)"
    bucket[non_material] = "materiality threshold (KEEP_CURRENT)"
    bucket[review] = "risk gate (REVIEW_REQUIRED, not actionable)"
    bucket[high_risk_keep] = "risk gate (HIGH risk -> KEEP_CURRENT)"
    bucket[blocked_pre] = "screened out before optimisation (eligibility / cost)"
    bucket_counts = bucket.value_counts()
    summary["final_determinant_counts"] = bucket_counts.to_dict()
    summary["share_determined_by_learned_signal"] = float(
        bucket.eq("learned signal (interior optimum, actionable)").mean()
    )
    write_json(cfg.path("metrics_dir") / "constraint_attribution.json", summary)
    bucket_rows = [[k, f"{v:,}", f"{100*v/n:.1f}%"] for k, v in bucket_counts.items()]

    report = f"""# 11. Constraint-binding attribution

**Question.** When this engine recommends a price, is the price chosen by the
estimated demand response, or is it simply the nearest point the guardrails
allow?

**Method.** Every one of the {n:,} `UPC x store` decision contexts of week
{week} (the last week of the test window) is run through the full pipeline
four times over: once with no guardrails on a wide research grid
(0.2x .. 5x the current price), once under the `{args.profile}` policy, and
once per constraint with that constraint - and only that constraint - relaxed
(leave-one-out). Price response: `{model.method.value}`.

The optimizer used here is `pricing_engine.optimization.attribution.evaluate`,
a fast replica of `optimize_price`. `tests/test_attribution.py` asserts that it
reproduces the real optimizer's decision state, final price and reason codes
exactly on 60 parameter combinations; it was also checked against 400 real
week-{week} contexts with zero mismatches.

## 1. Where the price comes from

| Quantity | Definition |
| --- | --- |
| `unconstrained_optimum` | arg-max of expected gross profit on the wide research grid, no business guardrails |
| `constrained_optimum` | arg-max inside the feasible interval the policy layer produces |
| `proposed_candidate_price` | the constrained optimum after the materiality check |
| `final_recommended_price` | what would actually be charged: the proposal only when the decision state is actionable |

Under the hybrid response `Q(p) = Q_hat(p0) (p/p0)^e`, the unconstrained
gross-profit optimum has a closed form:

```
p* = c * e / (1 + e)        for e < -1
p* -> unbounded             for -1 < e < 0   (inelastic: raising price always pays)
```

`Q_hat(p0)` is a positive constant across candidate prices, so it **cancels out
of the arg-max**. The demand *forecast* therefore sets the predicted volume and
the dollar amounts, but it does not choose the price: `(p0, c, e)` and the
guardrails do. See `reports/12_MODEL_VALUE_ABLATION.md`.

Median unconstrained optimum vs the current price:
**{100*summary['median_unconstrained_price_change_pct']:+.1f}%**
(p10 {100*summary['p10_unconstrained_price_change_pct']:+.1f}%,
p90 {100*summary['p90_unconstrained_price_change_pct']:+.1f}%).
{100*summary['share_unconstrained_optimum_above_current']:.1f}% of contexts have
an unconstrained optimum **above** the current price.

Median FINAL price change actually recommended:
**{100*summary['median_final_price_change_pct']:+.1f}%**.

Three progressively stricter readings of "the learned price signal decided this
recommendation":

| Reading | Contexts | Share |
| --- | ---: | ---: |
| The unconstrained optimum is inside the feasible interval at all | {inside:,} | {100*inside/n:.1f}% |
| The optimizer's **proposal** lands on it (within one 5c grid step) | {proposal_matches:,} | {100*proposal_matches/n:.1f}% |
| The **final** price lands on it (it also survived materiality and the risk gate) | {survives:,} | {100*survives/n:.1f}% |

The gap between the first two rows is price rounding: the 5-cent candidate grid
is anchored on the lower bound, so the grid optimum sits a median of
${summary['median_rounding_distance_dollars']:.3f} (p90 ${summary['p90_rounding_distance_dollars']:.3f}) away from the
continuous optimum inside the same interval. The gap between the second and
third rows is the non-actionable states: a REVIEW_REQUIRED or KEEP_CURRENT
context ships the current price, whatever the optimizer proposed.

## 2. Constraint attribution

Columns are counts of contexts (out of {n:,}):

* **Present** - configured and applicable to this context.
* **Removes candidates** - excludes at least one price from the wide research
  grid.
* **Binding at optimum** - its own bound is the active edge of the feasible
  interval at the chosen price (for the non-interval guardrails: it is the rule
  that fired).
* **Changed decision state / final price** - leave-one-out: relaxing only this
  constraint changes the decision state / the final price.

{md_table(table, ["Constraint", "Present", "Removes candidates", "Binding at optimum", "Changed decision state", "Changed final price"], ["---", "---:", "---:", "---:", "---:", "---:"])}

At least one interval constraint binds in {binding_any:,} contexts
({100*binding_any/n:.1f}%).

## 3. Which constraint stops the model first

Walking from the current price toward the unconstrained optimum, the first
guardrail encountered:

{md_table(first_rows, ["First binding constraint", "Contexts", "Share"], ["---", "---:", "---:"])}

## 4. Decision states

{md_table(decisions_rows, ["Decision state", "Contexts", "Share"], ["---", "---:", "---:"])}

## 5. What actually determined each final recommendation

One mutually exclusive bucket per context, assigned in priority order
(screened out > risk gate > materiality > guardrail corner > learned signal):

{md_table(bucket_rows, ["Determinant of the final price", "Contexts", "Share"], ["---", "---:", "---:"])}

**{100*summary['share_determined_by_learned_signal']:.1f}%** of final
recommendations are set by an interior optimum of the estimated price response
inside an unbinding feasible interval. Everything else is decided by a
guardrail, a screen or a gate. Note that even the "learned signal" bucket is
mostly the *elasticity*, not the demand forecast: see Section 1.

## 6. Reading of the result

See `reports/13_GUARDRAIL_ABLATION.md` for what happens when the guardrails are
loosened one layer at a time, and `docs/INTERVIEW_RED_TEAM.md` Q2 for how to
defend this architecture. The short version is in
`reports/VALIDATION_SUMMARY.md`.

---

*Generated by `scripts/audit_constraints.py` in {summary['runtime_seconds']}s.
Data: `artifacts/metrics/constraint_attribution.csv` (one row per context).*
"""
    out = cfg.path("reports_dir") / "11_CONSTRAINT_ATTRIBUTION.md"
    out.write_text(report, encoding="utf-8")
    print(f"wrote {out}")
    print(f"wrote {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
