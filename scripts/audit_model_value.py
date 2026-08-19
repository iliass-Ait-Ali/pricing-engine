"""Phase M / Task 2 - does the learned price response change the decisions?

    python scripts/audit_model_value.py

Builds deliberately dumb RULE-ONLY policies that never consult an estimated
demand curve, pushes them through the *same* policy layer as the model, and
compares the decisions.

This is **not** a profit comparison. Scoring a rule with the model's own demand
curve and declaring the model better would be circular: the model both proposes
and grades. The only question asked here is whether the learned elasticity layer
produces materially *different* decisions from reasonable rules.

Outputs
-------
    reports/12_MODEL_VALUE_ABLATION.md
    artifacts/metrics/model_value_ablation.json
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
    Thresholds,
    contexts_from_frame,
    evaluate,
    evaluate_rule,
)
from pricing_engine.utils.io import write_json  # noqa: E402

RULE_DESCRIPTIONS = {
    "R0 keep current price": "never move a price; the only policy whose outcome was actually observed",
    "R1 hold historical margin": "p = c / (1 - m), m = that series' median historical gross-margin rate",
    "R2 cost-plus 25% margin": "p = c / (1 - 0.25), one fixed category margin target",
    "R3 nearest historical modal price": "snap to the most frequent historical price of that series",
    "R4 always take the max allowed increase": "p = p0 * (1 + effective change cap); no demand model at all",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="standard")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    t0 = time.time()
    cfg = load_config()
    pool, week = load_decision_pool(cfg, limit=args.limit)
    _, model = load_models(cfg)
    thresholds = Thresholds.from_config(cfg)
    policy = cfg.policy(args.profile)
    print(f"decision week {week}: {len(pool):,} contexts | method={model.method.value}")

    # ---- rule inputs computed from TRAINING-WINDOW history only -------------
    train_max = int(pd.read_parquet(cfg.path("features_table"), columns=["week"])["week"].max())
    meta = pd.read_json(cfg.path("models_dir") / "demand_model_metadata.json", typ="series")
    train_hi = int(meta["train_weeks"][1]) if "train_weeks" in meta else train_max
    hist = pd.read_parquet(
        cfg.path("features_table"),
        columns=["upc", "store", "week", "effective_unit_price", "gross_margin_rate"],
    )
    hist = hist[hist["week"] <= train_hi]
    g = hist.groupby(["upc", "store"], observed=True)
    median_margin = g["gross_margin_rate"].median()
    modal_price = g["effective_unit_price"].agg(lambda s: s.round(2).mode().iloc[0] if len(s) else np.nan)

    keys = pd.MultiIndex.from_arrays([pool["upc"], pool["store"]])
    pool_margin = pd.Series(keys.map(median_margin), dtype="float64").to_numpy()
    pool_modal = pd.Series(keys.map(modal_price), dtype="float64").to_numpy()

    contexts = contexts_from_frame(model, pool, promotion_share=promotion_share(cfg, pool))

    # ---- run the model policy and every rule policy -------------------------
    model_evs = [evaluate(c, policy, thresholds) for c in contexts]

    def rule_targets(name: str) -> np.ndarray:
        p0 = np.array([c.current_price for c in contexts])
        cost = np.array([c.unit_cost if c.unit_cost is not None else np.nan for c in contexts])
        if name == "R0 keep current price":
            return p0
        if name == "R1 hold historical margin":
            m = np.clip(np.nan_to_num(pool_margin, nan=0.0), 0.0, 0.80)
            return np.where(np.isfinite(cost), cost / (1.0 - m), p0)
        if name == "R2 cost-plus 25% margin":
            return np.where(np.isfinite(cost), cost / 0.75, p0)
        if name == "R3 nearest historical modal price":
            return np.where(np.isfinite(pool_modal), pool_modal, p0)
        if name == "R4 always take the max allowed increase":
            cap = float(policy["max_price_change_pct"])
            return p0 * (1.0 + cap)
        raise ValueError(name)

    rules = {name: [evaluate_rule(c, float(t), policy, thresholds)
                    for c, t in zip(contexts, rule_targets(name), strict=True)]
             for name in RULE_DESCRIPTIONS}

    n = len(contexts)
    p0 = np.array([c.current_price for c in contexts])
    model_final = np.array([e.final_recommended_price for e in model_evs])
    model_dec = np.array([e.decision for e in model_evs])

    rows, detail = [], {}
    for name, evs in rules.items():
        rule_final = np.array([e.final_recommended_price for e in evs])
        rule_dec = np.array([e.decision for e in evs])
        diff = np.abs(model_final - rule_final)
        differs = diff > 1e-9
        # The candidate grid is 5c wide, so two policies that both go "to the
        # cap" can land one step apart. Materially different means more than
        # one grid step.
        step = thresholds.price_step
        differs_materially = diff > step + 1e-9
        same_decision = model_dec == rule_dec
        model_keeps_rule_moves = (model_dec != "RECOMMEND_CHANGE") & (rule_dec == "RECOMMEND_CHANGE")
        model_moves_rule_keeps = (model_dec == "RECOMMEND_CHANGE") & (rule_dec != "RECOMMEND_CHANGE")
        rec = {
            "description": RULE_DESCRIPTIONS[name],
            "share_final_price_differs": float(differs.mean()),
            "share_final_price_differs_more_than_one_grid_step": float(differs_materially.mean()),
            "mean_abs_price_difference": float(diff.mean()),
            "median_abs_price_difference": float(np.median(diff)),
            "mean_abs_price_difference_pct": float(np.mean(diff / p0)),
            "median_abs_price_difference_pct": float(np.median(diff / p0)),
            "share_same_decision_state": float(same_decision.mean()),
            "share_model_keeps_rule_changes": float(model_keeps_rule_moves.mean()),
            "share_model_changes_rule_keeps": float(model_moves_rule_keeps.mean()),
            "decision_counts": pd.Series(rule_dec).value_counts().to_dict(),
            "median_price_change_pct_when_actionable": float(
                np.median((rule_final[rule_dec == "RECOMMEND_CHANGE"] / p0[rule_dec == "RECOMMEND_CHANGE"]) - 1.0)
            ) if (rule_dec == "RECOMMEND_CHANGE").any() else 0.0,
        }
        detail[name] = rec
        rows.append([
            name,
            f"{100*rec['share_final_price_differs']:.1f}%",
            f"{100*rec['share_final_price_differs_more_than_one_grid_step']:.1f}%",
            f"${rec['mean_abs_price_difference']:.3f}",
            f"${rec['median_abs_price_difference']:.3f}",
            f"{100*rec['median_abs_price_difference_pct']:.1f}%",
            f"{100*rec['share_same_decision_state']:.1f}%",
            f"{100*rec['share_model_keeps_rule_changes']:.1f}%",
            f"{100*rec['share_model_changes_rule_keeps']:.1f}%",
        ])

    # ---- magnitude + risk distributions -------------------------------------
    def magnitude_rows(dec, final):
        change = final / p0 - 1.0
        act = dec == "RECOMMEND_CHANGE"
        buckets = pd.cut(
            np.abs(change[act]),
            [-1e-9, 0.01, 0.025, 0.05, 0.075, 0.10, 1.0],
            labels=["<1%", "1-2.5%", "2.5-5%", "5-7.5%", "7.5-10%", ">10%"],
        )
        return pd.Series(buckets).value_counts().reindex(
            ["<1%", "1-2.5%", "2.5-5%", "5-7.5%", "7.5-10%", ">10%"]
        ).fillna(0).astype(int)

    mag = {"MODEL (shrunk elasticity)": magnitude_rows(model_dec, model_final)}
    for name, evs in rules.items():
        mag[name] = magnitude_rows(
            np.array([e.decision for e in evs]),
            np.array([e.final_recommended_price for e in evs]),
        )
    mag_df = pd.DataFrame(mag).T

    risk = {"MODEL (shrunk elasticity)": pd.Series([e.risk_level for e in model_evs]).value_counts()}
    for name, evs in rules.items():
        risk[name] = pd.Series([e.risk_level for e in evs]).value_counts()
    risk_df = pd.DataFrame(risk).T.fillna(0).astype(int)

    def corner_profile(evs):
        """Where the actionable prices sit inside the feasible interval."""
        step = thresholds.price_step
        at_low = at_high = interior = 0
        for e in evs:
            if e.decision != "RECOMMEND_CHANGE":
                continue
            lo, hi = e.bounds
            if abs(e.final_recommended_price - hi) <= step + 1e-9:
                at_high += 1
            elif abs(e.final_recommended_price - lo) <= step + 1e-9:
                at_low += 1
            else:
                interior += 1
        total = max(at_low + at_high + interior, 1)
        return pd.Series(
            {
                "at upper bound": at_high,
                "at lower bound": at_low,
                "interior": interior,
                "interior %": round(100.0 * interior / total, 1),
            }
        )

    bind = {"MODEL (shrunk elasticity)": corner_profile(model_evs)}
    for name, evs in rules.items():
        bind[name] = corner_profile(evs)
    bind_df = pd.DataFrame(bind).T

    model_summary = {
        "decision_counts": pd.Series(model_dec).value_counts().to_dict(),
        "median_price_change_pct_when_actionable": float(
            np.median((model_final[model_dec == "RECOMMEND_CHANGE"] / p0[model_dec == "RECOMMEND_CHANGE"]) - 1.0)
        ),
    }

    summary = {
        "decision_week": int(week),
        "policy_profile": args.profile,
        "price_response_method": model.method.value,
        "n_contexts": int(n),
        "model": model_summary,
        "rules": detail,
        "runtime_seconds": round(time.time() - t0, 1),
    }
    write_json(cfg.path("metrics_dir") / "model_value_ablation.json", summary)

    closest = min(detail, key=lambda k: detail[k]["share_final_price_differs_more_than_one_grid_step"])
    r4 = detail["R4 always take the max allowed increase"]

    report = f"""# 12. Model value ablation - learned price response vs rule-only pricing

