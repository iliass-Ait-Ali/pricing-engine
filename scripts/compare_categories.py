"""Do the cereal findings hold in a second category?

    python scripts/compare_categories.py --config configs/crackers.yaml

Reads the metrics the unchanged pipeline produced for the base category
(``configs/config.yaml``, Cereals) and for a second category
(``scripts/run_category.py``), and puts the headline findings side by side.
Nothing was re-tuned between the two runs: every model setting, guardrail,
policy profile and risk threshold is shared.

A finding "replicates" here when it points the same way in both categories.
Two categories from one retailer and one decade are a robustness check, not a
proof of generality.

Outputs
-------
    artifacts/metrics/second_category.json
    reports/27_SECOND_CATEGORY.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.utils.io import utc_now, write_json  # noqa: E402

MAX_INCREASE_RULE = "R4 always take the max allowed increase"
LEARNED = "learned signal (interior optimum, actionable)"
CORNER = "guardrail corner (optimum outside the feasible interval)"


def headline(cfg) -> dict:
    """The comparable headline numbers of one category run."""
    m = cfg.path("metrics_dir")

    def load(name: str) -> dict:
        return json.loads((m / name).read_text(encoding="utf-8"))

    build, eda = load("build_audit.json"), load("eda_summary.json")
    elas, est = load("elasticity.json"), load("elasticity_estimation.json")
    models, pr = load("model_metrics.json"), load("price_response.json")["summary"]
    recs, attr = load("recommendations.json"), load("constraint_attribution.json")
    ablation, value, roles = (load("model_value_ablation.json"), load("value_sizing.json"),
                              load("product_roles.json"))

    ladder = {r["model"]: r["price_elasticity"] for r in elas["loglog"]}
    names = list(ladder)
    results = models["results"]
    naive = {k: v["valid"]["wape"] for k, v in results.items() if v.get("kind") == "baseline"}
    selected = models["selected"]
    n = attr["n_contexts"]
    det = attr["final_determinant_counts"]
    first = attr["first_binding_constraint_counts"]
    top_binding = max((k for k in first if k != "NONE"), key=first.get)
    rule = ablation["rules"][MAX_INCREASE_RULE]
    traffic = {r["role"]: r for r in roles["roles"]}["traffic driver"]
    series = eda["price_variation"]["upc_store_series"]
    return {
        "category": cfg.get("project.category"),
        "raw_rows": build["raw_rows"],
        "canonical_rows": build["processed_rows"],
        "share_rows_removed": build["rows_removed"] / build["raw_rows"],
        "upcs": build["upcs"], "stores": build["stores"], "weeks": build["weeks"],
        "revenue": build["total_revenue"],
        "gross_margin_pct": 100 * build["total_gross_profit"] / build["total_revenue"],
        "share_series_eligible": series["pct_eligible"] / 100,
        "elasticity_naive": ladder[names[0]],
        "elasticity_upc_store_fe": ladder[names[2]],
        "elasticity_controlled": ladder[names[3]],
        "elasticity_pooled_training": est["pooled_elasticity"],
        "elasticity_pooled_ci": [est["pooled_ci_low_two_way"], est["pooled_ci_high_two_way"]],
        "products_usable": est["n_products_usable"], "products": est["n_products"],
        "mean_shrinkage_weight": est["mean_shrinkage_weight"],
        "native_ml_elasticity": pr["median_local_elasticity"],
        "selected_model": selected,
        "valid_wape": results[selected]["valid"]["wape"],
        "test_wape": results[selected]["test"]["wape"],
        "best_naive_valid_wape": min(naive.values()),
        "share_actionable": recs["share_actionable"],
        "share_review": recs["share_review_required"],
        "share_keep": recs["share_keep_current"],
        "share_increases": recs["share_price_increases"],
        "high_risk_auto_changed": recs["high_risk_actionable"],
        "decision_contexts": n,
        "share_learned_signal": det.get(LEARNED, 0) / n,
        "share_guardrail_corner": det.get(CORNER, 0) / n,
        "most_binding_constraint": top_binding,
        "most_binding_share": first[top_binding] / n,
        "rule_within_one_step": 1 - rule["share_final_price_differs_more_than_one_grid_step"],
        "rule_same_decision": rule["share_same_decision_state"],
        "uplift_engine": value["range"]["engine_uplift_pct"],
        "uplift_low": value["range"]["low_uplift_pct"],
        "uplift_high": value["range"]["high_uplift_pct"],
        "traffic_revenue_share": traffic["revenue_share"],
        "traffic_share_actionable": traffic["share_actionable"],
        "traffic_share_increases": traffic["share_increases_among_actionable"],
        "traffic_cap_gain_given_up": roles["traffic_cap_what_if"]["share_of_gain_given_up"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Config of the second category.")
    args = parser.parse_args()

    base_cfg = load_config(REPO_ROOT / "configs" / "config.yaml")
    other_cfg = load_config(Path(args.config).resolve())
    a, b = headline(base_cfg), headline(other_cfg)
    A, B = a["category"].title(), b["category"].title()

    same_settings = all(base_cfg.get(k) == other_cfg.get(k) for k in
                        ("policy_profiles", "risk", "optimization", "modeling", "eligibility",
                         "elasticity"))

    checks = [
        ("Fixed effects and controls turn a near-zero naive elasticity into an elastic one",
         a["elasticity_naive"] > a["elasticity_controlled"] < -1,
         b["elasticity_naive"] > b["elasticity_controlled"] < -1),
        ("The selected demand model beats the best naive baseline",
         a["valid_wape"] < a["best_naive_valid_wape"], b["valid_wape"] < b["best_naive_valid_wape"]),
        ("The native ML price response is steeper than the controlled estimate",
         a["native_ml_elasticity"] < a["elasticity_pooled_training"],
         b["native_ml_elasticity"] < b["elasticity_pooled_training"]),
        ("Fewer than 10% of final prices come from the model's own optimum",
         a["share_learned_signal"] < 0.10, b["share_learned_signal"] < 0.10),
        ("A no-model rule lands within one grid step of the engine most of the time",
         a["rule_within_one_step"] > 0.5, b["rule_within_one_step"] > 0.5),
        ("No HIGH-risk context changes a price automatically",
         a["high_risk_auto_changed"] == 0, b["high_risk_auto_changed"] == 0),
        ("Most actionable changes are price increases",
         a["share_increases"] > 0.5, b["share_increases"] > 0.5),
        ("The model-internal value range is positive at its low end",
         a["uplift_low"] > 0, b["uplift_low"] > 0),
    ]
    replicated = sum(x and y for _, x, y in checks)

    payload = {
        "generated_at_utc": utc_now(),
        "same_decision_settings": same_settings,
        "base": a, "second": b,
        "checks": [{"finding": f, "base": bool(x), "second": bool(y)} for f, x, y in checks],
        "replicated": replicated, "n_checks": len(checks),
        "label": "two categories from one retailer: a robustness check, not proof of generality",
    }
    write_json(base_cfg.path("metrics_dir") / "second_category.json", payload)

    def pct(x, d=1):
        return f"{100 * x:.{d}f}%"

    def spct(x, d=1):
        return f"{100 * x:+.{d}f}%"

    def el(x):
        return f"{x:.2f}"

    rows = [
        ("**Data**", "", ""),
        ("canonical rows (products x stores x weeks)", f"{a['canonical_rows']:,}", f"{b['canonical_rows']:,}"),
        ("raw rows removed by the validity rules", pct(a["share_rows_removed"]), pct(b["share_rows_removed"])),
        ("products / stores", f"{a['upcs']} / {a['stores']}", f"{b['upcs']} / {b['stores']}"),
        ("gross margin", f"{a['gross_margin_pct']:.1f}%", f"{b['gross_margin_pct']:.1f}%"),
        ("series eligible for pricing", pct(a["share_series_eligible"]), pct(b["share_series_eligible"])),
        ("**Price sensitivity**", "", ""),
        ("naive elasticity", el(a["elasticity_naive"]), el(b["elasticity_naive"])),
        ("with product x store fixed effects", el(a["elasticity_upc_store_fe"]), el(b["elasticity_upc_store_fe"])),
        ("with controls (promotion, season, trend)", el(a["elasticity_controlled"]), el(b["elasticity_controlled"])),
        ("pooled, training weeks only (used for pricing)",
         f"{el(a['elasticity_pooled_training'])} [{el(a['elasticity_pooled_ci'][0])}, {el(a['elasticity_pooled_ci'][1])}]",
         f"{el(b['elasticity_pooled_training'])} [{el(b['elasticity_pooled_ci'][0])}, {el(b['elasticity_pooled_ci'][1])}]"),
        ("products with a usable own estimate", f"{a['products_usable']} of {a['products']}",
         f"{b['products_usable']} of {b['products']}"),
        ("mean shrinkage weight", f"{a['mean_shrinkage_weight']:.2f}", f"{b['mean_shrinkage_weight']:.2f}"),
        ("native ML implied elasticity", el(a["native_ml_elasticity"]), el(b["native_ml_elasticity"])),
        ("**Demand model**", "", ""),
        ("selected model", a["selected_model"], b["selected_model"]),
        ("validation / test WAPE", f"{a['valid_wape']:.3f} / {a['test_wape']:.3f}",
         f"{b['valid_wape']:.3f} / {b['test_wape']:.3f}"),
        ("best naive baseline, validation WAPE", f"{a['best_naive_valid_wape']:.3f}", f"{b['best_naive_valid_wape']:.3f}"),
        ("**Decisions (standard policy, last week)**", "", ""),
        ("change / review / keep", f"{pct(a['share_actionable'])} / {pct(a['share_review'])} / {pct(a['share_keep'])}",
         f"{pct(b['share_actionable'])} / {pct(b['share_review'])} / {pct(b['share_keep'])}"),
        ("actionable changes that are increases", pct(a["share_increases"]), pct(b["share_increases"])),
        ("HIGH-risk contexts changed automatically", str(a["high_risk_auto_changed"]), str(b["high_risk_auto_changed"])),
        ("**What sets the price**", "", ""),
        ("final prices from the model's own optimum", pct(a["share_learned_signal"]), pct(b["share_learned_signal"])),
        ("final prices on a guardrail corner", pct(a["share_guardrail_corner"]), pct(b["share_guardrail_corner"])),
        ("constraint that binds first most often",
         f"`{a['most_binding_constraint']}` ({pct(a['most_binding_share'])})",
         f"`{b['most_binding_constraint']}` ({pct(b['most_binding_share'])})"),
        ("no-model rule within one grid step of the engine", pct(a["rule_within_one_step"]), pct(b["rule_within_one_step"])),
        ("**Value (model-internal estimate)**", "", ""),
        ("engine's own portfolio uplift", spct(a["uplift_engine"]), spct(b["uplift_engine"])),
        ("range across evidenced elasticities", f"{spct(a['uplift_low'])} to {spct(a['uplift_high'])}",
         f"{spct(b['uplift_low'])} to {spct(b['uplift_high'])}"),
        ("traffic drivers: revenue share", pct(a["traffic_revenue_share"]), pct(b["traffic_revenue_share"])),
        ("traffic drivers: share that get a change", pct(a["traffic_share_actionable"]), pct(b["traffic_share_actionable"])),
        ("gain given up by capping traffic drivers at +3%", pct(a["traffic_cap_gain_given_up"], 0),
         pct(b["traffic_cap_gain_given_up"], 0)),
    ]
    table = "\n".join(f"| {r[0]} | {r[1]} | {r[2]} |" for r in rows)
    check_rows = "\n".join(
        f"| {f} | {'yes' if x else 'no'} | {'yes' if y else 'no'} | "
        f"{'replicates' if x and y else 'does NOT replicate'} |" for f, x, y in checks)
    failed = [f for f, x, y in checks if not (x and y)]
    fail_txt = ("Every finding points the same way in both categories."
                if not failed else
                "Findings that do not hold in both: " + "; ".join(f.lower() for f in failed) + ".")

    value_note = ""
    if b["uplift_low"] <= 0 < a["uplift_low"]:
        value_note = f"""
