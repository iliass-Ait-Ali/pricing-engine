"""Phase L - prove that recommendations use only pre-decision cost information.

    python scripts/audit_cost_leakage.py

Three independent checks on the REAL data:

1. **Identity check** - for every series, is `decision_time_unit_cost` at week
   t exactly the implied AAC observed at the latest week strictly before t?
2. **Future-poisoning check** - corrupt the AAC of week t and every later week
   for a set of series, rebuild features from scratch, and verify that neither
   the decision-time cost nor the recommendation at weeks <= t changes.
3. **Recommendation-level check** - re-run the optimizer on poisoned features
   and compare the resulting recommendations field by field.

Outputs
-------
    reports/10_COST_LEAKAGE_AUDIT.md
    artifacts/metrics/cost_leakage_audit.json
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

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.data.loader import load_processed  # noqa: E402
from pricing_engine.features.build import build_feature_table  # noqa: E402
from pricing_engine.models.demand_model import load_model  # noqa: E402
from pricing_engine.models.hybrid import load_pricing_model  # noqa: E402
from pricing_engine.optimization.optimizer import optimize_price  # noqa: E402
from pricing_engine.utils.io import write_json  # noqa: E402

STAT_COLUMNS = ["n_obs", "n_distinct_prices", "price_cv", "price_min", "price_max"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-series", type=int, default=25, help="Series to poison.")
    args = parser.parse_args()

    cfg = load_config()
    t0 = time.time()
    canonical = load_processed(cfg=cfg)
    feats = pd.read_parquet(cfg.path("features_table"))
    stats_table = pd.read_csv(cfg.path("metrics_dir") / "price_variation_upc_store.csv")
    base = load_model(cfg.path("models_dir") / "demand_model.joblib", cfg=cfg)
    model = load_pricing_model(base, cfg=cfg)

    results: dict = {"price_response_method": model.method.value}

    # ---- check 1: identity against the previous observed week --------------
    print("[1/3] verifying decision_time_unit_cost == previous week's implied AAC ...")
    d = feats[["upc", "store", "week", "estimated_unit_aac", "decision_time_unit_cost"]].copy()
    d = d.sort_values(["upc", "store", "week"])
    g = d.groupby(["upc", "store"], observed=True)
    expected = g["estimated_unit_aac"].shift(1)
    expected = expected.groupby([d["upc"], d["store"]]).ffill()
    both = d["decision_time_unit_cost"].notna() & expected.notna()
    mismatch = both & ~np.isclose(
        d["decision_time_unit_cost"].to_numpy(dtype="float64"),
        expected.to_numpy(dtype="float64"),
        rtol=1e-5, atol=1e-6, equal_nan=True,
    )
    same_week = np.isclose(
        d["decision_time_unit_cost"].to_numpy(dtype="float64"),
        d["estimated_unit_aac"].to_numpy(dtype="float64"),
        rtol=1e-9, atol=1e-9, equal_nan=False,
    )
    results["identity_check"] = {
        "rows_compared": int(both.sum()),
        "mismatches": int(mismatch.sum()),
        "share_equal_to_same_week_aac": float(np.mean(same_week)),
        "passed": bool(mismatch.sum() == 0),
    }
    print(f"      {int(both.sum()):,} rows compared, {int(mismatch.sum())} mismatches")

    # ---- check 2 + 3: poison the future, rebuild, re-recommend --------------
    print(f"[2/3] poisoning future cost for {args.n_series} series and rebuilding features ...")
    latest_week = int(feats["week"].max())
    target_week = latest_week - 1  # poison from this week onward, inclusive

    eligible = stats_table[stats_table["eligible"]].sample(
        args.n_series, random_state=cfg.seed
    )[["upc", "store"]]
    keys = set(map(tuple, eligible.to_numpy()))

    subset = canonical[
        canonical.apply(lambda r: (r["upc"], r["store"]) in keys, axis=1)
    ].copy() if len(canonical) < 200_000 else canonical.merge(eligible, on=["upc", "store"], how="inner")

    clean_features = build_feature_table(subset, cfg=cfg)

    poisoned_input = subset.copy()
    mask = poisoned_input["week"] >= target_week
    poisoned_input.loc[mask, "estimated_unit_aac"] = 999.0
    poisoned_input.loc[mask, "gross_margin_rate"] = -9.0
    poisoned_input.loc[mask, "gross_profit"] = -12345.0
    poisoned_features = build_feature_table(poisoned_input, cfg=cfg)

    before = clean_features[clean_features["week"] <= target_week]
    after = poisoned_features[poisoned_features["week"] <= target_week]
    cost_before = before.sort_values(["upc", "store", "week"])["decision_time_unit_cost"].to_numpy()
    cost_after = after.sort_values(["upc", "store", "week"])["decision_time_unit_cost"].to_numpy()
    cost_unchanged = bool(np.allclose(cost_before, cost_after, equal_nan=True))

    # the week AFTER the poisoned week must see it (proves the test has teeth)
    nxt_clean = clean_features[clean_features["week"] == target_week + 1].sort_values(["upc", "store"])
    nxt_poison = poisoned_features[poisoned_features["week"] == target_week + 1].sort_values(["upc", "store"])
    downstream_changed = bool(
        len(nxt_clean)
        and not np.allclose(
            nxt_clean["decision_time_unit_cost"].to_numpy(dtype="float64"),
            nxt_poison["decision_time_unit_cost"].to_numpy(dtype="float64"),
            equal_nan=True,
        )
    )
    results["poisoning_check"] = {
        "series_poisoned": int(len(eligible)),
        "poisoned_from_week": int(target_week),
        "rows_compared_before_poison": int(len(before)),
        "decision_time_cost_unchanged_at_or_before_poison": cost_unchanged,
        "next_week_cost_changed_as_expected": downstream_changed,
        "passed": bool(cost_unchanged and downstream_changed),
    }
    print(f"      earlier costs unchanged: {cost_unchanged} | next week reacts: {downstream_changed}")

    # ---- check 3: recommendations -----------------------------------------
    print("[3/3] re-running recommendations on poisoned features ...")
    compare_week = target_week - 1
    fields = [
        "proposed_candidate_price", "final_recommended_price", "decision", "actionable",
        "predicted_units_recommended",
        "expected_gross_profit_recommended", "unit_cost_used", "risk_level",
        "model_internal_estimated_profit_uplift_pct",
    ]
    diffs = 0
    compared = 0
    for _, key in eligible.iterrows():
        upc, store = int(key["upc"]), int(key["store"])
        st = stats_table[(stats_table["upc"] == upc) & (stats_table["store"] == store)]
        series_stats = st.iloc[0][STAT_COLUMNS].to_dict() if len(st) else {}
        row_clean = clean_features[
            (clean_features["upc"] == upc) & (clean_features["store"] == store)
            & (clean_features["week"] == compare_week)
        ]
        row_poison = poisoned_features[
            (poisoned_features["upc"] == upc) & (poisoned_features["store"] == store)
            & (poisoned_features["week"] == compare_week)
        ]
        if row_clean.empty or row_poison.empty:
            continue
        compared += 1
        rec_clean = optimize_price(model, row_clean.head(1), cfg=cfg, series_stats=series_stats)
        rec_poison = optimize_price(model, row_poison.head(1), cfg=cfg, series_stats=series_stats)
        a, b = rec_clean.as_dict(), rec_poison.as_dict()
        if any(a[f] != b[f] for f in fields):
            diffs += 1

    results["recommendation_check"] = {
        "compare_week": int(compare_week),
        "series_compared": compared,
        "recommendations_changed_by_future_cost": diffs,
        "passed": bool(diffs == 0),
    }
    print(f"      {compared} recommendations compared, {diffs} changed")

    results["all_passed"] = all(
        results[k]["passed"] for k in ("identity_check", "poisoning_check", "recommendation_check")
    )
    results["runtime_seconds"] = round(time.time() - t0, 1)
    write_json(cfg.path("metrics_dir") / "cost_leakage_audit.json", results)

    ic, pc, rc = results["identity_check"], results["poisoning_check"], results["recommendation_check"]
    report = f"""# 10 - Decision-time cost leakage audit (Phase L)

