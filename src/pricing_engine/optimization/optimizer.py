"""Constrained price optimizer.

Flow for one UPC x store decision context:

    eligibility screen
      -> preliminary risk assessment at the current price
      -> risk-adjusted policy (MEDIUM risk tightens the change cap)
      -> feasible price interval (constraints)
      -> candidate grid
      -> vectorised counterfactual simulation (one batched model call)
      -> objective evaluation
      -> materiality check
      -> final risk assessment at the proposed price
      -> decision state + reason codes
      -> Recommendation

Decision states (Phase L):

    RECOMMEND_CHANGE   actionable price change
    KEEP_CURRENT       leave the price alone
    REVIEW_REQUIRED    a proposal exists but is NOT automatically actionable;
                       a human must look at it first

A HIGH-risk context never produces RECOMMEND_CHANGE under the default policy
profiles. Only the explicitly labelled DEMO 'aggressive' profile can override
that, and the configuration says so.

All counterfactual economics returned here are **model-internal estimates**:
the same fitted price-response model both proposes and scores the candidate
prices, so they are an internal simulation, not an unbiased policy value.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

import numpy as np
import pandas as pd

from pricing_engine.config import Config, load_config
from pricing_engine.features.build import recompute_price_features
from pricing_engine.models.hybrid import REFERENCE_PRICE_COLUMN, attach_reference_price
from pricing_engine.optimization.constraints import apply_constraints, build_bounds
from pricing_engine.optimization.objective import ObjectiveError, objective_values
from pricing_engine.optimization.risk import RiskLevel, assess_risk, extrapolation_distance
from pricing_engine.simulation.counterfactual import (
    SimulationError,
    simulate_many,
    simulate_price_grid,
)
from pricing_engine.simulation.price_grid import build_price_grid


class DecisionState(StrEnum):
    RECOMMEND_CHANGE = "RECOMMEND_CHANGE"
    KEEP_CURRENT = "KEEP_CURRENT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class ReasonCode(StrEnum):
    KEEP_CURRENT_OPTIMAL = "KEEP_CURRENT_OPTIMAL"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    INSUFFICIENT_PRICE_VARIATION = "INSUFFICIENT_PRICE_VARIATION"
    OUTSIDE_EXTRAPOLATION_RANGE = "OUTSIDE_EXTRAPOLATION_RANGE"
    COST_UNAVAILABLE = "COST_UNAVAILABLE"
    MARGIN_CONSTRAINT = "MARGIN_CONSTRAINT"
    PRICE_CHANGE_LIMIT = "PRICE_CHANGE_LIMIT"
    PROFIT_UPLIFT_POSITIVE = "PROFIT_UPLIFT_POSITIVE"
    REVENUE_UPLIFT_POSITIVE = "REVENUE_UPLIFT_POSITIVE"
    NO_FEASIBLE_PRICE = "NO_FEASIBLE_PRICE"
    NON_MATERIAL_UPLIFT = "NON_MATERIAL_UPLIFT"
    DEMAND_CURVE_NOT_DECREASING = "DEMAND_CURVE_NOT_DECREASING"
    HIGH_RISK_REVIEW_REQUIRED = "HIGH_RISK_REVIEW_REQUIRED"
    HIGH_RISK_KEEP_CURRENT = "HIGH_RISK_KEEP_CURRENT"
    MEDIUM_RISK_CONSERVATIVE = "MEDIUM_RISK_CONSERVATIVE"
    POOLED_ELASTICITY_FALLBACK = "POOLED_ELASTICITY_FALLBACK"


class OptimizerError(RuntimeError):
    """Raised when a recommendation cannot be produced at all."""


@dataclass
class Recommendation:
    """A fully auditable price recommendation."""

    upc: int
    store: int
    decision_week: int
    decision_week_start_date: str
    objective: str
    policy_profile: str
    model_version: str | None
    price_response_method: str | None
    elasticity_used: float | None
    elasticity_source: str | None
    current_price: float
    proposed_candidate_price: float
    final_recommended_price: float
    proposed_price_change_pct: float
    price_change_pct: float
    decision: str
    actionable: bool
    predicted_units_current: float
    predicted_units_recommended: float
    expected_revenue_current: float
    expected_revenue_recommended: float
    expected_gross_profit_current: float | None
    expected_gross_profit_recommended: float | None
    model_internal_estimated_profit_uplift_pct: float | None
    model_internal_estimated_revenue_uplift_pct: float | None
    realisable_profit_uplift_pct: float | None
    unit_cost_used: float | None
    risk_level: str
    reason_codes: list[str] = field(default_factory=list)
    risk_notes: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    product_description: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _price_response_method(model) -> str:
    method = getattr(model, "method", None)
    return str(getattr(method, "value", method) or "ml")


def _elasticity_for_row(model, frame: pd.DataFrame) -> tuple[float | None, str | None]:
    if not hasattr(model, "elasticity_for"):
        return None, "ml_native"
    try:
        eps = float(np.asarray(model.elasticity_for(frame))[0])
        src = str(np.asarray(model.elasticity_source(frame))[0])
    except Exception:  # noqa: BLE001 - diagnostics must never break a recommendation
        return None, None
    return (None if not np.isfinite(eps) else eps), src


def _build(
    *,
    context: pd.Series,
    objective: str,
    policy_profile: str,
    model_version: str | None,
    price_response_method: str | None,
    elasticity_used: float | None,
    elasticity_source: str | None,
    current_price: float,
    proposed_candidate_price: float,
    units_current: float,
    units_recommended: float,
    unit_cost: float | None,
    decision: DecisionState,
    reasons: list[str],
    risk_level: str,
    risk_notes: list[str] | None = None,
    constraints: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> Recommendation:
    actionable = decision is DecisionState.RECOMMEND_CHANGE
    # The FINAL price is the proposal only when the decision state is
    # actionable. A REVIEW_REQUIRED proposal never becomes a business price on
    # its own, so it must never be reported as one.
    final_price = proposed_candidate_price if actionable else current_price

    rev_cur = current_price * units_current
    rev_rec = proposed_candidate_price * units_recommended
    gp_cur = (current_price - unit_cost) * units_current if unit_cost is not None else None
    gp_rec = (
        (proposed_candidate_price - unit_cost) * units_recommended
        if unit_cost is not None
        else None
    )
    profit_uplift = (
        float((gp_rec - gp_cur) / abs(gp_cur))
        if gp_cur not in (None, 0.0) and gp_rec is not None
        else None
    )
    revenue_uplift = float((rev_rec - rev_cur) / rev_cur) if rev_cur else None

    return Recommendation(
        upc=int(context["upc"]),
        store=int(context["store"]),
        decision_week=int(context["week"]),
        decision_week_start_date=str(pd.Timestamp(context["week_start_date"]).date()),
        objective=objective,
        policy_profile=policy_profile,
        model_version=model_version,
        price_response_method=price_response_method,
        elasticity_used=elasticity_used,
        elasticity_source=elasticity_source,
        current_price=float(current_price),
        proposed_candidate_price=float(proposed_candidate_price),
        final_recommended_price=float(final_price),
        proposed_price_change_pct=float(proposed_candidate_price / current_price - 1.0),
        price_change_pct=float(final_price / current_price - 1.0),
        decision=decision.value,
        actionable=actionable,
        predicted_units_current=float(units_current),
        predicted_units_recommended=float(units_recommended),
        expected_revenue_current=float(rev_cur),
        expected_revenue_recommended=float(rev_rec),
        expected_gross_profit_current=float(gp_cur) if gp_cur is not None else None,
        expected_gross_profit_recommended=float(gp_rec) if gp_rec is not None else None,
        model_internal_estimated_profit_uplift_pct=profit_uplift,
        model_internal_estimated_revenue_uplift_pct=revenue_uplift,
        realisable_profit_uplift_pct=(
            profit_uplift if (actionable and profit_uplift is not None) else 0.0
        ),
        unit_cost_used=float(unit_cost) if unit_cost is not None else None,
        risk_level=risk_level,
        reason_codes=sorted(set(reasons)),
        risk_notes=list(risk_notes or []),
        constraints=constraints or {},
        diagnostics=diagnostics or {},
        product_description=str(context.get("descrip")) if "descrip" in context else None,
    )


# ---------------------------------------------------------------------------
# The decision pipeline is split into two stages so that the single-context
# entry point and the vectorised batch entry point run *the same* code:
#
#   _prepare_context()   everything up to (and including) the candidate grid
#   _finalise_context()  objective, materiality, risk gate, decision state
#
# The only thing that differs between the two callers is HOW the candidate
# grid is scored: one model call per context (`simulate_price_grid`) or one
# batched model call for many contexts (`simulate_many`). No pricing logic,
# guardrail, elasticity selection or risk rule lives in either caller.
# ---------------------------------------------------------------------------
@dataclass
class _PreparedContext:
    """State carried from the pre-simulation stage into the decision stage."""

    row: pd.Series
    frame: pd.DataFrame | None
    position: int
    objective: str
    policy_profile: str
    policy: dict[str, Any]
    model_version: str | None
    price_response_method: str
    elasticity_used: float | None
    elasticity_source: str | None
    current_price: float
    unit_cost: float | None
    stats: dict[str, Any]
    risk_cfg: dict[str, Any]
    high_risk_action: str
    units_current: float
    reasons: list[str]
    preliminary_risk: Any
    constraint_info: dict[str, Any]
    bounds_binding: list[str]
    candidates: np.ndarray


def _risk_at(
    price: float,
    *,
    current_price: float,
    stats: dict[str, Any],
    risk_cfg: dict[str, Any],
    elasticity_source: str | None,
):
    return assess_risk(
        n_obs=stats.get("n_obs"),
        n_distinct_prices=stats.get("n_distinct_prices"),
        price_cv=stats.get("price_cv"),
        recommended_price=price,
        hist_price_min=stats.get("price_min"),
        hist_price_max=stats.get("price_max"),
        price_change_pct=price / current_price - 1.0,
        cfg_risk=risk_cfg,
        promotion_share=stats.get("promotion_share"),
        elasticity_source=elasticity_source,
    )


def _keep(
    prep: _PreparedContext,
    reasons: list[str],
    risk,
    *,
    constraints: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
    units: float | None = None,
) -> Recommendation:
    value = units if units is not None else prep.units_current
    return _build(
        context=prep.row,
        objective=prep.objective,
        policy_profile=prep.policy_profile,
        model_version=prep.model_version,
        price_response_method=prep.price_response_method,
        elasticity_used=prep.elasticity_used,
        elasticity_source=prep.elasticity_source,
        current_price=prep.current_price,
        proposed_candidate_price=prep.current_price,
        units_current=value,
        units_recommended=value,
        unit_cost=prep.unit_cost,
        decision=DecisionState.KEEP_CURRENT,
        reasons=reasons,
        risk_level=risk.level.value,
        risk_notes=risk.notes,
        constraints=constraints,
        diagnostics=diagnostics,
    )


def _context_frame(context: pd.Series | pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    if isinstance(context, pd.DataFrame):
        if len(context) != 1:
            raise OptimizerError(f"optimize_price expects one context row, got {len(context)}")
        return context.iloc[0], context
    return context, context.to_frame().T


def _check_current_price(row: pd.Series) -> float:
    current_price = float(row["effective_unit_price"])
    if not np.isfinite(current_price) or current_price <= 0:
        raise OptimizerError(
            f"Cannot optimise UPC {row.get('upc')} at store {row.get('store')}: "
            f"the current effective unit price is {current_price!r}."
        )
    return current_price


def _resolve_unit_cost(row: pd.Series, unit_cost: float | None) -> float | None:
    if unit_cost is None:
        raw_cost = row.get("decision_time_unit_cost", np.nan)
        unit_cost = float(raw_cost) if raw_cost is not None and np.isfinite(raw_cost) else None
    if unit_cost is not None and unit_cost <= 0:
        unit_cost = None
    return unit_cost


def _prepare_context(
    model,
    *,
    row: pd.Series,
    frame: pd.DataFrame | None,
    position: int,
    cfg: Config,
    policy_profile: str | None,
    objective: str | None,
    series_stats: dict[str, Any] | None,
    unit_cost: float | None,
    policy_override: dict[str, Any] | None,
    current_price: float,
    units_current: float,
    elasticity_used: float | None,
    elasticity_source: str | None,
) -> tuple[Recommendation | None, _PreparedContext | None]:
    """Run every stage up to the candidate grid.

    Returns ``(recommendation, None)`` when the context is decided before any
    counterfactual has to be scored, otherwise ``(None, prepared)``.
    """
    policy_profile = policy_profile or "standard"
    policy = dict(cfg.policy(policy_profile))
    if policy_override:
        policy.update(policy_override)
    objective = objective or str(cfg.get("optimization.objective", "gross_profit"))

    stats = series_stats or {}
    risk_cfg = cfg.require("risk")
    high_risk_action = str(policy.get("high_risk_action", "review_required")).lower()

    prep = _PreparedContext(
        row=row,
        frame=frame,
        position=position,
        objective=objective,
        policy_profile=policy_profile,
        policy=policy,
        model_version=getattr(getattr(model, "metadata", None), "version", None),
        price_response_method=_price_response_method(model),
        elasticity_used=elasticity_used,
        elasticity_source=elasticity_source,
        current_price=current_price,
        unit_cost=unit_cost,
        stats=stats,
        risk_cfg=risk_cfg,
        high_risk_action=high_risk_action,
        units_current=units_current,
        reasons=[],
        preliminary_risk=None,
        constraint_info={},
        bounds_binding=[],
        candidates=np.empty(0, dtype="float64"),
    )

    def risk_at(price: float):
        return _risk_at(
            price,
            current_price=current_price,
            stats=stats,
            risk_cfg=risk_cfg,
            elasticity_source=elasticity_source,
        )

    # -- eligibility screen ---------------------------------------------------
    elig = cfg.require("eligibility")
    n_obs = stats.get("n_obs")
    n_prices = stats.get("n_distinct_prices")
    price_cv = stats.get("price_cv")
    reasons: list[str] = []
    if n_obs is not None and n_obs < int(elig["min_observations"]):
        reasons.append(ReasonCode.INSUFFICIENT_HISTORY.value)
    if (n_prices is not None and n_prices < int(elig["min_distinct_prices"])) or (
        price_cv is not None and price_cv < float(elig["min_price_cv"])
    ):
        reasons.append(ReasonCode.INSUFFICIENT_PRICE_VARIATION.value)
    if objective == "gross_profit" and unit_cost is None:
        reasons.append(ReasonCode.COST_UNAVAILABLE.value)

    preliminary_risk = risk_at(current_price)
    prep.preliminary_risk = preliminary_risk
    if reasons:
        reasons.append(ReasonCode.KEEP_CURRENT_OPTIMAL.value)
        return _keep(prep, reasons, preliminary_risk), None

    # -- risk gate before any price is proposed ------------------------------
    if preliminary_risk.level is RiskLevel.HIGH and high_risk_action == "keep_current":
        return (
            _keep(
                prep,
                [
                    ReasonCode.HIGH_RISK_KEEP_CURRENT.value,
                    ReasonCode.LOW_CONFIDENCE.value,
                    ReasonCode.KEEP_CURRENT_OPTIMAL.value,
                ],
                preliminary_risk,
            ),
            None,
        )

    if preliminary_risk.level is RiskLevel.MEDIUM:
        cap = policy.get("medium_risk_max_price_change_pct")
        if cap is not None:
            policy["max_price_change_pct"] = min(
                float(policy.get("max_price_change_pct", 0.10)), float(cap)
            )
        reasons.append(ReasonCode.MEDIUM_RISK_CONSERVATIVE.value)
    if elasticity_source == "pooled_fallback":
        reasons.append(ReasonCode.POOLED_ELASTICITY_FALLBACK.value)
    prep.reasons = reasons

    # -- constraints ----------------------------------------------------------
    bounds = build_bounds(
        current_price,
        policy=policy,
        unit_cost=unit_cost,
        hist_price_min=stats.get("price_min"),
        hist_price_max=stats.get("price_max"),
    )
    constraint_info = {"bounds": [bounds.low, bounds.high], **bounds.details}
    prep.constraint_info = constraint_info
    prep.bounds_binding = list(bounds.binding)
    if bounds.is_empty:
        return (
            _keep(
                prep,
                [
                    *reasons,
                    ReasonCode.NO_FEASIBLE_PRICE.value,
                    ReasonCode.KEEP_CURRENT_OPTIMAL.value,
                ],
                preliminary_risk,
                constraints=constraint_info,
            ),
            None,
        )

    grid = build_price_grid(
        current_price,
        low=bounds.low,
        high=bounds.high,
        step=float(cfg.get("optimization.price_step", 0.05)),
        rounding=float(cfg.get("optimization.price_rounding", 0.01)),
        charm=bool(cfg.get("optimization.charm_pricing", False)),
    )
    constrained = apply_constraints(grid.prices, bounds)
    candidates = grid.prices[constrained.feasible_mask]
    if candidates.size == 0:
        return (
            _keep(
                prep,
                [
                    *reasons,
                    ReasonCode.NO_FEASIBLE_PRICE.value,
                    ReasonCode.KEEP_CURRENT_OPTIMAL.value,
                ],
                preliminary_risk,
                constraints=constraint_info,
            ),
            None,
        )
    prep.candidates = candidates
    return None, prep


def _finalise_context(prep: _PreparedContext, sim: pd.DataFrame) -> Recommendation:
    """Objective, materiality, final risk gate and decision state."""
    try:
        values = objective_values(
            sim["candidate_price"].to_numpy(),
            sim["predicted_units"].to_numpy(),
            objective=prep.objective,
            unit_cost=prep.unit_cost,
        )
    except ObjectiveError as exc:
        raise OptimizerError(str(exc)) from exc

    current_price = prep.current_price
    best_idx = int(np.argmax(values))
    best_price = float(sim["candidate_price"].iloc[best_idx])
    best_units = float(sim["predicted_units"].iloc[best_idx])

    cur_idx = int(np.argmin(np.abs(sim["candidate_price"].to_numpy() - current_price)))
    units_current_grid = float(sim["predicted_units"].iloc[cur_idx])
    value_current = float(values[cur_idx])
    value_best = float(values[best_idx])
    uplift = (value_best - value_current) / abs(value_current) if value_current else np.nan
    price_change_pct = best_price / current_price - 1.0

    candidates = prep.candidates
    stats = prep.stats
    diagnostics = {
        "n_candidates": int(candidates.size),
        "grid_low": float(candidates.min()),
        "grid_high": float(candidates.max()),
        "objective_current": value_current,
        "objective_best": value_best,
        "extrapolation_distance": extrapolation_distance(
            best_price, stats.get("price_min"), stats.get("price_max")
        ),
        "demand_curve_monotone_decreasing": bool(
            np.all(np.diff(sim.sort_values("candidate_price")["predicted_units"].to_numpy()) <= 1e-9)
        ),
        "price_response_method": prep.price_response_method,
        "elasticity_used": prep.elasticity_used,
        "elasticity_source": prep.elasticity_source,
        "preliminary_risk_level": prep.preliminary_risk.level.value,
        "series_stats": {
            k: stats.get(k)
            for k in ("n_obs", "n_distinct_prices", "price_cv", "price_min", "price_max")
        },
    }

    final_risk = _risk_at(
        best_price,
        current_price=current_price,
        stats=stats,
        risk_cfg=prep.risk_cfg,
        elasticity_source=prep.elasticity_source,
    )
    reasons = list(prep.reasons)
    reasons.extend(prep.bounds_binding)
    if not diagnostics["demand_curve_monotone_decreasing"]:
        reasons.append(ReasonCode.DEMAND_CURVE_NOT_DECREASING.value)

    materiality = float(prep.policy.get("materiality_threshold_pct", 0.01))
    if abs(price_change_pct) < 1e-9:
        return _keep(
            prep,
            [*reasons, ReasonCode.KEEP_CURRENT_OPTIMAL.value],
            final_risk,
            constraints=prep.constraint_info,
            diagnostics=diagnostics,
            units=units_current_grid,
        )
    if not np.isfinite(uplift) or uplift < materiality:
        return _keep(
            prep,
            [*reasons, ReasonCode.NON_MATERIAL_UPLIFT.value, ReasonCode.KEEP_CURRENT_OPTIMAL.value],
            final_risk,
            constraints=prep.constraint_info,
            diagnostics=diagnostics,
            units=units_current_grid,
        )

    reasons.append(
        ReasonCode.PROFIT_UPLIFT_POSITIVE.value
        if prep.objective == "gross_profit"
        else ReasonCode.REVENUE_UPLIFT_POSITIVE.value
    )

    # -- final risk gate ------------------------------------------------------
    decision = DecisionState.RECOMMEND_CHANGE
    if final_risk.level is RiskLevel.HIGH and prep.high_risk_action != "recommend":
        if prep.high_risk_action == "keep_current":
            return _keep(
                prep,
                [
                    *reasons,
                    ReasonCode.HIGH_RISK_KEEP_CURRENT.value,
                    ReasonCode.LOW_CONFIDENCE.value,
                    ReasonCode.KEEP_CURRENT_OPTIMAL.value,
                ],
                final_risk,
                constraints=prep.constraint_info,
                diagnostics=diagnostics,
                units=units_current_grid,
            )
        decision = DecisionState.REVIEW_REQUIRED
        reasons.extend(
            [ReasonCode.HIGH_RISK_REVIEW_REQUIRED.value, ReasonCode.LOW_CONFIDENCE.value]
        )

    return _build(
        context=prep.row,
        objective=prep.objective,
        policy_profile=prep.policy_profile,
        model_version=prep.model_version,
        price_response_method=prep.price_response_method,
        elasticity_used=prep.elasticity_used,
        elasticity_source=prep.elasticity_source,
        current_price=current_price,
        proposed_candidate_price=best_price,
        units_current=units_current_grid,
        units_recommended=best_units,
        unit_cost=prep.unit_cost,
        decision=decision,
        reasons=reasons,
        risk_level=final_risk.level.value,
        risk_notes=final_risk.notes,
        constraints=prep.constraint_info,
        diagnostics=diagnostics,
    )


def optimize_price(
    model,
    context: pd.Series | pd.DataFrame,
    *,
    cfg: Config | None = None,
    policy_profile: str | None = None,
    objective: str | None = None,
    series_stats: dict[str, Any] | None = None,
    unit_cost: float | None = None,
    policy_override: dict[str, Any] | None = None,
) -> Recommendation:
    """Recommend a price, keep the current one, or escalate for human review.

    ``policy_override`` merges on top of the named profile. It exists for
    explicit what-if analysis (e.g. the sensitivity scenarios in
    reports/08_PRICE_RESPONSE_COMPARISON.md), never as a way to quietly loosen
    the production guardrails.
    """
    cfg = cfg or load_config()
    row, frame = _context_frame(context)
    current_price = _check_current_price(row)
    unit_cost = _resolve_unit_cost(row, unit_cost)

    # Price features are recomputed at the current price so that any caller may
    # pass a raw context row and still be scored exactly as candidates are.
    if REFERENCE_PRICE_COLUMN not in frame.columns:
        frame = attach_reference_price(frame, current_price)
    frame = recompute_price_features(frame, np.array([current_price]))
    elasticity_used, elasticity_source = _elasticity_for_row(model, frame)
    units_current = float(model.predict(frame)[0])

    rec, prep = _prepare_context(
        model,
        row=row,
        frame=frame,
        position=0,
        cfg=cfg,
        policy_profile=policy_profile,
        objective=objective,
        series_stats=series_stats,
        unit_cost=unit_cost,
        policy_override=policy_override,
        current_price=current_price,
        units_current=units_current,
        elasticity_used=elasticity_used,
        elasticity_source=elasticity_source,
    )
    if rec is not None:
        return rec
    assert prep is not None and prep.frame is not None

    sim = simulate_price_grid(model, prep.frame, prep.candidates, unit_cost=prep.unit_cost)
    return _finalise_context(prep, sim)


# ---------------------------------------------------------------------------
# Vectorised batch path
# ---------------------------------------------------------------------------
SIM_COLUMNS = (
    "candidate_price",
    "predicted_units",
    "expected_revenue",
    "unit_cost",
    "expected_gross_profit",
    "expected_margin_rate",
)


def _elasticity_for_frame(model, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Per-row elasticity and provenance for a whole frame.

    Mirrors :func:`_elasticity_for_row`, including its rule that a diagnostics
    failure must never break a recommendation.
    """
    n = len(frame)
    if not hasattr(model, "elasticity_for"):
        return np.full(n, np.nan), np.array(["ml_native"] * n, dtype=object)
    try:
        eps = np.asarray(model.elasticity_for(frame), dtype="float64")
        src = np.asarray(model.elasticity_source(frame), dtype=object)
    except Exception:  # noqa: BLE001 - diagnostics must never break a recommendation
        return np.full(n, np.nan), np.full(n, None, dtype=object)
    return eps, src