**The one that does not hold matters.** In {A} the recommendations keep a
positive model-internal value under every evidenced elasticity
({spct(a['uplift_low'])} at worst). In {B} the low end is {spct(b['uplift_low'])}: if
shoppers are as price-sensitive as the steepest evidence says, the same price
increases earn nothing. The engine's own estimate there ({spct(b['uplift_engine'])}) is
also less than half the {A} figure. So "the recommendations are worth
something even in the worst case" is a {A} result, not a general one, which
is one more reason to measure with a pilot before any rollout.
"""

    report = f"""# 27 - Second category: do the {A} findings hold in {B}?

Generated by `python scripts/compare_categories.py --config {Path(args.config).name}` on
{payload['generated_at_utc'][:10]}. Both runs use the same code and
{'**identical** decision settings (policy profiles, risk bands, model, eligibility and elasticity settings)' if same_settings else '**DIFFERENT** decision settings, so the comparison is not like for like'}.
Only the input files differ.

> **What this is.** A robustness check: the unchanged pipeline on a second
> product category from the same retailer and years. It shows whether the
> headline findings are specific to {A}. It is not proof that they hold for
> other retailers, decades or markets, and every value figure is still a
> model-internal estimate.

## Answer first

**{replicated} of {len(checks)} headline findings point the same way in {B}.** {fail_txt}

