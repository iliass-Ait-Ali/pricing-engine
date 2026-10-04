"""Cross-document numeric consistency audit.

    python scripts/audit_metric_consistency.py [--strict]

One metric, one source of truth. This audit reads each headline number from the
artifact that owns it, then scans the hand-authored Markdown documents for
sentences that state that metric and checks the number they state.

It is deliberately *anchored*: a check only fires on a line that names the
metric, so it compares like with like instead of hunting for loose digits. A
mismatch is a documentation-drift defect and must be traced to its source, not
silently edited away.

Two categories of line are not evidence about the current value and are
excluded by design:

* **corrections** - "mean weight 0.954 -> 0.780", "was 0.95 before the audit".
  Deleting the superseded number would hide the correction, which this project
  explicitly does not do.
* **generated reports** - `reports/01..20` are rewritten from the artifacts by
  their pipeline scripts on every run, so they cannot drift.
* **verbatim transcripts** - a fenced block directly preceded by the marker
  ``<!-- metric-audit: verbatim-transcript -->`` is captured console output
  from a dated run. Editing it to match today's value would fabricate output
  that the tool never printed, so the whole block is skipped instead.

Outputs
-------
    artifacts/metrics/metric_consistency.json

``tests/test_metric_consistency.py`` fails the build on any mismatch.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.utils.io import write_json  # noqa: E402

METRICS_DIR = REPO_ROOT / "artifacts" / "metrics"

#: Hand-authored documents only (see the module docstring).
SCAN_GLOBS = (
    "README.md",
    "STATUS.md",
    "ROADMAP.md",
    "DECISIONS.md",
    "KNOWN_LIMITATIONS.md",
    "FUTURE_WORK.md",
    "docs/*.md",
    "reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md",
    "reports/VALIDATION_SUMMARY.md",
    "reports/MONITORING_DESIGN.md",
)

#: Documents whose subject matter IS other documents' wrong numbers (drift
#: registers, the claim audit, the QA record).
SKIP_FILES = {
    "20_CLAIM_AUDIT.md",
    "21_FINAL_ENGINEERING_CLOSURE.md",
    "AI_PRICING_REVENUE_OPTIMIZATION_REPORT_SOURCES.md",
    "REPORT_QA.md",
    "Claude Code Prompt — Ultra-Detailed Full Project Report.md",
}

#: A line that records a correction states the superseded value on purpose.
TRANSITION = re.compile(
    r"->|→|"
    r"\b(?:moved|went|fell|rose|changed)\b.{0,40}\bto\b|\bfrom\b.{0,30}\bto\b|"
    r"\bwas\b|\bbefore the\b|"
    r"\bat Phase [A-Z]\b|\bPhase L\b|\bsuperseded\b|\bpreviously\b|\bused to\b|"
    r"\bno longer\b|\bcorrected\b|\bstale\b|\bdrift\b",
    re.IGNORECASE,
)

MINUS = "[-−–]"

#: Marks the next fenced block as captured console output (see the docstring).
VERBATIM_MARKER = "<!-- metric-audit: verbatim-transcript -->"


def scannable_lines(text: str) -> list[tuple[int, str]]:
    """(line number, line) pairs, minus fenced blocks marked as verbatim."""
    out: list[tuple[int, str]] = []
    armed = in_verbatim = False
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if in_verbatim:
            if stripped.startswith("```"):
                in_verbatim = False
            continue
        if stripped == VERBATIM_MARKER:
            armed = True
            continue
        if armed and stripped.startswith("```"):
            armed = False
            in_verbatim = True
            continue
        if stripped:
            armed = False
        out.append((lineno, line))
    return out


def artifact(name: str) -> dict:
    path = METRICS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"missing authoritative artifact {path}")
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class Check:
    """One metric, its owning artifact, and how documents state it."""

    name: str
    source: str
    expected: float
    tolerance: float
    patterns: tuple[str, ...]
    unit: str = ""
    #: Lines matching this are about something else with a similar shape.
    exclude: str | None = None
    #: Set False for a metric that was never corrected, so a "->" on the line
    #: is not a reason to ignore it.
    allow_transitions: bool = True
    hits: list[dict] = field(default_factory=list)

    def compiled(self) -> list[re.Pattern]:
        return [re.compile(p, re.IGNORECASE) for p in self.patterns]

    def skip_line(self, line: str) -> bool:
        if self.exclude and re.search(self.exclude, line, re.IGNORECASE):
            return True
        return bool(self.allow_transitions and TRANSITION.search(line))


def parse_number(text: str) -> float:
    return float(text.replace(",", "").replace("−", "-").replace("–", "-"))


def build_checks() -> list[Check]:
    fingerprint = artifact("dataset_fingerprint.json")
    eda = artifact("eda_summary.json")
    models = artifact("model_metrics.json")
    evaluation = artifact("evaluation.json")
    elasticity = artifact("elasticity_estimation.json")
    funnel = artifact("elasticity_funnel.json")
    out_of_time = artifact("out_of_time_price_response.json")
    recs = artifact("recommendations.json")
    attribution = artifact("constraint_attribution.json")
    comparison = artifact("price_response_comparison.json")
    tests = artifact("test_suite.json")
    selected = models["selected"]
    pairs = {p["pair"]: p for p in comparison["pairwise_disagreement"]}
    determinants = attribution["final_determinant_counts"]

    checks = [
        Check(
            name="canonical rows",
            source="dataset_fingerprint.json:rows",
            expected=float(fingerprint["rows"]),
            tolerance=0.5,
            patterns=(
                r"canonical[^0-9]{0,40}(4,\d{3},\d{3})",
                r"(4,\d{3},\d{3})\s*(?:canonical\s*)?UPC\s*[x×]\s*store",
                r"(4,\d{3},\d{3})\s*rows in the (?:canonical|processed) (?:panel|table)",
            ),
        ),
        Check(
            name="observed revenue",
            source="eda_summary.json:scale.total_revenue",
            expected=eda["scale"]["total_revenue"],
            tolerance=1.0,
            patterns=(r"\$(262,\d{3},\d{3})",),
        ),
        Check(
            name="observed gross profit",
            source="eda_summary.json:scale.total_gross_profit",
            expected=eda["scale"]["total_gross_profit"],
            tolerance=1.0,
            patterns=(r"\$(40,\d{3},\d{3})",),
        ),
        Check(
            name="validation WAPE (selected model)",
            source=f"model_metrics.json:results[{selected}].valid.wape",
            expected=models["results"][selected]["valid"]["wape"],
            tolerance=0.0001,
            patterns=(r"validation WAPE[^0-9]{0,40}(0\.\d{3,4})",),
            exclude=r"baseline|naive|M0|HGB|M2",
        ),
        Check(
            name="test WAPE (selected model)",
            source="evaluation.json:test_metrics.wape",
            expected=evaluation["test_metrics"]["wape"],
            tolerance=0.0001,
            patterns=(r"test WAPE[^0-9]{0,40}(0\.\d{3,4})",),
            exclude=r"baseline|naive|rolling-mean|M0|HGB|M2|backtest|weekly",
        ),
        Check(
            name="pooled elasticity",
            source="elasticity_estimation.json:pooled_elasticity",
            expected=elasticity["pooled_elasticity"],
            tolerance=0.0006,
            patterns=(
                rf"pooled (?:controlled )?elasticity[^0-9{MINUS}]{{0,40}}({MINUS}\d\.\d{{2,3}})",
            ),
            exclude=r"naive|fixed effect",
        ),
        Check(
            name="two-way clustered CI, lower bound",
            source="elasticity_estimation.json:pooled_ci_low_two_way",
            expected=elasticity["pooled_ci_low_two_way"],
            tolerance=0.006,
            patterns=(
                rf"two-way clustered.{{0,80}}\[\s*({MINUS}\d\.\d{{2,3}})\s*,",
                rf"clustered \(UPC, week\).{{0,60}}\[\s*({MINUS}\d\.\d{{2,3}})\s*,",
            ),
        ),
        Check(
            name="two-way clustered CI, upper bound",
            source="elasticity_estimation.json:pooled_ci_high_two_way",
            expected=elasticity["pooled_ci_high_two_way"],
            tolerance=0.006,
            patterns=(
                rf"two-way clustered.{{0,80}}\[\s*{MINUS}\d\.\d{{2,3}}\s*,\s*"
                rf"({MINUS}\d\.\d{{2,3}})\s*\]",
                rf"clustered \(UPC, week\).{{0,60}}\[\s*{MINUS}\d\.\d{{2,3}}\s*,\s*"
                rf"({MINUS}\d\.\d{{2,3}})\s*\]",
            ),
        ),
        Check(
            name="usable product elasticities",
            source="elasticity_estimation.json:n_products_usable",
            expected=float(elasticity["n_products_usable"]),
            tolerance=0.5,
            patterns=(
                r"(\d{3})\s*(?:of|/)\s*372\b",
                r"usable own (?:estimate|elasticity)[^0-9]{0,40}(\d{3})\b",
            ),
            # "133 of 372 products have NO usable own elasticity" is the
            # complement, checked separately.
            exclude=(r"\bno usable\b|\bnot usable\b|\bwithout a usable\b|"
                     r"\black a usable\b|pooled fallback"),
        ),
        Check(
            name="products without a usable own elasticity",
            source="elasticity_estimation.json:n_products - n_products_usable",
            expected=float(elasticity["n_products"] - elasticity["n_products_usable"]),
            tolerance=0.5,
            patterns=(r"(\d{3})\s*(?:of|/)\s*372[^.]{0,60}no usable",),
        ),
        Check(
            name="products entering per-UPC estimation",
            source="elasticity_estimation.json:n_products",
            expected=float(elasticity["n_products"]),
            tolerance=0.5,
            patterns=(
                r"\d{3}\s*(?:of|/)\s*(\d{3})\s*products",
                r"(\d{3}) products (?:observed )?in the training weeks",
            ),
        ),
        Check(
            name="UPCs entering the elasticity funnel",
            source="elasticity_funnel.json:funnel[0].n_upcs",
            expected=float(funnel["funnel"][0]["n_upcs"]),
            tolerance=0.5,
            patterns=(r"(\d{3}) UPCs? (?:->|→) 239",),
            allow_transitions=False,
        ),
        Check(
            name="empirical-Bayes tau squared",
            source="elasticity_estimation.json:tau2_between_product_variance",
            expected=elasticity["tau2_between_product_variance"],
            tolerance=0.0006,
            patterns=(r"(?:τ²|tau\^?2|tau squared)[^0-9]{0,30}(0\.\d{2,4})",),
            exclude=r"method of moments|moment|less than|Phase L|variant",
        ),
        Check(
            name="mean shrinkage weight",
            source="elasticity_estimation.json:mean_shrinkage_weight",
            expected=elasticity["mean_shrinkage_weight"],
            tolerance=0.006,
            patterns=(r"mean (?:empirical-Bayes |shrinkage )?weight[^0-9]{0,30}(0\.\d{2,4})",),
            exclude=r"method of moments|moment|less than|Phase L|variant",
        ),
        Check(
            name="out-of-time price-change episodes",
            source="out_of_time_price_response.json:n_episodes",
            expected=float(out_of_time["n_episodes"]),
            tolerance=0.5,
            patterns=(r"(15\d,\d{3})[^.]{0,40}episodes",),
            allow_transitions=False,
        ),
        Check(
            name="recommendations: actionable share",
            source="recommendations.json:share_actionable",
            expected=100 * recs["share_actionable"],
            tolerance=0.06,
            unit="%",
            patterns=(r"(\d{2}\.\d)%\s*(?:actionable|`?RECOMMEND_CHANGE`?)",),
            exclude=r"aggressive|conservative|13,964|sampling|\bvs\b",
        ),
        Check(
            name="recommendations: contexts scored",
            source="recommendations.json:n_contexts",
            expected=float(recs["n_contexts"]),
            tolerance=0.5,
            patterns=(
                r"(?:batch|sample|scored|profile,)[^0-9]{0,40}(\d,\d{3}) contexts",
                r"(\d,\d{3}) contexts[^0-9]{0,40}(?:standard profile|`shrunk`|decision week)",
            ),
            allow_transitions=False,
        ),
        Check(
            name="HIGH-risk contexts that auto-changed a price",
            source="recommendations.json:high_risk_actionable",
            expected=float(recs["high_risk_actionable"]),
            tolerance=0.5,
            patterns=(
                r"HIGH[- ]risk[^0-9]{0,80}(?:auto-changed a price|automatic price change)"
                r"[^0-9]{0,20}\*{0,2}(\d+)",
            ),
        ),
        Check(
            name="constraint attribution: contexts",
            source="constraint_attribution.json:n_contexts",
            expected=float(attribution["n_contexts"]),
            tolerance=0.5,
            patterns=(r"(13,\d{3}) contexts",),
            allow_transitions=False,
        ),
        Check(
            name="share decided by the learned signal",
            source="constraint_attribution.json:share_determined_by_learned_signal",
            expected=100 * attribution["share_determined_by_learned_signal"],
            tolerance=0.06,
            unit="%",
            patterns=(
                r"\*{0,2}(\d\.\d)%\*{0,2}\s*of (?:final |the final )?recommendations",
                r"interior (?:model |learned )?optim[ua][^0-9%]{0,40}\*{0,2}(\d\.\d)%",
            ),
        ),
        Check(
            name="learned-signal contexts",
            source="constraint_attribution.json:final_determinant_counts",
            expected=float(determinants["learned signal (interior optimum, actionable)"]),
            tolerance=0.5,
            patterns=(r"learned signal \(interior optimum, actionable\)[^0-9]{0,10}\|?\s*(\d{3})\b",),
        ),
        Check(
            name="decision agreement: ml vs pooled",
            source="price_response_comparison.json:ml vs pooled",
            expected=100 * pairs["ml vs pooled"]["share_same_decision_state"],
            tolerance=0.06,
            unit="%",
            patterns=(r"ml vs pooled[^0-9%]{0,60}(\d{2}\.\d)%",),
        ),
        Check(
            name="decision agreement: ml vs shrunk",
            source="price_response_comparison.json:ml vs shrunk",
            expected=100 * pairs["ml vs shrunk"]["share_same_decision_state"],
            tolerance=0.06,
            unit="%",
            patterns=(r"ml vs shrunk[^0-9%]{0,60}(\d{2}\.\d)%",),
        ),
        Check(
            name="decision agreement: pooled vs shrunk",
            source="price_response_comparison.json:pooled vs shrunk",
            expected=100 * pairs["pooled vs shrunk"]["share_same_decision_state"],
            tolerance=0.06,
            unit="%",
            patterns=(r"pooled vs shrunk[^0-9%]{0,60}(\d{2}\.\d)%",),
        ),
        Check(
            name="test count",
            source="test_suite.json:collected_tests",
            expected=float(tests["collected_tests"]),
            tolerance=0.5,
            patterns=(
                r"\*{0,2}(\d{3}) (?:tests? )?(?:passed|collected)",
                r"(\d{3}) tests\b",
            ),
            exclude=r"tests/test_|at Phase|\bPhase [A-Z]\b",
        ),
    ]
    return checks + commercial_checks()


def commercial_checks() -> list[Check]:
    """The commercial headline numbers (value range, product roles)."""
    value = artifact("value_sizing.json")["range"]
    roles = artifact("product_roles.json")
    traffic = {r["role"]: r for r in roles["roles"]}["traffic driver"]
    return [
        Check(
            name="annual value range, low end ($k)",
            source="value_sizing.json:range.low_annual_usd",
            expected=value["low_annual_usd"] / 1000.0,
            tolerance=1.0,
            patterns=(r"\$(\d{3})k to \$\d{3}k",),
            allow_transitions=False,
        ),
        Check(
            name="annual value range, high end ($k)",
            source="value_sizing.json:range.high_annual_usd",
            expected=value["high_annual_usd"] / 1000.0,
            tolerance=1.0,
            patterns=(r"\$\d{3}k to \$(\d{3})k",),
            allow_transitions=False,
        ),
        Check(
            name="value range, low end (% of gross profit)",
            source="value_sizing.json:range.low_uplift_pct",
            expected=100.0 * value["low_uplift_pct"],
            tolerance=0.06,
            patterns=(r"\+(\d+\.\d)% to \+\d+\.\d%",),
            allow_transitions=False,
        ),
        Check(
            name="value range, high end (% of gross profit)",
            source="value_sizing.json:range.high_uplift_pct",
            expected=100.0 * value["high_uplift_pct"],
            tolerance=0.06,
            patterns=(r"\+\d+\.\d% to \+(\d+\.\d)%",),
            allow_transitions=False,
        ),
        Check(
            name="traffic-driver cap: share of gain given up",
            source="product_roles.json:traffic_cap_what_if.share_of_gain_given_up",
            expected=100.0 * roles["traffic_cap_what_if"]["share_of_gain_given_up"],
            tolerance=0.6,
            patterns=(r"gives? up (\d+(?:\.\d)?)% of the estimated gain",),
            allow_transitions=False,
        ),
        Check(
            name="traffic-driver revenue share",
            source="product_roles.json:roles[traffic driver].revenue_share",
            expected=100.0 * traffic["revenue_share"],
            tolerance=0.06,
            patterns=(r"[Tt]raffic[- ]drivers?\W[^.]{0,80}?(\d+\.\d)%(?: of)?(?:\s+revenue|\s*$)",),
            allow_transitions=False,
        ),
    ]


def scan(checks: list[Check]) -> list[dict]:
    files: list[Path] = []
    for glob in SCAN_GLOBS:
        files.extend(sorted(REPO_ROOT.glob(glob)))
    files = [f for f in files if f.name not in SKIP_FILES and f.is_file()]

    mismatches: list[dict] = []
    compiled = [(c, c.compiled()) for c in checks]
    for path in files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        for lineno, line in scannable_lines(path.read_text(encoding="utf-8")):
            for check, patterns in compiled:
                if check.skip_line(line):
                    continue
                for match in (m for rx in patterns for m in rx.finditer(line)):
                    value = parse_number(match.group(1))
                    ok = abs(value - check.expected) <= check.tolerance
                    hit = {
                        "file": rel,
                        "line": lineno,
                        "stated": match.group(1),
                        "value": value,
                        "ok": ok,
                        "text": line.strip()[:200],
                    }
                    check.hits.append(hit)
                    if not ok:
                        mismatches.append(
                            {"metric": check.name, "expected": check.expected,
                             "source": check.source, **hit}
                        )
    return mismatches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true",
                        help="Exit non-zero if any document disagrees with an artifact.")
    args = parser.parse_args()

    checks = build_checks()
    mismatches = scan(checks)

    summary = {
        "n_checks": len(checks),
        "n_statements_verified": sum(len(c.hits) for c in checks),
        "n_mismatches": len(mismatches),
        "documents_scanned": sorted(
            {h["file"] for c in checks for h in c.hits}
        ),
        "checks": [
            {
                "metric": c.name,
                "source_of_truth": c.source,
                "expected": c.expected,
                "unit": c.unit,
                "tolerance": c.tolerance,
                "statements_found": len(c.hits),
                "statements_agreeing": sum(1 for h in c.hits if h["ok"]),
                "files": sorted({h["file"] for h in c.hits}),
            }
            for c in checks
        ],
        "mismatches": mismatches,
    }
    out = METRICS_DIR / "metric_consistency.json"
    write_json(out, summary)

    for check in checks:
        state = "OK   " if all(h["ok"] for h in check.hits) else "DRIFT"
        print(f"{state} {check.name:<45} {len(check.hits):>3} statements  <- {check.source}")
    print(
        f"\n{summary['n_statements_verified']} statements verified across "
        f"{summary['n_checks']} metrics; {summary['n_mismatches']} mismatches"
    )
    for m in mismatches:
        line = (f"  DRIFT {m['file']}:{m['line']} {m['metric']}: states {m['stated']}, "
                f"artifact says {m['expected']}")
        print(line.encode("ascii", "replace").decode("ascii"))
    print(f"wrote {out}")
    return 1 if (args.strict and mismatches) else 0


if __name__ == "__main__":
    raise SystemExit(main())
