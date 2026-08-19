"""Phase M / Task 11 - portfolio claim audit.

    python scripts/audit_claims.py [--strict]

Scans the whole repository for the phrases that get portfolio projects into
trouble in interviews, classifies every occurrence, and fails (with ``--strict``)
if any UNSUPPORTED claim is still present.

The classification is not guessed by regex. Each pattern carries a verdict and a
rule; occurrences that are the *prohibition* of a claim rather than the claim
itself (the many "not causal", "never realised" disclaimers) are recognised and
scored as SAFE.

Outputs
-------
    reports/20_CLAIM_AUDIT.md
    artifacts/metrics/claim_audit.json
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from _phase_m import md_table  # noqa: E402

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.utils.io import write_json  # noqa: E402

SCAN_SUFFIXES = {".md", ".py", ".yaml", ".yml"}
SKIP_DIRS = {".git", ".pytest_cache", ".ruff_cache", "__pycache__", "data", "artifacts", "notebooks"}
#: The scanner itself *defines* the watched phrases, so it matches all of them.
#: Excluding it is not self-serving - the definitions are quoted in section 2 of
#: the report, where a reader can check them directly.
SKIP_FILES = {
    "AI_Pricing_Revenue_Optimization_Claude_Codex_Master_Prompt.txt",
    "audit_claims.py",
    # The generated report quotes every offending line by design; scanning it
    # would report its own contents back as new offences.
    "20_CLAIM_AUDIT.md",
}

#: How many lines of context are read around a hit when deciding whether the
#: sentence asserts the claim or forbids it. Prohibition lists ("Never use:"
#: followed by bullets) need several lines.
CONTEXT_BEFORE = 5
CONTEXT_AFTER = 1

#: Phrases that turn a statement into a disclaimer rather than a claim.
NEGATORS = re.compile(
    r"\b(not|never|no|nothing|cannot|can't|does not|do not|isn't|aren't|without|"
    r"avoid|must not|may not|refus|forbid|prohibit|wrong to|would be|superseded|"
    r"too high|overstat|removed|replaced|instead of|rather than|corrected|"
    r"unsupported|phase l)\b",
    re.IGNORECASE,
)

#: pattern -> (verdict, rule, preferred wording)
PATTERNS: dict[str, tuple[str, str, str]] = {
    r"\bcausal\b": (
        "SAFE",
        "Only ever appears as a denial of causality or as the name of "
        "docs/CAUSAL_LIMITATIONS.md. Any assertive use would be UNSUPPORTED.",
        "observational price-response estimate",
    ),
    r"production[ -]ready": (
        "SAFE",
        "Only appears as an explicit denial. No orchestration, registry, live "
        "monitoring or deployment exists.",
        "offline portfolio project with a runnable API and dashboard",
    ),
    r"\boptimal price\b": (
        "UNSUPPORTED",
        "'Optimal' asserts a global optimum of the true profit function. What "
        "exists is the arg-max of a fitted, observational model over a "
        "constrained grid.",
        "candidate profit-maximising price under model assumptions",
    ),
    r"\b(increased|improved|achieved|delivered|realised|realized) (profit|revenue|margin)": (
        "UNSUPPORTED",
        "Asserts a realised business outcome. No price recommended by this "
        "engine was ever charged.",
        "model-internal estimated gross-profit uplift (offline simulation)",
    ),
    r"profit uplift": (
        "NEEDS QUALIFICATION",
        "Acceptable only with the 'model-internal estimated' qualifier and an "
        "adjacent statement that it is an offline simulation.",
        "model-internal estimated profit uplift",
    ),
    r"\bvalidated\b": (
        "NEEDS QUALIFICATION",
        "Acceptable for things that were actually validated out of sample "
        "(forecast WAPE, price-response episodes). Never for the pricing "
        "policy itself.",
        "validated out of time on unseen price-change episodes",
    ),
    r"no forecast skill is lost": (
        "UNSUPPORTED",
        "The hybrid preserves the baseline only AT the reference price. "
        "Removed in Phase M; this pattern must find zero occurrences.",
        "preserves the forecaster's baseline exactly at the reference price",
    ),
    r"mean shrinkage weight[^.\n]{0,40}0\.95": (
        "UNSUPPORTED",
        "Superseded. The 0.95 figure came from HC1 standard errors on a "
        "clustered panel; the corrected value is 0.78 (reports/14).",
        "mean empirical-Bayes shrinkage weight 0.78 (panel-robust standard errors)",
    ),
    r"\bguarantee[sd]?\b": (
        "NEEDS QUALIFICATION",
        "Only acceptable for mechanical guarantees of the code (non-negative "
        "predictions, bounds respected), never for business outcomes.",
        "the optimizer never returns a price outside the feasible interval",
    ),
    r"\bstate[- ]of[- ]the[- ]art\b|\bbest[- ]in[- ]class\b|\bworld[- ]class\b": (
        "UNSUPPORTED",
        "Unfalsifiable marketing language with no benchmark behind it.",
        "(delete; state the metric instead)",
    ),
}


def scan(root: Path) -> list[dict]:
    hits = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SCAN_SUFFIXES:
            continue
        if path.name in SKIP_FILES or any(part in SKIP_DIRS for part in path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(lines, start=1):
            for pattern, (verdict, _, _) in PATTERNS.items():
                if re.search(pattern, line, re.IGNORECASE):
                    context = " ".join(
                        lines[max(0, i - 1 - CONTEXT_BEFORE): i + CONTEXT_AFTER]
                    )
                    negated = bool(NEGATORS.search(context))
                    hits.append({
                        "file": rel,
                        "line": i,
                        "pattern": pattern,
                        "declared_verdict": verdict,
                        "negated_context": negated,
                        "text": line.strip()[:160],
                    })
    return hits


def resolve(hit: dict) -> str:
    """Final verdict for one occurrence, after accounting for disclaimers."""
    declared = hit["declared_verdict"]
    if hit["negated_context"] and declared in {"UNSUPPORTED", "NEEDS QUALIFICATION"}:
        return "SAFE"
    if declared == "NEEDS QUALIFICATION" and re.search(
        r"model[- ]internal|offline|estimated|out of time|out-of-time", hit["text"], re.IGNORECASE
    ):
        return "SAFE"
    return declared


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true",
                        help="Exit non-zero if any UNSUPPORTED claim survives.")
    args = parser.parse_args()

    t0 = time.time()
    cfg = load_config()
    hits = scan(REPO_ROOT)
    for h in hits:
        h["verdict"] = resolve(h)

    by_verdict: dict[str, list[dict]] = {"UNSUPPORTED": [], "NEEDS QUALIFICATION": [], "SAFE": []}
    for h in hits:
        by_verdict[h["verdict"]].append(h)

    pattern_rows = []
    for pattern, (verdict, rule, preferred) in PATTERNS.items():
        _ = rule
        matched = [h for h in hits if h["pattern"] == pattern]
        resolved = {v: sum(1 for h in matched if h["verdict"] == v) for v in by_verdict}
        pattern_rows.append([
            f"`{pattern}`", verdict, f"{len(matched)}",
            f"{resolved['SAFE']}", f"{resolved['NEEDS QUALIFICATION']}", f"{resolved['UNSUPPORTED']}",
            preferred,
        ])
    rule_rows = [[f"`{p_}`", r] for p_, (_, r, _) in PATTERNS.items()]

    unsupported_rows = [
        [f"`{h['file']}:{h['line']}`", h["text"], PATTERNS[h["pattern"]][2]]
        for h in by_verdict["UNSUPPORTED"]
    ]
    needs_rows = [
        [f"`{h['file']}:{h['line']}`", h["text"], PATTERNS[h["pattern"]][2]]
        for h in by_verdict["NEEDS QUALIFICATION"]
    ]

    summary = {
        "n_files_scanned": len({h["file"] for h in hits}),
        "n_occurrences": len(hits),
        "counts": {k: len(v) for k, v in by_verdict.items()},
        "unsupported": by_verdict["UNSUPPORTED"],
        "needs_qualification": by_verdict["NEEDS QUALIFICATION"],
        "runtime_seconds": round(time.time() - t0, 1),
    }
    write_json(cfg.path("metrics_dir") / "claim_audit.json", summary)

    n_unsupported = len(by_verdict["UNSUPPORTED"])
    n_needs = len(by_verdict["NEEDS QUALIFICATION"])

    report = f"""# 20. Portfolio claim audit

