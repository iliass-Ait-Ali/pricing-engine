"""README's headline numbers must be generated, never hand-copied.

Documentation drift was a real defect class in this project: a number updated
in one file and not another. The generated block in README.md is written by
``scripts/update_readme_metrics.py`` straight from ``artifacts/metrics/*.json``,
and this module fails the build if the two ever disagree.

The artifact-dependent tests skip when the generated artifacts are absent (a
fresh clone, or CI, which never downloads the licensed dataset). The structural
tests - markers present, no stale hardcoded counts - always run.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
SCRIPT = REPO_ROOT / "scripts" / "update_readme_metrics.py"
METRICS_DIR = REPO_ROOT / "artifacts" / "metrics"

BEGIN = "<!-- BEGIN GENERATED METRICS -->"
END = "<!-- END GENERATED METRICS -->"


def load_script():
    spec = importlib.util.spec_from_file_location("update_readme_metrics", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return load_script()


@pytest.fixture(scope="module")
def readme() -> str:
    return README.read_text(encoding="utf-8")


def artifacts_present(module) -> bool:
    names = [
        "dataset_fingerprint.json",
        "eda_summary.json",
        "data_validation.json",
        "model_metrics.json",
        "evaluation.json",
        "elasticity.json",
        "elasticity_estimation.json",
        "elasticity_funnel.json",
        "out_of_time_price_response.json",
        "recommendations.json",
        "constraint_attribution.json",
        "price_response.json",
        "backtest.json",
        "batch_performance.json",
        "build_audit.json",
    ]
    return all((METRICS_DIR / n).exists() for n in names) and module.TEST_SUITE_ARTIFACT.exists()


# ---------------------------------------------------------------------------
# structure (always runs)
# ---------------------------------------------------------------------------
def test_readme_has_the_generated_block_markers(readme):
    assert readme.count(BEGIN) == 1
    assert readme.count(END) == 1
    assert readme.index(BEGIN) < readme.index(END)


def test_generated_block_is_not_empty(readme):
    block = readme.split(BEGIN, 1)[1].split(END, 1)[0]
    assert "| metric | value |" in block
    assert len(block.strip().splitlines()) > 20


def test_readme_does_not_hardcode_a_test_count_outside_the_block(readme):
    """A hand-typed 'N tests' is exactly the drift this mechanism removes."""
    outside = readme.split(BEGIN, 1)[0] + readme.split(END, 1)[1]
    stale = re.findall(r"\b\d{2,4}\s+(?:tests|passed)\b", outside)
    assert not stale, f"hand-copied test counts outside the generated block: {stale}"


# ---------------------------------------------------------------------------
# consistency with the authoritative artifacts
# ---------------------------------------------------------------------------
def test_generated_block_matches_the_artifacts(script, readme):
    if not artifacts_present(script):
        pytest.skip("generated metrics artifacts are not present in this environment")
    expected = script.build_block()
    actual = BEGIN + readme.split(BEGIN, 1)[1].split(END, 1)[0] + END
    assert actual == expected, (
        "README's generated metrics are stale - run "
        "`python scripts/update_readme_metrics.py`."
    )


def test_check_mode_agrees(script):
    """`--check` is what CI and the Makefile call; it must agree with the test."""
    if not artifacts_present(script):
        pytest.skip("generated metrics artifacts are not present in this environment")
    current = README.read_text(encoding="utf-8")
    assert script.apply_block(current, script.build_block()) == current


def test_readme_test_count_matches_the_live_collection(script, request):
    """When the whole suite runs, the published test count must be the real one."""
    if not artifacts_present(script):
        pytest.skip("generated metrics artifacts are not present in this environment")
    args = list(request.config.invocation_params.args)
    selected_subset = any(not a.startswith("-") for a in args) or any(
        a.split("=")[0] in {"-k", "-m", "--lf", "--last-failed", "--ff", "--failed-first",
                            "--deselect", "--ignore"}
        for a in args
    )
    if selected_subset:
        pytest.skip("a subset of the suite was selected; the live count is not comparable")
    import json

    published = json.loads(script.TEST_SUITE_ARTIFACT.read_text(encoding="utf-8"))
    collected = request.session.testscollected
    assert collected == published["collected_tests"], (
        f"pytest collected {collected} tests but "
        f"{script.TEST_SUITE_ARTIFACT.name} says {published['collected_tests']} - run "
        "`python scripts/update_readme_metrics.py --refresh-tests`."
    )


def test_model_internal_uplift_is_not_a_headline_metric(script, readme):
    """The self-scored uplift is never presented as realised business impact."""
    if not artifacts_present(script):
        pytest.skip("generated metrics artifacts are not present in this environment")
    block = readme.split(BEGIN, 1)[1].split(END, 1)[0]
    for line in block.splitlines():
        lowered = line.lower()
        if "uplift" in lowered or "vs historical" in lowered:
            assert "model-internal" in lowered, (
                f"a counterfactual profit line without the model-internal qualifier: {line}"
            )


def test_every_metric_family_names_its_source_of_truth(script, readme):
    if not artifacts_present(script):
        pytest.skip("generated metrics artifacts are not present in this environment")
    block = readme.split(BEGIN, 1)[1].split(END, 1)[0]
    for path in script.METRIC_SOURCES.values():
        for part in path.split(" + "):
            name = part.strip().split("/")[-1]
            assert name in block, f"{name} is not cited as a source of truth in README"


def test_sys_path_is_not_polluted():
    """Importing the script module must not leave duplicate src entries behind."""
    src = str(REPO_ROOT / "src")
    assert sys.path.count(src) <= 1
