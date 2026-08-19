"""Phase I tests: API contract, validation and model-loading behaviour.

These tests need the trained artifact and the feature table, so they skip
cleanly on a fresh clone (and in CI, which never downloads the licensed data).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (REPO_ROOT, REPO_ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from pricing_engine.config import load_config  # noqa: E402

cfg = load_config()
ARTIFACTS_READY = (
    (cfg.path("models_dir") / "demand_model.joblib").exists() and cfg.path("features_table").exists()
)

pytestmark = pytest.mark.skipif(
    not ARTIFACTS_READY,
    reason="requires the trained model and feature table (run make data && make train)",
)


@pytest.fixture(scope="module")
def client():
    from api.main import app

    with fastapi_testclient.TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def sample_key(client):
    """A UPC x store that the service actually serves."""
    from api.state import build_state

    state = build_state()
    row = state.contexts.sort_values("week").iloc[-1]
    return int(row["upc"]), int(row["store"])


# ---------------------------------------------------------------------------
# meta endpoints
# ---------------------------------------------------------------------------
def test_health_reports_loaded_model(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True
    assert body["contexts_loaded"] > 0
    assert len(body["decision_weeks"]) > 0


def test_model_info_exposes_version_and_windows(client):
    r = client.get("/model/info")
    assert r.status_code == 200
    body = r.json()
    assert body["version"]
    assert body["target"] == "move"
    assert len(body["train_weeks"]) == 2
    assert body["train_weeks"][1] < body["test_weeks"][0]  # temporal split preserved
    assert "MODEL-INTERNAL" in body["disclaimer"]
    assert body["price_response_method"] in {"ml", "pooled", "shrunk"}


def test_model_is_loaded_once_not_per_request(client):
    """Two requests must be served by the same model object."""
    from api.main import app

    first = id(app.state.engine.model)
    client.post("/predict-demand", json={"upc": 1, "store": 1})
    assert id(app.state.engine.model) == first


# ---------------------------------------------------------------------------
# predict-demand
# ---------------------------------------------------------------------------
def test_predict_demand_returns_non_negative_units(client, sample_key):
    upc, store = sample_key
    r = client.post("/predict-demand", json={"upc": upc, "store": store})
    assert r.status_code == 200
    body = r.json()
    assert body["predicted_units"] >= 0
    assert body["price_used"] == body["observed_price"]


def test_predict_demand_respects_a_candidate_price(client, sample_key):
    upc, store = sample_key
    base = client.post("/predict-demand", json={"upc": upc, "store": store}).json()
    cheaper = client.post(
        "/predict-demand", json={"upc": upc, "store": store, "price": base["observed_price"] * 0.8}
    ).json()
    assert cheaper["price_used"] < base["price_used"]
    assert cheaper["predicted_units"] >= base["predicted_units"]


def test_predict_demand_rejects_negative_price(client, sample_key):
    upc, store = sample_key
    r = client.post("/predict-demand", json={"upc": upc, "store": store, "price": -1.0})
    assert r.status_code == 422


def test_unknown_series_returns_404(client):
    r = client.post("/predict-demand", json={"upc": 999999999, "store": 1})
    assert r.status_code == 404
    assert "No decision context" in r.json()["detail"]


# ---------------------------------------------------------------------------
# simulate-prices
# ---------------------------------------------------------------------------
def test_simulate_prices_returns_a_curve(client, sample_key):
    upc, store = sample_key
    r = client.post(
        "/simulate-prices",
        json={"upc": upc, "store": store, "min_price": 2.0, "max_price": 4.0, "step": 0.25},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["n_candidates"] == len(body["curve"]) >= 8
    prices = [p["candidate_price"] for p in body["curve"]]
    assert prices == sorted(prices)
    assert "monotone_decreasing" in body["diagnostics"]


def test_simulate_prices_rejects_inverted_range(client, sample_key):
    upc, store = sample_key
    r = client.post(
        "/simulate-prices",
        json={"upc": upc, "store": store, "min_price": 5.0, "max_price": 2.0, "step": 0.1},
    )
    assert r.status_code == 422


def test_simulate_prices_rejects_absurd_grid(client, sample_key):
    upc, store = sample_key
    r = client.post(
        "/simulate-prices",
        json={"upc": upc, "store": store, "min_price": 0.01, "max_price": 100.0, "step": 0.01},
    )
    assert r.status_code == 422


def test_simulate_prices_rejects_negative_cost(client, sample_key):
    upc, store = sample_key
    r = client.post(
        "/simulate-prices",
        json={"upc": upc, "store": store, "min_price": 2.0, "max_price": 3.0, "step": 0.5, "unit_cost": -1},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# recommend-price
# ---------------------------------------------------------------------------
def test_recommend_price_contract(client, sample_key):
    upc, store = sample_key
    r = client.post("/recommend-price", json={"upc": upc, "store": store})
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] in {"RECOMMEND_CHANGE", "KEEP_CURRENT", "REVIEW_REQUIRED"}
    assert body["actionable"] is (body["decision"] == "RECOMMEND_CHANGE")
    assert body["reason_codes"]
    assert body["risk_level"] in {"HIGH", "MEDIUM", "LOW"}
    assert body["final_recommended_price"] > 0
    assert body["proposed_candidate_price"] > 0
    if body["decision"] == "KEEP_CURRENT":
        assert body["final_recommended_price"] == body["current_price"]
    assert "model_internal_estimated_profit_uplift_pct" in body
    assert "model_estimated_profit_uplift_pct" not in body


def test_recommend_price_respects_policy_profile(client, sample_key):
    upc, store = sample_key
    conservative = client.post(
        "/recommend-price", json={"upc": upc, "store": store, "policy_profile": "conservative"}
    ).json()
    aggressive = client.post(
        "/recommend-price", json={"upc": upc, "store": store, "policy_profile": "aggressive"}
    ).json()
    assert abs(conservative["price_change_pct"]) <= 0.05 + 1e-9
    assert abs(aggressive["price_change_pct"]) <= 0.20 + 1e-9


def test_recommend_price_rejects_invalid_objective(client, sample_key):
    upc, store = sample_key
    r = client.post("/recommend-price", json={"upc": upc, "store": store, "objective": "market_share"})
    assert r.status_code == 422


def test_recommend_price_revenue_objective_is_supported(client, sample_key):
    upc, store = sample_key
    r = client.post("/recommend-price", json={"upc": upc, "store": store, "objective": "revenue"})
    assert r.status_code == 200
    assert r.json()["objective"] == "revenue"
