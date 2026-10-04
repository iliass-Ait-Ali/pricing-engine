"""End to end on the synthetic public demo: generator -> pipeline -> API.

Always runs (it needs no licensed data), so CI exercises the whole chain that
the hosted demo serves. It also proves the demo build is isolated: nothing it
does may touch the real artifact folder.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from pricing_engine.config import load_config

fastapi_testclient = pytest.importorskip("fastapi.testclient")

pytestmark = pytest.mark.demo

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def demo(demo_config):
    previous = os.environ.get("PRICING_ENGINE_CONFIG")
    os.environ["PRICING_ENGINE_CONFIG"] = str(demo_config)
    from api.main import app

    with fastapi_testclient.TestClient(app) as client:
        yield client, app.state.engine, load_config(demo_config)
    if previous is None:
        os.environ.pop("PRICING_ENGINE_CONFIG", None)
    else:
        os.environ["PRICING_ENGINE_CONFIG"] = previous


def test_demo_serves_synthetic_data_and_says_so(demo):
    client, _, cfg = demo
    health = client.get("/health")
    assert health.status_code == 200
    body = health.json()
    assert body["model_loaded"] is True
    assert body["data_mode"] == "synthetic" and "SYNTHETIC" in body["data_label"]
    assert health.headers["X-Data-Mode"] == "synthetic"
    info = client.get("/model/info").json()
    assert info["data_mode"] == "synthetic"
    assert "SYNTHETIC" in client.get("/openapi.json").json()["info"]["description"]
    assert cfg.is_synthetic


def test_recommendations_round_trip_with_a_mix_of_decisions(demo):
    client, state, _ = demo
    keys = state.products()[["upc", "store"]].drop_duplicates().head(60)
    decisions = set()
    for upc, store in keys.itertuples(index=False):
        r = client.post("/recommend-price", json={"upc": int(upc), "store": int(store)})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["final_recommended_price"] > 0
        if body["risk_level"] == "HIGH":
            assert body["decision"] != "RECOMMEND_CHANGE"  # HIGH risk is never auto-actionable
        decisions.add(body["decision"])
    assert len(decisions) >= 2, f"the demo shows only {decisions}"


def test_shared_serving_state_matches_the_api(demo):
    """The copilot and dashboard call AppState directly; the API must agree."""
    client, state, _ = demo
    upc, store = (int(v) for v in state.products()[["upc", "store"]].iloc[0])
    direct = state.recommend(upc, store).as_dict()
    via_api = client.post("/recommend-price", json={"upc": upc, "store": store}).json()
    for key in ("decision", "final_recommended_price", "risk_level", "reason_codes"):
        assert direct[key] == via_api[key]


def test_demo_build_never_touches_the_real_artifacts(demo_config):
    cfg = load_config(demo_config)
    out_root = Path(demo_config).parent
    for key, value in cfg.require("paths").items():
        assert Path(value).resolve().is_relative_to(out_root.resolve()), key

    build = json.loads((cfg.path("metrics_dir") / "demo_build.json").read_text(encoding="utf-8"))
    started = datetime.fromisoformat(build["built_at_utc"]).timestamp() - build["total_seconds"]
    real_metrics = load_config(REPO_ROOT / "configs" / "config.yaml").path("metrics_dir")
    touched = [
        p.name for p in real_metrics.glob("*") if p.is_file() and p.stat().st_mtime > started - 1
    ]
    assert not touched, f"the demo build modified real artifacts: {touched}"
    assert not (real_metrics / "demo_build.json").exists()
