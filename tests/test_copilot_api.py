"""POST /copilot/ask: disabled without a key, guarded answers, rate limits."""

from __future__ import annotations

import os

import pytest

from pricing_engine.copilot import LLMTurn, PricingCopilot, ScriptedClient, ToolCall

fastapi_testclient = pytest.importorskip("fastapi.testclient")

pytestmark = pytest.mark.demo


@pytest.fixture(scope="module")
def app_client(demo_config):
    previous = {k: os.environ.get(k) for k in ("PRICING_ENGINE_CONFIG", "OPENAI_API_KEY")}
    os.environ["PRICING_ENGINE_CONFIG"] = str(demo_config)
    os.environ.pop("OPENAI_API_KEY", None)
    from api.main import app

    with fastapi_testclient.TestClient(app) as client:
        yield app, client
    for k, v in previous.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_copilot_is_disabled_without_a_key(app_client):
    app, client = app_client
    assert app.state.copilot is None
    r = client.post("/copilot/ask", json={"question": "What should Cheerios cost?"})
    assert r.status_code == 503 and "OPENAI_API_KEY" in r.json()["detail"]


def test_empty_or_huge_questions_are_rejected(app_client):
    _, client = app_client
    assert client.post("/copilot/ask", json={"question": ""}).status_code == 422
    assert client.post("/copilot/ask", json={"question": "x" * 1001}).status_code == 422


def test_answer_comes_back_with_its_tool_trace(app_client):
    app, client = app_client
    state = app.state.engine
    upc, store = (int(v) for v in state.products()[["upc", "store"]].iloc[0])
    script = ScriptedClient([
        LLMTurn(tool_calls=[ToolCall("c1", "get_model_info", {})]),
        LLMTurn(text="The engine serves the synthetic demo panel."),
    ])
    app.state.copilot = PricingCopilot(script, state)
    try:
        r = client.post("/copilot/ask", json={"question": f"What data backs UPC {upc} in store {store}?"})
    finally:
        app.state.copilot = None
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "passed"
    assert body["tool_calls"][0]["name"] == "get_model_info"
    assert body["data_mode"] == "synthetic"


def test_rate_limiter_blocks_after_the_hourly_quota():
    from api.routes.copilot import RateLimiter

    limiter = RateLimiter(per_client_per_hour=2, daily_limit=100)
    assert limiter.allow("a", now=0) and limiter.allow("a", now=1)
    assert not limiter.allow("a", now=2)
    assert limiter.allow("b", now=2)            # other clients unaffected
    assert limiter.allow("a", now=3700)         # window slides
    daily = RateLimiter(per_client_per_hour=10, daily_limit=1)
    assert daily.allow("x", now=0) and not daily.allow("y", now=1)