**Question.** Does the learned demand / elasticity layer produce materially
different decisions from reasonable rules?

**Not the question.** Whether the model earns more money. Scoring a rule with
the model's own demand curve and declaring the model better is circular - the
same fitted curve would be both proposer and judge. No profit comparison is
made here. See `docs/CAUSAL_LIMITATIONS.md`.

**Setup.** All {n:,} `UPC x store` contexts of week {week}, policy profile
`{args.profile}`, price response `{model.method.value}`. Every rule is pushed
through the *same* policy layer as the model - eligibility screen, risk gate,
feasible interval, rounding, materiality, decision state - so the comparison
isolates the pricing signal, not the guardrails. Rule inputs (median margin,
modal price) are computed from **training weeks only**.

## 1. The rules

| Rule | Definition |
| --- | --- |
""" + "\n".join(f"| `{k}` | {v} |" for k, v in RULE_DESCRIPTIONS.items()) + f"""

## 2. Decision agreement with the model policy

{md_table(rows, ["Rule", "Final price differs at all", "Differs by > 1 grid step (5c)", "Mean abs diff", "Median abs diff", "Median abs diff %", "Same decision state", "Model keeps / rule changes", "Model changes / rule keeps"], ["---", "---:", "---:", "---:", "---:", "---:", "---:", "---:", "---:"])}

