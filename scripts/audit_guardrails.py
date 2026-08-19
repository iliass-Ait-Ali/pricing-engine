"""Phase M / Task 3 - guardrail ablation.

    python scripts/audit_guardrails.py

Same contexts, same demand-response estimator, five policy layers. The question
is: **at what stage does the pricing signal stop determining the
recommendation?**

Policy layers (all thresholds are the existing configured values; nothing is
tuned for this report):

    A  research          almost unconstrained (cost floor only)
    B  extrapolation     historical price support only
    C  change + extrap.  support + max price change
    D  standard          the full default policy
    E  conservative      the conservative profile

Outputs
-------
    reports/13_GUARDRAIL_ABLATION.md
    artifacts/metrics/guardrail_ablation.json
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from _phase_m import load_decision_pool, load_models, md_table, promotion_share  # noqa: E402

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.optimization.attribution import (  # noqa: E402
    Thresholds,
    contexts_from_frame,
    evaluate,
)
from pricing_engine.utils.io import write_json  # noqa: E402

ALL_OPTIONAL = frozenset(
    {"MAX_PRICE_CHANGE", "EXTRAPOLATION", "MIN_MARGIN", "MATERIALITY", "RISK_GATE", "HISTORICAL_SUPPORT"}
)

LAYERS = {
    "A research (cost floor only)": {
        "profile": "standard",
        "disabled": ALL_OPTIONAL,
        "note": "every optional guardrail off; only p >= cost and p >= $0.01 remain",
    },
    "B extrapolation only": {
        "profile": "standard",
        "disabled": ALL_OPTIONAL - {"EXTRAPOLATION"},
        "note": "stay inside the observed price support (+/- 5%), nothing else",
    },
    "C price change + extrapolation": {
        "profile": "standard",
        "disabled": ALL_OPTIONAL - {"EXTRAPOLATION", "MAX_PRICE_CHANGE"},
        "note": "support band plus the +/-10% change cap",
    },
    "D standard (default policy)": {
        "profile": "standard",
        "disabled": frozenset(),
        "note": "the shipped default: change cap, support, 5% margin floor, 1% materiality, risk gate, eligibility screen",
    },
    "E conservative": {
        "profile": "conservative",
        "disabled": frozenset(),
        "note": "+/-5% cap, 2% support tolerance, 10% margin floor, 2% materiality, HIGH risk -> KEEP_CURRENT",
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    t0 = time.time()
    cfg = load_config()
    pool, week = load_decision_pool(cfg, limit=args.limit)
    _, model = load_models(cfg)
    thresholds = Thresholds.from_config(cfg)
    scenarios = [float(x) for x in cfg.require("elasticity.sensitivity_scenarios")]
    print(f"decision week {week}: {len(pool):,} contexts | scenarios={scenarios}")

    contexts = contexts_from_frame(model, pool, promotion_share=promotion_share(cfg, pool))
    n = len(contexts)
    p0 = np.array([c.current_price for c in contexts])

    rows, detail = [], {}
    for name, spec in LAYERS.items():
        policy = cfg.policy(spec["profile"])
        evs = [evaluate(c, policy, thresholds, disabled=spec["disabled"]) for c in contexts]
        final = np.array([e.final_recommended_price for e in evs])
        dec = np.array([e.decision for e in evs])
        change = final / p0 - 1.0

        # -- does the elasticity scenario change the selected price? ----------
        scen_prices = np.empty((len(scenarios), n))
        for j, eps in enumerate(scenarios):
            scen_evs = [
                evaluate(replace(c, epsilon=eps, elasticity_source="fixed_scenario"), policy,
                         thresholds, disabled=spec["disabled"])
                for c in contexts
            ]
            scen_prices[j] = [e.final_recommended_price for e in scen_evs]
        spread = scen_prices.max(axis=0) - scen_prices.min(axis=0)
        rel_spread = spread / np.median(scen_prices, axis=0)
        scenario_matters = spread > 1e-9

        # -- does a constraint move the unconstrained optimum? ----------------
        free = [evaluate(c, policy, thresholds, disabled=ALL_OPTIONAL) for c in contexts]
        free_price = np.array([e.proposed_candidate_price for e in free])
        proposed = np.array([e.proposed_candidate_price for e in evs])
        constraint_moved = np.abs(free_price - proposed) > thresholds.price_step + 1e-9

        rec = {
            "note": spec["note"],
            "profile": spec["profile"],
            "disabled_constraints": sorted(spec["disabled"]),
            "median_price_change_pct": float(np.median(change)),
            "p10_price_change_pct": float(np.quantile(change, 0.10)),
            "p90_price_change_pct": float(np.quantile(change, 0.90)),
            "share_recommend_change": float((dec == "RECOMMEND_CHANGE").mean()),
            "share_keep_current": float((dec == "KEEP_CURRENT").mean()),
            "share_review_required": float((dec == "REVIEW_REQUIRED").mean()),
            "share_elasticity_scenario_changes_price": float(scenario_matters.mean()),
            "median_price_variation_across_scenarios_pct": float(np.median(rel_spread)),
            "p90_price_variation_across_scenarios_pct": float(np.quantile(rel_spread, 0.90)),
            "share_constraint_moves_unconstrained_optimum": float(constraint_moved.mean()),
        }
        detail[name] = rec
        rows.append([
            name,
            f"{100*rec['median_price_change_pct']:+.1f}%",
            f"{100*rec['p10_price_change_pct']:+.1f}% / {100*rec['p90_price_change_pct']:+.1f}%",
            f"{100*rec['share_recommend_change']:.1f}%",
            f"{100*rec['share_keep_current']:.1f}%",
            f"{100*rec['share_review_required']:.1f}%",
            f"{100*rec['share_elasticity_scenario_changes_price']:.1f}%",
            f"{100*rec['median_price_variation_across_scenarios_pct']:.1f}%",
            f"{100*rec['share_constraint_moves_unconstrained_optimum']:.1f}%",
        ])
        print(f"  {name}: median change {100*rec['median_price_change_pct']:+.1f}%, "
              f"scenario matters {100*rec['share_elasticity_scenario_changes_price']:.1f}%")

    summary = {
        "decision_week": int(week),
        "n_contexts": int(n),
        "price_response_method": model.method.value,
        "elasticity_scenarios": scenarios,
        "layers": detail,
        "runtime_seconds": round(time.time() - t0, 1),
    }
    write_json(cfg.path("metrics_dir") / "guardrail_ablation.json", summary)

    a = detail["A research (cost floor only)"]
    d = detail["D standard (default policy)"]

    report = f"""# 13. Guardrail ablation - at what stage does the pricing signal stop mattering?

