"""The copilot loop, with a scripted model and the real engine tools.

No API key is needed: the model is a ScriptedClient that replays fixed turns,
and the tools run against the synthetic demo state.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from pricing_engine.config import load_config
from pricing_engine.copilot import LLMTurn, PricingCopilot, ScriptedClient, ToolCall
from pricing_engine.copilot.tools import TOOLS, run_tool
from pricing_engine.serving import build_state

pytestmark = pytest.mark.demo


@pytest.fixture(scope="module")
def state(demo_config):
    return build_state(load_config(demo_config))


@pytest.fixture(scope="module")
def key(state):
    row = state.products().iloc[0]
    return int(row["upc"]), int(row["store"])


@pytest.fixture(scope="module")
def rec(state, key):
    return run_tool(state, "recommend_price", {"upc": key[0], "store": key[1]})


def _call(name, **args):
    return LLMTurn(tool_calls=[ToolCall(id=f"call_{name}", name=name, arguments=args)])


def _good_text(rec):
    return (f"Decision {rec['decision']}: move from ${rec['current_price']} to "
            f"${rec['recommended_price']} ({rec['price_change_percent']}%), risk {rec['risk_level']}. "
            f"The model-internal estimated uplift is "
            f"{rec['model_internal_estimated_profit_uplift_percent']}%.")


def test_tools_are_json_ready(state, key):
    import json

    for name, args in [("get_model_info", {}), ("list_products", {"limit": 3}),
                       ("recommend_price", {"upc": key[0], "store": key[1]}),
                       ("compare_policies", {"upc": key[0], "store": key[1]}),
                       ("explain_reason_codes", {"codes": ["PRICE_CHANGE_LIMIT"]})]:
        result = run_tool(state, name, args)
        assert "error" not in result, (name, result)
        json.loads(json.dumps(result))
    assert {t.schema()["function"]["name"] for t in TOOLS.values()} == set(TOOLS)


def test_happy_path_passes_the_guard(state, key, rec):
    client = ScriptedClient([_call("recommend_price", upc=key[0], store=key[1]),
                             LLMTurn(text=_good_text(rec))])
    out = PricingCopilot(client, state).ask("What price for this product?")
    assert out.status == "passed", out.guard
    assert out.tool_calls[0].name == "recommend_price"
    assert rec["decision"] in out.answer
    assert "Synthetic demo data" in out.answer  # appended by code, not by the model


def test_invented_number_triggers_one_regeneration(state, key, rec):
    client = ScriptedClient([
        _call("recommend_price", upc=key[0], store=key[1]),
        LLMTurn(text=f"Decision {rec['decision']}: charge $987.65."),
        LLMTurn(text=_good_text(rec)),
    ])
    out = PricingCopilot(client, state).ask("What price?")
    assert out.status == "passed_after_regeneration"
    feedback = client.requests[-1][-1]["content"]
    assert "987.65" in feedback


def test_repeated_failure_falls_back_to_a_template(state, key, rec):
    client = ScriptedClient([
        _call("recommend_price", upc=key[0], store=key[1]),
        LLMTurn(text="This will increase profit by 50%."),
        LLMTurn(text="It is guaranteed to raise profit by 50%."),
    ])
    out = PricingCopilot(client, state).ask("What price?")
    assert out.status == "fallback_template"
    assert rec["decision"] in out.answer
    assert out.guard.passed  # the template itself passes the guard


def test_tool_round_cap_stops_runaway_loops(state, key):
    turns = [_call("get_model_info") for _ in range(10)]
    out = PricingCopilot(ScriptedClient(turns), state, max_tool_rounds=3,
                         max_regenerations=0).ask("loop")
    assert out.status == "fallback_template"
    assert len(out.tool_calls) == 3


def test_unknown_tool_and_unknown_product_come_back_as_data(state):
    client = ScriptedClient([
        LLMTurn(tool_calls=[ToolCall("a", "set_price", {"price": 1}),
                            ToolCall("b", "recommend_price", {"upc": 1, "store": 1})]),
        LLMTurn(text="The engine has no data for that product."),
    ])
    out = PricingCopilot(client, state).ask("Price UPC 1?")
    assert "Unknown tool" in out.tool_calls[0].result["error"]
    assert "No decision context" in out.tool_calls[1].result["error"]
    assert out.status == "passed"


def test_copilot_imports_without_the_openai_sdk():
    code = ("import sys; sys.modules['openai'] = None; "
            "import pricing_engine.copilot as c; print('ok')")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            env={**os.environ, "PYTHONPATH": "src"})
    assert result.returncode == 0 and "ok" in result.stdout, result.stderr
