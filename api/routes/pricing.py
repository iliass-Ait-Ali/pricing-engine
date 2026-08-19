"""Pricing endpoints: demand prediction, price simulation, recommendation."""

from __future__ import annotations

import numpy as np
from fastapi import APIRouter, HTTPException, Request

from api.schemas import (
    PredictDemandRequest,
    PredictDemandResponse,
    RecommendPriceRequest,
    RecommendPriceResponse,
    SimulatePricesRequest,
    SimulatePricesResponse,
)
from api.state import ContextNotFound
from pricing_engine.features.build import recompute_price_features
from pricing_engine.models.hybrid import attach_reference_price
from pricing_engine.optimization.optimizer import OptimizerError, optimize_price
from pricing_engine.simulation.counterfactual import (
    demand_curve_diagnostics,
    simulate_price_grid,
)

router = APIRouter(tags=["pricing"])


def _state(request: Request):
    state = getattr(request.app.state, "engine", None)
    if state is None:
        raise HTTPException(status_code=503, detail="Model is not loaded yet.")
    return state


def _context(state, upc: int, store: int, week: int | None):
    try:
        return state.context(upc, store, week)
    except ContextNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/predict-demand", response_model=PredictDemandResponse)
def predict_demand(payload: PredictDemandRequest, request: Request) -> PredictDemandResponse:
    state = _state(request)
    row = _context(state, payload.upc, payload.store, payload.week)
    observed_price = float(row["effective_unit_price"].iloc[0])
    price = float(payload.price) if payload.price is not None else observed_price
    # Reference price = the observed price of the context, stamped before the
    # candidate price is substituted (the elasticity response needs p0).
    frame = recompute_price_features(attach_reference_price(row), np.array([price]))
    units = float(state.model.predict(frame)[0])
    return PredictDemandResponse(
        upc=payload.upc,
        store=payload.store,
        week=int(row["week"].iloc[0]),
        week_start_date=str(row["week_start_date"].iloc[0])[:10],
        product_description=str(row["descrip"].iloc[0]) if "descrip" in row else None,
        price_used=price,
        observed_price=observed_price,
        predicted_units=units,
        model_version=getattr(state.model.metadata, "version", None),
    )


@router.post("/simulate-prices", response_model=SimulatePricesResponse)
def simulate_prices(payload: SimulatePricesRequest, request: Request) -> SimulatePricesResponse:
    state = _state(request)
    row = _context(state, payload.upc, payload.store, payload.week)
    cost = payload.unit_cost
    if cost is None:
        raw = float(row["decision_time_unit_cost"].iloc[0])
        cost = raw if np.isfinite(raw) and raw > 0 else None

    grid = np.round(
        np.arange(payload.min_price, payload.max_price + payload.step / 2, payload.step), 4
    )
    grid = grid[grid > 0]
    if grid.size == 0:
        raise HTTPException(status_code=422, detail="The requested price grid is empty.")

    sim = simulate_price_grid(state.model, row, grid, unit_cost=cost)
    curve = [
        {
            "candidate_price": float(r.candidate_price),
            "predicted_units": float(r.predicted_units),
            "expected_revenue": float(r.expected_revenue),
            "expected_gross_profit": None if np.isnan(r.expected_gross_profit) else float(r.expected_gross_profit),
            "expected_margin_rate": None if np.isnan(r.expected_margin_rate) else float(r.expected_margin_rate),
        }
        for r in sim.itertuples()
    ]
    eps = state.model.elasticity_for(row) if hasattr(state.model, "elasticity_for") else None
    src = state.model.elasticity_source(row) if hasattr(state.model, "elasticity_source") else None
    return SimulatePricesResponse(
        upc=payload.upc,
        store=payload.store,
        week=int(row["week"].iloc[0]),
        unit_cost_used=cost,
        model_version=getattr(state.model.metadata, "version", None),
        price_response_method=getattr(getattr(state.model, "method", None), "value", None),
        elasticity_used=None if eps is None or not np.isfinite(eps[0]) else float(eps[0]),
        elasticity_source=None if src is None else str(src[0]),
        n_candidates=len(curve),
        curve=curve,
        diagnostics=demand_curve_diagnostics(sim),
    )


@router.post("/recommend-price", response_model=RecommendPriceResponse)
def recommend_price(payload: RecommendPriceRequest, request: Request) -> RecommendPriceResponse:
    state = _state(request)
    row = _context(state, payload.upc, payload.store, payload.week)
    try:
        rec = optimize_price(
            state.model,
            row,
            cfg=state.cfg,
            policy_profile=payload.policy_profile,
            objective=payload.objective,
            series_stats=state.series_stats(payload.upc, payload.store),
            unit_cost=payload.unit_cost,
        )
    except OptimizerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    d = rec.as_dict()
    d.pop("diagnostics", None)
    return RecommendPriceResponse(**d)