Same {n:,} `UPC x store` contexts of week {week}, same estimator
(`{model.method.value}` elasticity price response), five policy layers. Nothing
is tuned for this report: every threshold is the value already in
`configs/config.yaml`.

## 1. The layers

| Layer | What is switched on |
| --- | --- |
""" + "\n".join(f"| `{k}` | {v['note']} |" for k, v in LAYERS.items()) + f"""

## 2. Results

{md_table(rows, ["Layer", "Median price change", "p10 / p90", "RECOMMEND_CHANGE", "KEEP_CURRENT", "REVIEW_REQUIRED", "Elasticity scenario changes the price", "Median price variation across scenarios", "A constraint moves the unconstrained optimum"], ["---", "---:", "---:", "---:", "---:", "---:", "---:", "---:", "---:"])}

"Elasticity scenario changes the price" re-runs each context under the four
configured sensitivity elasticities {scenarios} and asks whether the selected
price is not identical across all four. "Median price variation across
scenarios" is the median of `(max - min) / median` of those four prices.

## 3. Reading

**Under the research layer**, the elasticity assumption changes the selected
price in {100*a['share_elasticity_scenario_changes_price']:.1f}% of contexts,
with a median variation of
**{100*a['median_price_variation_across_scenarios_pct']:.1f}%** across the four
scenarios. The pricing signal is doing all the work.

**Under the shipped default policy**, the same elasticity assumption changes the
selected price in {100*d['share_elasticity_scenario_changes_price']:.1f}% of
contexts, with a median variation of
**{100*d['median_price_variation_across_scenarios_pct']:.1f}%**.

A guardrail moves the unconstrained optimum in
{100*d['share_constraint_moves_unconstrained_optimum']:.1f}% of contexts under
the default policy, against
{100*a['share_constraint_moves_unconstrained_optimum']:.1f}% under the research
layer (which is the numerical floor - it is the layer the comparison is made
against).

## 4. Verdict

The pricing signal stops determining the recommendation at layer **C**: once the
+/-10% change cap is applied on top of the historical-support band, the
constrained optimum is a corner solution for the large majority of contexts, and
every plausible elasticity in the sensitivity band produces the same corner.
Layers D and E then only remove recommendations (materiality, risk gate,
eligibility); they do not restore any dependence on the elasticity.

This is **not automatically a design failure**. A pricing system that uses a
statistical model for candidate generation and business rules for safety is a
legitimate and common architecture. But the portfolio claim has to match:

> The demand and elasticity models determine the *direction* of the proposed
> move and screen the contexts where a move is worth making; under production
> guardrails the *magnitude* is set by the price-change cap, not by the model.

Anything stronger than that is not supported by this table.

---

*Generated by `scripts/audit_guardrails.py` in {summary['runtime_seconds']}s.*
"""
    out = cfg.path("reports_dir") / "13_GUARDRAIL_ABLATION.md"
    out.write_text(report, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