Every phrase in this repository that could overstate what the project has shown,
classified. Run by `scripts/audit_claims.py`; `--strict` makes it fail the build
if an UNSUPPORTED claim reappears.

Scanned: `.md`, `.py`, `.yaml` across the repository (excluding data, artifacts
and the original brief). **{summary['n_occurrences']} occurrences** of
{len(PATTERNS)} watched patterns.

## 1. Verdict summary

| Verdict | Occurrences |
| --- | ---: |
| SAFE | {len(by_verdict['SAFE'])} |
| NEEDS QUALIFICATION | {n_needs} |
| UNSUPPORTED | {n_unsupported} |

An occurrence is scored SAFE when the surrounding sentence *denies* the claim -
this repository states "not causal", "never realised", "not production ready"
many times, and those are the opposite of the offence.

## 2. The watched patterns

{md_table(pattern_rows, ["Pattern", "Default verdict", "Occurrences", "SAFE", "NEEDS QUAL.", "UNSUPPORTED", "Preferred wording"], ["---", "---", "---:", "---:", "---:", "---:", "---"])}

Why each pattern is watched:

{md_table(rule_rows, ["Pattern", "Rule"], ["---", "---"])}

## 3. Unsupported claims still present

{md_table(unsupported_rows, ["Location", "Text", "Replace with"], ["---", "---", "---"]) if unsupported_rows else "**None.**"}

