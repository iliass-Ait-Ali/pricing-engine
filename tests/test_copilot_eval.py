"""The copilot evaluation set and its scoring (no API key needed)."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from pricing_engine.copilot.guard import GuardReport
from pricing_engine.copilot.tools import TOOLS

REPO_ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = REPO_ROOT / "evals" / "copilot"
SPEC = yaml.safe_load((EVAL_DIR / "questions.yaml").read_text(encoding="utf-8"))


def _load_script():
    spec = importlib.util.spec_from_file_location("eval_copilot", REPO_ROOT / "scripts" / "eval_copilot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _out(answer, tools=(), status="passed", failed=()):
    calls = [SimpleNamespace(name=t, result={}) for t in tools]
    calls += [SimpleNamespace(name=t, result={"error": "no such product"}) for t in failed]
    return SimpleNamespace(answer=answer, tool_calls=calls, status=status,
                           guard=GuardReport(passed=True), latency_ms=1, usage={})


def test_question_set_is_well_formed():
    ids = [c["id"] for c in SPEC["cases"]]
    assert len(ids) == len(set(ids)) >= 20
    for case in SPEC["cases"]:
        assert set(case.get("expected_tools", [])) <= set(TOOLS), case["id"]
    categories = {c["category"] for c in SPEC["cases"]}
    assert {"causal bait", "fabrication bait", "prompt injection", "off-topic"} <= categories


def test_scoring_rewards_a_grounded_answer_and_catches_bad_ones():
    ev = _load_script()
    case = {"id": "x", "expected_tools": ["recommend_price"], "must_include": ["model-internal"]}
    good = ev.score_case(case, _out("A model-internal estimated uplift.", ["recommend_price"]),
                         SPEC["global_forbidden"])
    assert good["passed"]
    no_tool = ev.score_case(case, _out("A model-internal estimated uplift."), SPEC["global_forbidden"])
    assert not no_tool["passed"] and no_tool["missing_tools"] == ["recommend_price"]
    bait = ev.score_case(case, _out("Model-internal: this " + "optimal" + " price is best.",
                                    ["recommend_price"]), SPEC["global_forbidden"])
    assert not bait["passed"] and bait["forbidden_hits"]
    fallback = ev.score_case(case, _out("model-internal", ["recommend_price"], "fallback_template"),
                             SPEC["global_forbidden"])
    assert not fallback["passed"]


def test_a_required_tool_that_returned_an_error_does_not_count():
    """Calling the right tool with an invented product code is not a pass."""
    ev = _load_script()
    case = {"id": "x", "expected_tools": ["recommend_price"]}
    wrong_code = ev.score_case(case, _out("No data for that product.", failed=["recommend_price"]), [])
    assert not wrong_code["passed"]
    assert wrong_code["missing_tools"] == ["recommend_price"]
    assert wrong_code["tools_returned_error"] == ["recommend_price"]
    retried = ev.score_case(case, _out("Fine.", tools=["recommend_price"], failed=["recommend_price"]), [])
    assert retried["passed"]


def test_refusal_cases_need_a_refusal():
    ev = _load_script()
    case = {"id": "r", "expect_refusal": True}
    assert ev.score_case(case, _out("I can only help with pricing questions."), [])["passed"]
    assert not ev.score_case(case, _out("Here is a poem about stocks."), [])["passed"]
    assert ev.score_case(case, _out("I can\u2019t set prices."), [])["passed"]   # curly apostrophe


@pytest.mark.demo
def test_recorded_answers_still_pass(demo_config):
    """Replays recorded model turns against today's engine (skipped until recorded)."""
    recordings = list((EVAL_DIR / "recordings").glob("*.json"))
    if not recordings:
        pytest.skip("no copilot recordings yet: run scripts/eval_copilot.py --mode record")
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "eval_copilot.py"), "--mode", "replay",
         "--config", str(demo_config)],
        cwd=REPO_ROOT, capture_output=True, text=True, env={**os.environ},
    )
    assert result.returncode == 0, result.stderr
    import json

    summary = json.loads((EVAL_DIR / "replay_results.json").read_text(encoding="utf-8"))
    # The guard's invariant holds whatever the recordings contain: no answer
    # that reaches a person shows a number the engine did not return.
    assert summary["unsupported_numbers_in_final_answers"] == 0
    assert summary["scored"] == summary["cases"]
    # A replay can only do as well as the recorded run or worse (a recording
    # goes stale when the demo's numbers move); it must never do better.
    recorded = json.loads((EVAL_DIR / "results.json").read_text(encoding="utf-8"))
    assert summary["forbidden_hits"] <= recorded["forbidden_hits"]
    replayed_pass = sum(bool(r.get("passed")) for r in summary["results"])
    assert replayed_pass <= round(recorded["pass_rate"] * recorded["scored"])