The central one: in {A}, {pct(a['share_learned_signal'])} of final prices come
from the model's own optimum; in {B} it is {pct(b['share_learned_signal'])}.
A rule that ignores demand lands within one 5-cent step of the engine in
{pct(a['rule_within_one_step'])} of {A} contexts and {pct(b['rule_within_one_step'])}
of {B} contexts. The description "rule-bounded pricing with a learned
direction" therefore fits both.
{value_note}
## Findings

| finding | {A} | {B} | verdict |
| --- | --- | --- | --- |
{check_rows}

## Side by side

| | {A} | {B} |
| --- | --- | --- |
{table}

## Reading the differences

* Numbers differ between categories, as they should: different products,
  margins and promotion habits. What matters is the direction of each finding.
* The constraint that binds first differs: `{a['most_binding_constraint']}` in {A},
  `{b['most_binding_constraint']}` in {B}. Which rule does the work depends on how
  much each category's prices have moved in the past.
* The crackers file contains 200 blank export rows (a UPC and nothing else),
  which the cereal file does not. The loader now reads them, and the cleaning
  step counts and drops them under their own rule (`blank_key_rows`); the
  cereal table is byte-identical to before that change.
* Row-level outputs of the second run stay in git-ignored folders
  (`data/processed/`, `artifacts_{b['category']}/`). Only this comparison is
  committed.
"""
    out = base_cfg.path("reports_dir") / "27_SECOND_CATEGORY.md"
    out.write_text(report, encoding="utf-8")
    print(f"{replicated} of {len(checks)} findings replicate in {B}")
    for f, x, y in checks:
        print(f"  {'OK ' if x and y else 'NO '} {f}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