Generated by `python scripts/audit_cost_leakage.py --n-series {args.n_series}`.

Gross-profit optimization needs a unit cost. The only cost a pricing analyst
actually has when setting next week's price is the **latest known** implied
AAC - never the accounting margin that will be recorded for the week being
priced. This audit proves the pipeline honours that.

## The rule under test

```text
decision_time_unit_cost(u, s, t) = estimated_unit_aac(u, s, t-1),
                                   forward-filled within the series
```

## Check 1 - identity against the previous observed week

| metric | value |
| --- | --- |
| rows compared | {ic['rows_compared']:,} |
| mismatches vs the previous week's implied AAC | **{ic['mismatches']}** |
| rows where the decision cost coincidentally equals the SAME week's AAC | {100*ic['share_equal_to_same_week_aac']:.4f}% |
| verdict | **{'PASS' if ic['passed'] else 'FAIL'}** |

The third row matters: a naive implementation would set the decision cost equal
to the current week's AAC, and that share would be ~100%. Here it is
essentially zero, which is what a correctly lagged cost looks like.

## Check 2 - future poisoning of the cost signal

{pc['series_poisoned']} eligible series were rebuilt twice from the canonical
table: once clean, once with `estimated_unit_aac`, `gross_margin_rate` and
`gross_profit` corrupted from week **{pc['poisoned_from_week']}** onward.