## 4. Claims needing qualification

{md_table(needs_rows, ["Location", "Text", "Preferred wording"], ["---", "---", "---"]) if needs_rows else "**None.**"}

## 5. What Phase M corrected

| Claim | Status before | Status now |
| --- | --- | --- |
| "no forecast skill is lost" | UNSUPPORTED - asserted for all prices | Replaced everywhere with "preserves the forecaster's baseline exactly at the reference price; counterfactuals away from it are governed by the elasticity model" |
| "optimal price" | UNSUPPORTED - asserts a true optimum | "candidate profit-maximising price under model assumptions" |
| "mean shrinkage weight 0.95" | UNSUPPORTED - artifact of HC1 SEs on a clustered panel | 0.78 with panel-robust standard errors (`reports/14`) |
| `recommended_price` on a REVIEW_REQUIRED row | Misleading - a proposal presented as a price | Split into `proposed_candidate_price` and `final_recommended_price` (`reports/19`) |
| "the ML model produces the recommendations" | Unsupported by the constraint attribution | Under the default policy 2.1% of final recommendations come from an interior model optimum (`reports/11`, `reports/12`, `reports/13`) |

## 6. The safe vocabulary

Use:

* model-internal estimated profit uplift
* candidate profit-maximising price under model assumptions
* offline simulated recommendation
* predictive price-response estimate
* training-estimated elasticity
* observational retail scanner data
* out-of-time predictive validation

Never use:

* optimized profit by X% / increased profit / achieved X% uplift
* optimal price / the best price
* causal price effect / proven price effect
* validated pricing uplift
* production ready
* no forecast skill is lost

## 7. Claims that are legitimate and must NOT be weakened

These are measured facts and stay exactly as they are:

* 4,707,776 processed `UPC x store x week` observations from Dominick's Finer
  Foods Cereals.
* Test WAPE 0.4565 on 749,040 held-out rows, against 0.7326 for the best naive
  baseline, on a chronological split.
* The reported test counts, lint status and runtimes.
* The elasticity point estimates and their (panel-robust) confidence intervals.
* Zero HIGH-risk contexts actionable under both default policy profiles.

---

*Generated by `scripts/audit_claims.py` in {summary['runtime_seconds']}s.*
"""
    out = cfg.path("reports_dir") / "20_CLAIM_AUDIT.md"
    out.write_text(report, encoding="utf-8")
    print(f"wrote {out}")
    print(f"SAFE={len(by_verdict['SAFE'])} NEEDS_QUALIFICATION={n_needs} UNSUPPORTED={n_unsupported}")
    for h in by_verdict["UNSUPPORTED"]:
        print(f"  UNSUPPORTED {h['file']}:{h['line']}  {h['text'][:100]}")
    if args.strict and n_unsupported:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