def optimize_price_batch(
    model,
    contexts: pd.DataFrame,
    *,
    cfg: Config | None = None,
    policy_profile: str | None = None,
    objective: str | None = None,
    series_stats: Sequence[dict[str, Any]] | None = None,
    unit_costs: Sequence[float | None] | None = None,
    policy_override: dict[str, Any] | None = None,
    max_simulation_rows: int = 250_000,
) -> list[Recommendation]:
    """Recommend prices for many contexts with batched model calls.

    Semantically identical to calling :func:`optimize_price` once per row: the
    same preparation, guardrails, elasticity selection, risk policy and
    decision code run for every context. The only difference is *how many model
    calls* it takes - the demand model is called once per group of contexts
    instead of once per context, through the vectorised
    :func:`~pricing_engine.simulation.counterfactual.simulate_many` path.
    ``tests/test_batch_equivalence.py`` pins the two paths to each other.
    """
    cfg = cfg or load_config()
    if len(contexts) == 0:
        return []
    contexts = contexts.reset_index(drop=True)

    prices = np.empty(len(contexts), dtype="float64")
    rows: list[pd.Series] = []
    costs: list[float | None] = []
    for i in range(len(contexts)):
        row = contexts.iloc[i]
        rows.append(row)
        prices[i] = _check_current_price(row)
        costs.append(_resolve_unit_cost(row, None if unit_costs is None else unit_costs[i]))

    frame = contexts
    if REFERENCE_PRICE_COLUMN not in frame.columns:
        frame = attach_reference_price(frame, prices)
    frame = recompute_price_features(frame, prices)

    eps_all, src_all = _elasticity_for_frame(model, frame)
    units_current_all = np.asarray(model.predict(frame), dtype="float64")

    results: list[Recommendation | None] = [None] * len(contexts)
    pending: list[_PreparedContext] = []
    for i in range(len(contexts)):
        eps = float(eps_all[i])
        src = src_all[i]
        rec, prep = _prepare_context(
            model,
            row=rows[i],
            frame=None,
            position=i,
            cfg=cfg,
            policy_profile=policy_profile,
            objective=objective,
            series_stats=None if series_stats is None else series_stats[i],
            unit_cost=costs[i],
            policy_override=policy_override,
            current_price=float(prices[i]),
            units_current=float(units_current_all[i]),
            elasticity_used=None if not np.isfinite(eps) else eps,
            elasticity_source=None if src is None else str(src),
        )
        if rec is not None:
            results[i] = rec
        else:
            assert prep is not None
            pending.append(prep)

    # Contexts whose candidate grids have the same width are scored together:
    # `simulate_many` needs a rectangular (n_contexts x n_candidates) matrix.
    by_width: dict[int, list[_PreparedContext]] = {}
    for prep in pending:
        by_width.setdefault(int(prep.candidates.size), []).append(prep)

    for width, group in by_width.items():
        chunk = max(1, int(max_simulation_rows // max(width, 1)))
        for start in range(0, len(group), chunk):
            block = group[start : start + chunk]
            positions = [p.position for p in block]
            price_matrix = np.vstack([p.candidates for p in block])
            block_costs = np.array(
                [np.nan if p.unit_cost is None else float(p.unit_cost) for p in block],
                dtype="float64",
            )
            sim = simulate_many(model, frame.iloc[positions], price_matrix, unit_costs=block_costs)
            columns = {c: sim[c].to_numpy() for c in SIM_COLUMNS}
            if np.any(columns["predicted_units"] < 0):
                raise SimulationError("demand model returned negative predicted units")
            for k, prep in enumerate(block):
                lo, hi = k * width, (k + 1) * width
                sub = pd.DataFrame({c: columns[c][lo:hi] for c in SIM_COLUMNS})
                results[prep.position] = _finalise_context(prep, sub)

    missing = [i for i, r in enumerate(results) if r is None]
    if missing:  # pragma: no cover - defensive
        raise OptimizerError(f"batch optimisation produced no result for rows {missing[:5]}")
    return [r for r in results if r is not None]