**The headline.** The rule that never looks at demand at all - `R4 always take
the max allowed increase` - lands within one 5-cent grid step of the model's
final price in **{100*(1-r4['share_final_price_differs_more_than_one_grid_step']):.1f}%** of
contexts and agrees on the decision state in
{100*r4['share_same_decision_state']:.1f}%; the median absolute price difference
is ${r4['median_abs_price_difference']:.3f}
({100*r4['median_abs_price_difference_pct']:.1f}% of price). The exact-match rate
is lower ({100*(1-r4['share_final_price_differs']):.1f}%) only because the 5-cent
candidate grid rarely contains the cap itself. The closest rule overall is
`{closest}`.

This is the same fact `reports/11_CONSTRAINT_ATTRIBUTION.md` reports from the
other direction: the model's unconstrained optimum sits above the change cap in
most contexts, so the constrained answer is "go to the cap", which is exactly
what R4 does by construction.

## 3. Where the model and the rules do differ

The learned layer is not decorative. It differs from R4 in the contexts where

* the estimated elasticity implies an optimum *below* the current price
  (a price cut), which R4 can never propose;
* the optimum is interior, so the recommended move is smaller than the cap;
* the estimated uplift is immaterial and the model keeps the price while R4
  would still move it.

Model decisions: {model_summary['decision_counts']}
Median actionable price change (model): {100*model_summary['median_price_change_pct_when_actionable']:+.2f}%

## 4. Distribution of recommendation magnitudes (actionable contexts only)

{mag_df.to_markdown()}

## 5. Risk distribution

{risk_df.to_markdown()}

## 6. Where each policy's actionable price sits in the feasible interval

The feasible interval is identical for every policy (it depends only on the
context and the guardrails), so what separates the policies is *where inside it
they land*. A policy that always sits on a bound is not being priced by its
demand signal.

{bind_df.to_markdown()}

## 7. Conclusion

The learned elasticity layer changes *which direction and how far* to move in a
minority of contexts, and is otherwise dominated by the change cap. The honest
portfolio claim is therefore:

> The elasticity layer decides the direction of the price move and identifies
> the contexts where a cut is better than an increase; the guardrails decide the
> magnitude in most contexts.

It is **not** defensible to claim that the ML pipeline is what produces the
recommended prices in the standard configuration.

---

*Generated by `scripts/audit_model_value.py` in {summary['runtime_seconds']}s.*
"""
    out = cfg.path("reports_dir") / "12_MODEL_VALUE_ABLATION.md"
    out.write_text(report, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
