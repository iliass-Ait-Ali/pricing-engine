"""Every hand-authored document must agree with the artifact that owns the number.

`scripts/audit_metric_consistency.py` reads each headline metric from its single
authoritative artifact and checks every sentence in the hand-authored Markdown
that states it. This test turns that audit into a build failure, so numeric
drift cannot be reintroduced silently.

Skips when the generated artifacts are absent (fresh clone, or CI, which never
downloads the licensed dataset).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "audit_metric_consistency.py"
METRICS_DIR = REPO_ROOT / "artifacts" / "metrics"

REQUIRED_ARTIFACTS = (
    "dataset_fingerprint.json",
    "eda_summary.json",
    "model_metrics.json",
    "evaluation.json",
    "elasticity_estimation.json",
    "elasticity_funnel.json",
    "out_of_time_price_response.json",
    "recommendations.json",
    "constraint_attribution.json",
    "price_response_comparison.json",
    "test_suite.json",
)


@pytest.fixture(scope="module")
def audit():
    spec = importlib.util.spec_from_file_location("audit_metric_consistency", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # The module defines a dataclass, and dataclasses resolve their annotations
    # through sys.modules; registering the module first keeps that working.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def result(audit):
    if not all((METRICS_DIR / n).exists() for n in REQUIRED_ARTIFACTS):
        pytest.skip("generated metrics artifacts are not present in this environment")
    checks = audit.build_checks()
    mismatches = audit.scan(checks)
    return checks, mismatches


def test_no_document_contradicts_an_artifact(result):
    _, mismatches = result
    detail = "\n".join(
        f"  {m['file']}:{m['line']} {m['metric']}: states {m['stated']}, "
        f"artifact ({m['source']}) says {m['expected']}"
        for m in mismatches
    )
    assert not mismatches, f"{len(mismatches)} numeric drift(s) between documents:\n{detail}"


def test_the_audit_actually_verified_something(result):
    """A silent audit that matches nothing would pass vacuously."""
    checks, _ = result
    verified = sum(len(c.hits) for c in checks)
    assert verified >= 100, f"only {verified} statements matched; the patterns have gone stale"


def test_most_metrics_are_stated_somewhere(result):
    """Each metric family should be traceable to at least one document."""
    checks, _ = result
    unmatched = [c.name for c in checks if not c.hits]
    assert len(unmatched) <= 4, f"too many metrics no document states: {unmatched}"


def test_the_headline_scientific_finding_is_still_stated(audit, result):
    """The 2.9%-type attribution result must remain visible in the documents."""
    checks, _ = result
    by_name = {c.name: c for c in checks}
    learned = by_name["share decided by the learned signal"]
    assert learned.hits, "no document states the share decided by the learned signal"
    assert learned.expected < 10.0, (
        "the learned-signal share is the project's central finding; a large value "
        "here means the guardrails changed and the finding must be re-derived, "
        "not quietly restated"
    )