| metric | value |
| --- | --- |
| feature rows compared at weeks <= the poisoned week | {pc['rows_compared_before_poison']:,} |
| decision-time cost unchanged at or before the poisoned week | **{pc['decision_time_cost_unchanged_at_or_before_poison']}** |
| the following week's cost does react (test has teeth) | **{pc['next_week_cost_changed_as_expected']}** |
| verdict | **{'PASS' if pc['passed'] else 'FAIL'}** |

Both halves are required. If the first were false there would be leakage; if
the second were false the test would be vacuous.

## Check 3 - recommendation level

Recommendations for week **{rc['compare_week']}** (before the poisoning point)
were regenerated from both feature tables with the production pricing model
(`{results['price_response_method']}` price response) and compared field by
field: recommended price, decision state, actionability, predicted units,
expected gross profit, cost used, risk level and internal uplift.

| metric | value |
| --- | --- |
| series compared | {rc['series_compared']} |
| recommendations altered by future cost information | **{rc['recommendations_changed_by_future_cost']}** |
| verdict | **{'PASS' if rc['passed'] else 'FAIL'}** |

## Overall

**{'ALL CHECKS PASS' if results['all_passed'] else 'FAILURES PRESENT - see above'}**
(runtime {results['runtime_seconds']}s).

Related unit tests (run on every `pytest`): `tests/test_cost_leakage.py` and
`tests/test_features.py::test_decision_time_cost_is_lagged_not_contemporaneous`.

## Residual caveats

* The audit proves *temporal* availability, not economic correctness: AAC is an
  accounting average acquisition cost and can differ from replacement cost
  (`docs/DATA_DICTIONARY.md`, and the Kilts manual's own discussion of sluggish
  AAC adjustment and forward buying).
* When no prior AAC exists at all, the optimizer refuses gross-profit
  optimization and returns `COST_UNAVAILABLE` rather than inventing a cost.
* Cost is held fixed across the candidate price grid; that separate invariant is
  covered by `tests/test_simulation.py::test_cost_is_held_fixed_across_price_grid`.
"""
    out = cfg.path("reports_dir") / "10_COST_LEAKAGE_AUDIT.md"
    out.write_text(report, encoding="utf-8")
    print(f"\nwrote {out}")
    print(f"ALL PASSED: {results['all_passed']}")
    return 0 if results["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
