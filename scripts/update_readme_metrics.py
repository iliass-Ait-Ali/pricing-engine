"""Write README's headline numbers from the generated artifacts.

    python scripts/update_readme_metrics.py            # rewrite the block
    python scripts/update_readme_metrics.py --check    # fail if it is stale
    python scripts/update_readme_metrics.py --refresh-tests   # re-collect tests

Documentation drift - a number edited in one place and not another - was a real
problem during this project's development. This script removes that failure
mode for the headline numeric results: everything between

    <!-- BEGIN GENERATED METRICS -->
    <!-- END GENERATED METRICS -->

in README.md is written from ``artifacts/metrics/*.json``, which are themselves
written by the pipeline. Prose outside that block is never touched.

Each metric has exactly ONE authoritative artifact, listed in
``METRIC_SOURCES`` and printed in the generated block itself.

Deliberately excluded: the model-internal estimated profit uplift. It is a
self-scored offline simulation, not business impact, so it is not a headline
number - see reports/12_MODEL_VALUE_ABLATION.md and §54 of the full report.

``tests/test_readme_metrics.py`` fails when README and the artifacts disagree.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

METRICS_DIR = REPO_ROOT / "artifacts" / "metrics"
README = REPO_ROOT / "README.md"

BEGIN = "<!-- BEGIN GENERATED METRICS -->"
END = "<!-- END GENERATED METRICS -->"

TEST_SUITE_ARTIFACT = METRICS_DIR / "test_suite.json"

# One authoritative artifact per metric family. Printed in the README block so
# a reader can go straight to the file that owns the number.
METRIC_SOURCES = {
    "build": "artifacts/metrics/build_audit.json",
    "dataset": "artifacts/metrics/dataset_fingerprint.json",
    "coverage": "artifacts/metrics/eda_summary.json",
    "validation": "artifacts/metrics/data_validation.json",
    "model": "artifacts/metrics/model_metrics.json + evaluation.json",
    "elasticity_ladder": "artifacts/metrics/elasticity.json",
    "elasticity": "artifacts/metrics/elasticity_estimation.json",
    "funnel": "artifacts/metrics/elasticity_funnel.json",
    "out_of_time": "artifacts/metrics/out_of_time_price_response.json",
    "recommendations": "artifacts/metrics/recommendations.json",
    "attribution": "artifacts/metrics/constraint_attribution.json",
    "price_response": "artifacts/metrics/price_response.json",
    "backtest": "artifacts/metrics/backtest.json",
    "batch": "artifacts/metrics/batch_performance.json",
    "tests": "artifacts/metrics/test_suite.json",
}


class MetricsError(RuntimeError):
    """Raised when an authoritative artifact is missing or unreadable."""


def load(name: str) -> dict:
    path = METRICS_DIR / name
    if not path.exists():
        raise MetricsError(
            f"missing authoritative artifact {path.relative_to(REPO_ROOT)} - "
            "run the pipeline stage that produces it before updating README."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def collect_test_count() -> int:
    """Number of tests pytest collects, from pytest itself."""
    # `-o addopts=` neutralises the project's own `-q`, so pytest prints the
    # single "N tests collected" summary line rather than a per-file tally.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-o", "addopts=", "--collect-only", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.endswith("tests collected") or " tests collected" in line:
            return int(line.split()[0])
        if line.endswith("test collected"):
            return int(line.split()[0])
    raise MetricsError(f"could not parse the pytest collection output:\n{proc.stdout[-2000:]}")


def refresh_test_suite_artifact() -> dict:
    payload = {
        "collected_tests": collect_test_count(),
        "command": "python -m pytest -o addopts= --collect-only -q",
    }
    TEST_SUITE_ARTIFACT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def model_row(name: str, result: dict, selected: str) -> str:
    """One row of the model-comparison table (a model may have no test score)."""
    valid = f"{result['valid']['wape']:.4f}"
    test = f"{result['test']['wape']:.4f}" if "test" in result else ""
    if name == selected:
        return f"| **{name} (selected)** | **{valid}** | **{test}** |"
    return f"| {name} | {valid} | {test} |"


def pct(x: float, digits: int = 1) -> str:
    return f"{100 * float(x):.{digits}f}%"


def build_block() -> str:
    build = load("build_audit.json")
    fingerprint = load("dataset_fingerprint.json")
    eda = load("eda_summary.json")
    validation = load("data_validation.json")
    models = load("model_metrics.json")
    evaluation = load("evaluation.json")
    ladder = load("elasticity.json")
    elasticity = load("elasticity_estimation.json")
    funnel = load("elasticity_funnel.json")
    out_of_time = load("out_of_time_price_response.json")
    recs = load("recommendations.json")
    attribution = load("constraint_attribution.json")
    price_response = load("price_response.json")
    backtest = load("backtest.json")
    batch = load("batch_performance.json")
    tests = json.loads(TEST_SUITE_ARTIFACT.read_text(encoding="utf-8"))

    scale = eda["scale"]
    selected = models["selected"]
    valid_wape = models["results"][selected]["valid"]["wape"]
    test_wape = evaluation["test_metrics"]["wape"]
    checks_passed = sum(1 for c in validation["checks"] if c["passed"])
    binding = attribution["first_binding_constraint_counts"]
    top_binding, top_binding_n = max(
        ((k, v) for k, v in binding.items() if k != "NONE"), key=lambda kv: kv[1]
    )
    episodes = out_of_time["results"]["all episodes"]
    determinants = attribution["final_determinant_counts"]
    n_ctx = attribution["n_contexts"]

    lines = [
        BEGIN,
        "",
        "<!-- Written by scripts/update_readme_metrics.py from the generated",
        "     artifacts. Do not edit by hand: tests/test_readme_metrics.py fails",
        "     when this block and the artifacts disagree. -->",
        "",
        "**Data** — source of truth: "
        f"`{METRIC_SOURCES['build']}`, `{METRIC_SOURCES['dataset']}`, "
        f"`{METRIC_SOURCES['coverage']}`, `{METRIC_SOURCES['validation']}`",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| raw rows → canonical rows | {build['raw_rows']:,} → {fingerprint['rows']:,} |",
        "| excluded | "
        + "; ".join(
            f"`{e['rule']}`: {e['rows_removed']:,} ({e['pct_of_raw']:.2f}%)"
            for e in build["exclusions"]
            if e["rows_removed"]
        )
        + " |",
        f"| coverage | {scale['upcs']} UPCs × {scale['stores']} stores × "
        f"{scale['weeks']} weeks, {scale['date_min']} .. {scale['date_max']} |",
        f"| observed revenue / gross profit | ${scale['total_revenue']:,.0f} / "
        f"${scale['total_gross_profit']:,.0f} ({scale['overall_gross_margin_pct']:.2f}%) |",
        f"| formula + structural checks | {checks_passed} / {len(validation['checks'])} pass |",
        f"| dataset SHA-256 (parquet) | `{fingerprint['parquet_sha256'][:16]}…` |",
        "",
        f"**Demand model** — source of truth: `{METRIC_SOURCES['model']}`",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| selected model | **{selected}** (`{evaluation['model_version']}`) |",
        f"| chronological split | train {evaluation['split']['train_weeks'][0]}-"
        f"{evaluation['split']['train_weeks'][1]}, validation "
        f"{evaluation['split']['valid_weeks'][0]}-{evaluation['split']['valid_weeks'][1]}, "
        f"test {evaluation['split']['test_weeks'][0]}-{evaluation['split']['test_weeks'][1]} |",
        f"| validation WAPE | **{valid_wape:.4f}** |",
        f"| test WAPE | **{test_wape:.4f}** (MAE {evaluation['test_metrics']['mae']:.3f}, "
        f"RMSE {evaluation['test_metrics']['rmse']:.3f}) |",
        "",
        "| model | validation WAPE | test WAPE |",
        "| --- | --- | --- |",
        *[model_row(name, r, selected) for name, r in models["results"].items()],
        "",
        f"Native ML price-response validation on {price_response['summary']['n_contexts']} "
        f"decision contexts (`{METRIC_SOURCES['price_response']}`): "
        f"{pct(price_response['summary']['share_monotone_decreasing'], 0)} monotone "
        f"decreasing demand curves, "
        f"{pct(price_response['summary']['share_flat_response'], 0)} flat, "
        f"{pct(price_response['summary']['share_negative_predicted_units'], 0)} negative "
        f"predictions, median implied elasticity "
        f"**{price_response['summary']['median_local_elasticity']:.2f}** - steeper than the "
        "controlled econometric estimate, which is why the optimizer is deliberately "
        "conservative.",
        "",
        f"**Price response** — source of truth: `{METRIC_SOURCES['elasticity']}`, "
        f"`{METRIC_SOURCES['funnel']}`, `{METRIC_SOURCES['out_of_time']}`",
        "",
        "| specification (`" + METRIC_SOURCES["elasticity_ladder"] + "`) | elasticity |",
        "| --- | --- |",
        *[
            f"| {m['model'][3:] if m['model'][:2] == 'M' + str(i + 1) else m['model']} | "
            f"**{m['price_elasticity']:.3f}** |"
            for i, m in enumerate(ladder["loglog"])
        ],
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| pooled controlled elasticity (training weeks only) | "
        f"**{elasticity['pooled_elasticity']:.3f}** |",
        f"| two-way clustered (UPC, week) 95% CI | "
        f"[{elasticity['pooled_ci_low_two_way']:.3f}, {elasticity['pooled_ci_high_two_way']:.3f}] |",
        f"| UPC × store fixed-effects specification | {elasticity['fe_elasticity']:.3f} |",
        f"| products with a usable own estimate | "
        f"{elasticity['n_products_usable']} of {elasticity['n_products']} "
        f"({funnel['funnel'][0]['n_upcs']} UPCs entered the funnel) |",
        f"| empirical-Bayes τ² (REML) | {elasticity['tau2_between_product_variance']:.3f} |",
        f"| mean shrinkage weight | {elasticity['mean_shrinkage_weight']:.2f} |",
        f"| out-of-time price-change episodes (weeks "
        f"{out_of_time['evaluation_weeks'][0]}-{out_of_time['evaluation_weeks'][1]}) | "
        f"{out_of_time['n_episodes']:,} |",
        f"| observed implied elasticity out of time (all / no recorded promotion) | "
        f"{out_of_time['median_observed_implied_elasticity']:.2f} / "
        f"{out_of_time['median_observed_implied_elasticity_no_promo']:.2f} |",
        f"| WAPE on those episodes: null / pooled / shrunk / native ML | "
        f"{episodes['null (baseline only, epsilon = 0)']['wape']:.4f} / "
        f"{episodes['pooled elasticity']['wape']:.4f} / "
        f"{episodes['shrunk product elasticity']['wape']:.4f} / "
        f"{episodes['native ML price response']['wape']:.4f} |",
        "",
        f"**Recommendations** — source of truth: `{METRIC_SOURCES['recommendations']}` "
        f"(decision week {recs['decision_week']}, `{recs['policy_profile']}` profile, "
        f"`{recs['price_response_method']}` price response, {recs['n_contexts']:,} contexts)",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| `RECOMMEND_CHANGE` (actionable) | **{pct(recs['share_actionable'])}** |",
        f"| `REVIEW_REQUIRED` (escalated to a human) | {pct(recs['share_review_required'])} |",
        f"| `KEEP_CURRENT` | {pct(recs['share_keep_current'])} |",
        f"| median absolute price change (actionable) | "
        f"{pct(recs['median_abs_price_change_pct'])} |",
        f"| HIGH-risk contexts that auto-changed a price | "
        f"**{recs['high_risk_actionable']}** of {recs['high_risk_contexts']:,} |",
        "| elasticity provenance | "
        + ", ".join(f"{k} {v:,}" for k, v in recs["elasticity_source_counts"].items())
        + " |",
        "",
        f"**What actually decides the price** — source of truth: "
        f"`{METRIC_SOURCES['attribution']}` (all "
        f"{attribution['n_contexts']:,} contexts of week {attribution['decision_week']})",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| final recommendations set by an interior learned optimum | "
        f"**{pct(attribution['share_determined_by_learned_signal'])}** "
        f"({determinants['learned signal (interior optimum, actionable)']:,} contexts) |",
        f"| set by a guardrail corner | "
        f"{pct(determinants['guardrail corner (optimum outside the feasible interval)'] / n_ctx)} |",
        f"| screened out before optimisation | "
        f"{pct(determinants['screened out before optimisation (eligibility / cost)'] / n_ctx)} |",
        f"| risk-gated / below materiality | "
        f"{pct(determinants['risk gate (REVIEW_REQUIRED, not actionable)'] / n_ctx)} / "
        f"{pct(determinants['materiality threshold (KEEP_CURRENT)'] / n_ctx)} |",
        f"| unconstrained optimum inside the feasible bounds | "
        f"{pct(attribution['share_unconstrained_optimum_inside_bounds'])} |",
        f"| most frequently first-binding constraint | `{top_binding}` "
        f"({pct(top_binding_n / attribution['n_contexts'])} of contexts) |",
        "",
        f"**Offline policy backtest** — source of truth: `{METRIC_SOURCES['backtest']}` "
        f"(weeks {backtest['weeks'][0]}-{backtest['weeks'][-1]}, "
        f"`{backtest['policy_profile']}` profile)",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| mean weekly WAPE | {backtest['accuracy_anchor']['weekly_wape_mean']:.4f} |",
        *[
            f"| {t['policy']}: model-internal estimated gross profit vs historical pricing | "
            f"{t['model_internal_estimated_gp_vs_historical_pct']:+.2f}% "
            f"(prices unchanged {pct(t['share_no_change'])}) |"
            for t in backtest["policy_totals"]
            if t["policy"] != "HistoricalPricePolicy"
        ],
        "",
        f"**Engineering** — source of truth: `{METRIC_SOURCES['tests']}`, "
        f"`{METRIC_SOURCES['batch']}`",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| tests collected | **{tests['collected_tests']}** |",
        f"| batch scoring, {batch['n_contexts']:,} contexts | "
        f"per-context loop {batch['per_context_loop_seconds']:.1f}s → vectorised "
        f"**{batch['vectorised_batch_seconds']:.1f}s** ({batch['speedup']:.0f}× on this machine) |",
        f"| batch equivalence (discrete fields exact, floats ≤ {batch['equivalence']['tolerance']:g}) | "
        f"{'PASS' if batch['equivalence']['equivalent'] else 'FAIL'} "
        f"on {batch['equivalence']['n_recommendations']:,} recommendations |",
        "",
        END,
    ]
    return "\n".join(lines)


def apply_block(text: str, block: str) -> str:
    if BEGIN not in text or END not in text:
        raise MetricsError(
            f"README.md must contain the {BEGIN} / {END} markers before this script can run."
        )
    head = text.split(BEGIN)[0]
    tail = text.split(END, 1)[1]
    return head + block + tail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="Exit non-zero if README is out of date; write nothing.")
    parser.add_argument("--refresh-tests", action="store_true",
                        help="Re-collect the test count into artifacts/metrics/test_suite.json.")
    args = parser.parse_args()

    if args.refresh_tests or not TEST_SUITE_ARTIFACT.exists():
        payload = refresh_test_suite_artifact()
        print(f"collected tests: {payload['collected_tests']}")

    block = build_block()
    current = README.read_text(encoding="utf-8")
    updated = apply_block(current, block)

    if args.check:
        if updated != current:
            print("README generated metrics are STALE - run scripts/update_readme_metrics.py")
            return 1
        print("README generated metrics match the artifacts.")
        return 0

    if updated == current:
        print("README generated metrics already match the artifacts.")
        return 0
    README.write_text(updated, encoding="utf-8")
    print(f"updated the generated metrics block in {README.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
