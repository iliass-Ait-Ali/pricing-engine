"""Pricing endpoints: demand prediction, price simulation, recommendation.

Thin HTTP adapters over :class:`pricing_engine.serving.AppState`, which holds
the one implementation shared with the dashboard and the pricing copilot.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from api.schemas import (
    PredictDemandRequest,
    PredictDemandResponse,
    RecommendPriceRequest,
    RecommendPriceResponse,
    SimulatePricesRequest,
    SimulatePricesResponse,
)
from pricing_engine.optimization.optimizer import OptimizerError
from pricing_engine.serving import ContextNotFound, ServingError

router = APIRouter(tags=["pricing"])


def _state(request: Request):
    state = getattr(request.app.state, "engine", None)
    if state is None:
        raise HTTPException(status_code=503, detail="Model is not loaded yet.")
    return state


def _serve(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ContextNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ServingError, OptimizerError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/predict-demand", response_model=PredictDemandResponse)
def predict_demand(payload: PredictDemandRequest, request: Request) -> PredictDemandResponse:
    state = _state(request)
    out = _serve(state.predict_demand, payload.upc, payload.store, payload.week, payload.price)
    return PredictDemandResponse(**out)


@router.post("/simulate-prices", response_model=SimulatePricesResponse)
def simulate_prices(payload: SimulatePricesRequest, request: Request) -> SimulatePricesResponse:
    state = _state(request)
    out = _serve(
        state.simulate_prices,
        payload.upc,
        payload.store,
        payload.min_price,
        payload.max_price,
        payload.step,
        week=payload.week,
        unit_cost=payload.unit_cost,
    )
    return SimulatePricesResponse(**out)


@router.post("/recommend-price", response_model=RecommendPriceResponse)
def recommend_price(payload: RecommendPriceRequest, request: Request) -> RecommendPriceResponse:
    state = _state(request)
    rec = _serve(
        state.recommend,
        payload.upc,
        payload.store,
        payload.week,
        policy_profile=payload.policy_profile,
        objective=payload.objective,
        unit_cost=payload.unit_cost,
    )
    d = rec.as_dict()
    d.pop("diagnostics", None)
    return RecommendPriceResponse(**d)
