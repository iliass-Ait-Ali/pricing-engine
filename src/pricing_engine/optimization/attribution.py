"""Constraint attribution for price recommendations (Phase M audit layer).

The question this module exists to answer is uncomfortable and therefore worth
answering precisely:

    when the engine recommends a price, is that price chosen by the estimated
    demand response, or is it simply the closest point the guardrails allow?

To answer it we need, for every decision context, four different prices:

``unconstrained_optimum``
    the profit-maximising price with **no** business guardrails, on a wide
    research grid. For the hybrid constant-elasticity response this also has a
    closed form (see :func:`analytic_unconstrained_optimum`).
``constrained_optimum``
    the profit-maximising price inside the feasible interval produced by the
    policy layer.
``proposed_candidate_price``
    the constrained optimum after the materiality check.
``final_recommended_price``
    what the business would actually charge: the proposal only if the decision
    state is actionable, otherwise the current price.

and, for each individual constraint, whether it

1. is **present** (configured and applicable to this context),
2. **removes candidate prices** from the research grid,
3. is **binding at the optimum** (its own bound is the active edge), and
4. **changes the final decision** (leave-one-out: relax only this constraint
   and see whether the decision state or the final price moves).

Exact replica, not an approximation
-----------------------------------
Running ``optimize_price`` eleven times per context (baseline + one
leave-one-out per constraint) over thousands of contexts is slow, because each
call pays pandas and scikit-learn overhead. So the audit uses a *replica* of
the optimizer written directly against the closed-form hybrid demand curve

    Q(p) = clip(Q0 * (p / p0) ** epsilon, 0, cap)

``tests/test_attribution.py`` asserts that the replica reproduces
``optimize_price`` exactly - decision state, final price and reason codes - on
real decision contexts. The replica is only valid for the elasticity-based
price responses (``pooled`` / ``shrunk``); the native ML response has no closed
form and must be audited with the real optimizer.

A finding that falls out of the algebra
---------------------------------------
Under the hybrid response, ``Q0`` is a positive multiplicative constant across
all candidate prices, so it cancels out of ``argmax_p (p - c) Q(p)``. The
*price* chosen for a context therefore does not depend on the ML forecast at
all - only on ``(p0, c, epsilon)`` and the guardrails. The forecast determines
the predicted volume and the euro amounts, not the choice. That is verified
numerically in the tests (scaling the base model by 10x leaves every
recommended price unchanged) and reported in
``reports/12_MODEL_VALUE_ABLATION.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from pricing_engine.optimization.risk import RiskLevel, assess_risk
from pricing_engine.simulation.price_grid import build_price_grid

#: Every constraint the audit tracks, in the order they are reported.
CONSTRAINTS: tuple[str, ...] = (
    "MAX_PRICE_CHANGE",
    "MIN_PRICE",
    "MAX_PRICE",
    "MIN_MARGIN",
    "COST_FLOOR",
    "EXTRAPOLATION",
    "ROUNDING",
    "MATERIALITY",
    "RISK_GATE",
    "HISTORICAL_SUPPORT",
)

#: Constraints implemented as an interval on the candidate price.
INTERVAL_CONSTRAINTS: tuple[str, ...] = (
    "MAX_PRICE_CHANGE",
    "MIN_PRICE",
    "MAX_PRICE",
    "MIN_MARGIN",
    "COST_FLOOR",
    "EXTRAPOLATION",
)

#: Wide research band used as the "no guardrails" reference, expressed as a
#: multiple of the current price. Deliberately generous but finite: an
#: unbounded grid is not a price policy, it is a divergence.
RESEARCH_LOW_MULTIPLE = 0.20
RESEARCH_HIGH_MULTIPLE = 5.00

_TOL = 1e-9


@dataclass
class PricingContext:
    """Everything the replica needs about one UPC x store x week decision."""

    upc: int
    store: int
    week: int
    current_price: float
    unit_cost: float | None
    baseline_units: float          # Q0 = base model prediction at the current price
    epsilon: float | None          # None for the native ML response
    elasticity_source: str | None
    n_obs: int | None
    n_distinct_prices: int | None
    price_cv: float | None
    hist_price_min: float | None
    hist_price_max: float | None
    promotion_share: float | None = None
    prediction_cap: float | None = None

    def demand(self, prices: np.ndarray) -> np.ndarray:
        """Hybrid demand curve on a candidate grid."""
        if self.epsilon is None:
            raise ValueError("the replica requires an elasticity-based price response")
        p = np.asarray(prices, dtype="float64")
        with np.errstate(over="ignore", invalid="ignore"):
            units = self.baseline_units * np.power(p / self.current_price, self.epsilon)
        return np.clip(units, 0.0, self.prediction_cap)


@dataclass
class Thresholds:
    """The non-policy configuration the optimizer reads."""

    eligibility_min_observations: int
    eligibility_min_distinct_prices: int
    eligibility_min_price_cv: float
    risk_bands: dict[str, Any]
    price_step: float
    price_rounding: float
    charm: bool = False
    objective: str = "gross_profit"

    @classmethod
    def from_config(cls, cfg) -> Thresholds:
        elig = cfg.require("eligibility")
        return cls(
            eligibility_min_observations=int(elig["min_observations"]),
            eligibility_min_distinct_prices=int(elig["min_distinct_prices"]),
            eligibility_min_price_cv=float(elig["min_price_cv"]),
            risk_bands=cfg.require("risk"),
            price_step=float(cfg.get("optimization.price_step", 0.05)),
            price_rounding=float(cfg.get("optimization.price_rounding", 0.01)),
            charm=bool(cfg.get("optimization.charm_pricing", False)),
            objective=str(cfg.get("optimization.objective", "gross_profit")),
        )


@dataclass
class Evaluation:
    """One replica run of the full pipeline for one context."""

    decision: str
    proposed_candidate_price: float
    final_recommended_price: float
    actionable: bool
    risk_level: str
    reason_codes: list[str]
    bounds: tuple[float, float]
    constrained_optimum: float
    objective_current: float
    objective_best: float
    uplift: float
    binding: list[str] = field(default_factory=list)
    n_candidates: int = 0


# ---------------------------------------------------------------------------
# constraint intervals
# ---------------------------------------------------------------------------
def constraint_intervals(
    ctx: PricingContext,
    policy: dict[str, Any],
    *,
    effective_max_change: float | None = None,
) -> dict[str, tuple[float, float]]:
    """Interval each guardrail imposes on the candidate price, on its own.

    ``(-inf, inf)`` means the constraint is configured but cannot bite for this
    context (for example a cost floor with no cost available).
    """
    p0 = ctx.current_price
    out: dict[str, tuple[float, float]] = {}

    max_change = (
        float(policy.get("max_price_change_pct", 0.10))
        if effective_max_change is None
        else float(effective_max_change)
    )
    out["MAX_PRICE_CHANGE"] = (p0 * (1.0 - max_change), p0 * (1.0 + max_change))

    out["MIN_PRICE"] = (float(policy.get("abs_min_price", 0.01)), np.inf)
    abs_max = policy.get("abs_max_price")
    out["MAX_PRICE"] = (-np.inf, float(abs_max) if abs_max is not None else np.inf)

    tol = float(policy.get("extrapolation_tolerance_pct", 0.05))
    lo = (
        float(ctx.hist_price_min) * (1.0 - tol)
        if ctx.hist_price_min is not None and np.isfinite(ctx.hist_price_min)
        else -np.inf
    )
    hi = (
        float(ctx.hist_price_max) * (1.0 + tol)
        if ctx.hist_price_max is not None and np.isfinite(ctx.hist_price_max)
        else np.inf
    )
    out["EXTRAPOLATION"] = (lo, hi)

    cost = ctx.unit_cost
    allow_below_cost = bool(policy.get("allow_below_cost", False))
    if cost is not None and np.isfinite(cost) and not allow_below_cost:
        out["COST_FLOOR"] = (float(cost), np.inf)
    else:
        out["COST_FLOOR"] = (-np.inf, np.inf)

    min_margin = float(policy.get("min_gross_margin_rate", 0.0))
    if cost is not None and np.isfinite(cost) and min_margin > 0:
        out["MIN_MARGIN"] = (float(cost) / (1.0 - min_margin), np.inf)
    else:
        out["MIN_MARGIN"] = (-np.inf, np.inf)

    return out


def analytic_unconstrained_optimum(cost: float | None, epsilon: float | None) -> float:
    """Closed-form profit optimum of a constant-elasticity demand curve.

        max_p (p - c) * Q0 * (p / p0) ** e   ->   p* = c * e / (1 + e)   for e < -1

    Returns ``+inf`` when demand is inelastic (``-1 < e < 0``): profit then
    rises without bound in price, which is exactly why an inelastic estimate
    must never be allowed to price on its own.
    """
    if cost is None or epsilon is None or not np.isfinite(cost) or not np.isfinite(epsilon):
        return float("nan")
    if epsilon >= -1.0:
        return float("inf")
    return float(cost * epsilon / (1.0 + epsilon))


def _objective(prices: np.ndarray, units: np.ndarray, cost: float | None, objective: str) -> np.ndarray:
    if objective == "revenue":
        return prices * units
    if cost is None:
        raise ValueError("gross-profit objective needs a unit cost")
    return (prices - float(cost)) * units


# ---------------------------------------------------------------------------
# the replica
# ---------------------------------------------------------------------------
def evaluate(
    ctx: PricingContext,
    policy: dict[str, Any],
    thresholds: Thresholds,
    *,
    disabled: frozenset[str] = frozenset(),
) -> Evaluation:
    """Reproduce ``optimize_price`` for one context under a policy.

    ``disabled`` switches individual guardrails off, which is what makes the
    leave-one-out attribution and the guardrail ablation possible without
    editing configuration.
    """
    policy = dict(policy)
    p0 = ctx.current_price
    cost = ctx.unit_cost
    objective = thresholds.objective

    def risk_at(price: float):
        return assess_risk(
            n_obs=ctx.n_obs,
            n_distinct_prices=ctx.n_distinct_prices,
            price_cv=ctx.price_cv,
            recommended_price=price,
            hist_price_min=ctx.hist_price_min,
            hist_price_max=ctx.hist_price_max,
            price_change_pct=price / p0 - 1.0,
            cfg_risk=thresholds.risk_bands,
            promotion_share=ctx.promotion_share,
            elasticity_source=ctx.elasticity_source,
        )

    def keep(reasons: list[str], risk, bounds=(np.nan, np.nan), opt=np.nan,
             obj_cur=np.nan, obj_best=np.nan, uplift=np.nan, binding=None, n_cand=0) -> Evaluation:
        return Evaluation(
            decision="KEEP_CURRENT",
            proposed_candidate_price=p0,
            final_recommended_price=p0,
            actionable=False,
            risk_level=risk.level.value,
            reason_codes=sorted(set(reasons)),
            bounds=bounds,
            constrained_optimum=opt,
            objective_current=obj_cur,
            objective_best=obj_best,
            uplift=uplift,
            binding=list(binding or []),
            n_candidates=n_cand,
        )

    # -- eligibility (the "historical support restriction") ------------------
    reasons: list[str] = []
    if "HISTORICAL_SUPPORT" not in disabled:
        if ctx.n_obs is not None and ctx.n_obs < thresholds.eligibility_min_observations:
            reasons.append("INSUFFICIENT_HISTORY")
        if (
            ctx.n_distinct_prices is not None
            and ctx.n_distinct_prices < thresholds.eligibility_min_distinct_prices
        ) or (ctx.price_cv is not None and ctx.price_cv < thresholds.eligibility_min_price_cv):
            reasons.append("INSUFFICIENT_PRICE_VARIATION")
    if objective == "gross_profit" and cost is None:
        reasons.append("COST_UNAVAILABLE")

    preliminary_risk = risk_at(p0)
    if reasons:
        return keep([*reasons, "KEEP_CURRENT_OPTIMAL"], preliminary_risk)

    # -- risk gate before any price is proposed ------------------------------
    high_risk_action = str(policy.get("high_risk_action", "review_required")).lower()
    risk_disabled = "RISK_GATE" in disabled
    if not risk_disabled and preliminary_risk.level is RiskLevel.HIGH and high_risk_action == "keep_current":
        return keep(
            ["HIGH_RISK_KEEP_CURRENT", "LOW_CONFIDENCE", "KEEP_CURRENT_OPTIMAL"], preliminary_risk
        )

    effective_max_change = float(policy.get("max_price_change_pct", 0.10))
    if not risk_disabled and preliminary_risk.level is RiskLevel.MEDIUM:
        cap = policy.get("medium_risk_max_price_change_pct")
        if cap is not None:
            effective_max_change = min(effective_max_change, float(cap))
        reasons.append("MEDIUM_RISK_CONSERVATIVE")
    if ctx.elasticity_source == "pooled_fallback":
        reasons.append("POOLED_ELASTICITY_FALLBACK")

    # -- feasible interval ----------------------------------------------------
    intervals = constraint_intervals(ctx, policy, effective_max_change=effective_max_change)
    active = {k: v for k, v in intervals.items() if k not in disabled}
    low = max(v[0] for v in active.values()) if active else 0.01
    high = min(v[1] for v in active.values()) if active else np.inf
    if not np.isfinite(high):
        high = p0 * RESEARCH_HIGH_MULTIPLE
    low = max(low, 0.01)

    binding_names = _binding_at(active, low, high)
    reason_map = {
        "MAX_PRICE_CHANGE": "PRICE_CHANGE_LIMIT",
        "EXTRAPOLATION": "OUTSIDE_EXTRAPOLATION_RANGE",
        "MIN_MARGIN": "MARGIN_CONSTRAINT",
        "COST_FLOOR": "MARGIN_CONSTRAINT",
        "MIN_PRICE": "ABSOLUTE_PRICE_FLOOR",
        "MAX_PRICE": "ABSOLUTE_PRICE_CEILING",
    }
    binding_reasons = [reason_map[n] for n in binding_names if n in reason_map]

    if high < low:
        return keep(
            [*reasons, "NO_FEASIBLE_PRICE", "KEEP_CURRENT_OPTIMAL"],
            preliminary_risk,
            bounds=(low, high),
        )

    grid = build_price_grid(
        p0,
        low=low,
        high=high,
        step=thresholds.price_step,
        rounding=thresholds.price_rounding,
        charm=thresholds.charm,
    )
    candidates = grid.prices[(grid.prices >= low - 1e-9) & (grid.prices <= high + 1e-9)]
    if candidates.size == 0:
        return keep(
            [*reasons, "NO_FEASIBLE_PRICE", "KEEP_CURRENT_OPTIMAL"],
            preliminary_risk,
            bounds=(low, high),
        )

    # Binding-constraint reason codes are only published once a feasible grid
    # exists, exactly as ``optimize_price`` does it.
    reasons.extend(binding_reasons)

    units = ctx.demand(candidates)
    values = _objective(candidates, units, cost, objective)
    best_idx = int(np.argmax(values))
    best_price = float(candidates[best_idx])
    cur_idx = int(np.argmin(np.abs(candidates - p0)))
    value_current = float(values[cur_idx])
    value_best = float(values[best_idx])
    uplift = (value_best - value_current) / abs(value_current) if value_current else np.nan
    price_change_pct = best_price / p0 - 1.0

    final_risk = risk_at(best_price)
    materiality = 0.0 if "MATERIALITY" in disabled else float(policy.get("materiality_threshold_pct", 0.01))

    if abs(price_change_pct) < 1e-9:
        return keep(
            [*reasons, "KEEP_CURRENT_OPTIMAL"], final_risk, bounds=(low, high), opt=best_price,
            obj_cur=value_current, obj_best=value_best, uplift=uplift,
            binding=binding_names, n_cand=int(candidates.size),
        )
    if not np.isfinite(uplift) or uplift < materiality:
        return keep(
            [*reasons, "NON_MATERIAL_UPLIFT", "KEEP_CURRENT_OPTIMAL"], final_risk,
            bounds=(low, high), opt=best_price, obj_cur=value_current, obj_best=value_best,
            uplift=uplift, binding=binding_names, n_cand=int(candidates.size),
        )

    reasons.append("PROFIT_UPLIFT_POSITIVE" if objective == "gross_profit" else "REVENUE_UPLIFT_POSITIVE")

    decision = "RECOMMEND_CHANGE"
    if not risk_disabled and final_risk.level is RiskLevel.HIGH and high_risk_action != "recommend":
        if high_risk_action == "keep_current":
            return keep(
                [*reasons, "HIGH_RISK_KEEP_CURRENT", "LOW_CONFIDENCE", "KEEP_CURRENT_OPTIMAL"],
                final_risk, bounds=(low, high), opt=best_price, obj_cur=value_current,
                obj_best=value_best, uplift=uplift, binding=binding_names,
                n_cand=int(candidates.size),
            )
        decision = "REVIEW_REQUIRED"
        reasons.extend(["HIGH_RISK_REVIEW_REQUIRED", "LOW_CONFIDENCE"])

    actionable = decision == "RECOMMEND_CHANGE"
    return Evaluation(
        decision=decision,
        proposed_candidate_price=best_price,
        final_recommended_price=best_price if actionable else p0,
        actionable=actionable,
        risk_level=final_risk.level.value,
        reason_codes=sorted(set(reasons)),
        bounds=(low, high),
        constrained_optimum=best_price,
        objective_current=value_current,
        objective_best=value_best,
        uplift=uplift,
        binding=binding_names,
        n_candidates=int(candidates.size),
    )


def _binding_at(intervals: dict[str, tuple[float, float]], low: float, high: float) -> list[str]:
    """Constraints whose own bound is the active edge of the feasible interval."""
    out: list[str] = []
    for name, (lo, hi) in intervals.items():
        if np.isfinite(lo) and abs(lo - low) <= _TOL * max(1.0, abs(low)):
            out.append(name)
        elif np.isfinite(hi) and abs(hi - high) <= _TOL * max(1.0, abs(high)):
            out.append(name)
    return out


# ---------------------------------------------------------------------------
# full attribution
# ---------------------------------------------------------------------------
def research_grid(ctx: PricingContext, thresholds: Thresholds) -> np.ndarray:
    lo = max(0.01, ctx.current_price * RESEARCH_LOW_MULTIPLE)
    hi = ctx.current_price * RESEARCH_HIGH_MULTIPLE
    grid = build_price_grid(
        ctx.current_price, low=lo, high=hi, step=thresholds.price_step,
        rounding=thresholds.price_rounding, charm=thresholds.charm,
    )
    return grid.prices


def attribute(
    ctx: PricingContext,
    policy: dict[str, Any],
    thresholds: Thresholds,
) -> dict[str, Any]:
    """Full constraint attribution for one decision context."""
    baseline = evaluate(ctx, policy, thresholds)

    # -- unconstrained reference on the wide research grid -------------------
    # No filtering: below cost the gross-profit objective is negative, so the
    # arg-max excludes those prices on its own, and every guardrail keeps an
    # honest "candidates removed" count against the same full grid.
    grid = research_grid(ctx, thresholds)
    unconstrained = float("nan")
    if grid.size:
        values = _objective(grid, ctx.demand(grid), ctx.unit_cost, thresholds.objective)
        unconstrained = float(grid[int(np.argmax(values))])

    # -- per-constraint diagnostics -------------------------------------------
    intervals = constraint_intervals(ctx, policy)
    per_constraint: dict[str, dict[str, Any]] = {}
    for name in CONSTRAINTS:
        present = False
        removed = 0
        if name in INTERVAL_CONSTRAINTS:
            lo, hi = intervals[name]
            present = bool(np.isfinite(lo) or np.isfinite(hi))
            if grid.size:
                removed = int(np.sum((grid < lo - 1e-9) | (grid > hi + 1e-9)))
        elif name == "ROUNDING":
            present = True
            removed = 0
        elif name == "MATERIALITY":
            present = float(policy.get("materiality_threshold_pct", 0.0)) > 0
        elif name == "RISK_GATE":
            present = True
        elif name == "HISTORICAL_SUPPORT":
            present = True

        binding = name in baseline.binding
        if name == "MATERIALITY":
            binding = "NON_MATERIAL_UPLIFT" in baseline.reason_codes
        elif name == "RISK_GATE":
            binding = any(
                r in baseline.reason_codes
                for r in ("HIGH_RISK_REVIEW_REQUIRED", "HIGH_RISK_KEEP_CURRENT", "MEDIUM_RISK_CONSERVATIVE")
            )
        elif name == "HISTORICAL_SUPPORT":
            binding = any(
                r in baseline.reason_codes
                for r in ("INSUFFICIENT_HISTORY", "INSUFFICIENT_PRICE_VARIATION")
            )
        elif name == "ROUNDING":
            binding = False

        # leave-one-out: does relaxing ONLY this constraint change the outcome?
        loo = evaluate(ctx, policy, thresholds, disabled=frozenset({name}))
        changed_decision = loo.decision != baseline.decision
        changed_price = abs(loo.final_recommended_price - baseline.final_recommended_price) > 1e-9

        per_constraint[name] = {
            "present": present,
            "removed_candidates": removed,
            "binding_at_optimum": bool(binding),
            "changed_decision_state": bool(changed_decision),
            "changed_final_price": bool(changed_price),
            "changed_anything": bool(changed_decision or changed_price),
        }

    # rounding is special: compare the grid optimum with the continuous optimum
    # inside the same feasible interval.
    lo_b, hi_b = baseline.bounds
    rounding_changed = False
    rounding_distance = float("nan")
    if np.isfinite(lo_b) and np.isfinite(hi_b) and hi_b >= lo_b and ctx.epsilon is not None:
        fine = np.arange(lo_b, hi_b + 1e-9, 0.01)
        if fine.size:
            fv = _objective(fine, ctx.demand(fine), ctx.unit_cost, thresholds.objective)
            fine_opt = float(fine[int(np.argmax(fv))])
            rounding_distance = abs(fine_opt - baseline.constrained_optimum)
            rounding_changed = rounding_distance > 0.011
    per_constraint["ROUNDING"]["binding_at_optimum"] = bool(rounding_changed)
    per_constraint["ROUNDING"]["changed_final_price"] = bool(rounding_changed)

    first_binding = _first_binding(baseline, unconstrained, intervals)

    # Three progressively stricter versions of "the learned signal decided":
    #   1. the unconstrained optimum is inside the feasible interval at all;
    #   2. the optimizer's PROPOSAL lands on it (within one grid step);
    #   3. the FINAL price does - i.e. it also survived materiality and the
    #      risk gate and would actually reach the shelf.
    tol = thresholds.price_step + 1e-9
    inside = (
        bool(np.isfinite(unconstrained))
        and np.isfinite(lo_b)
        and np.isfinite(hi_b)
        and lo_b - 1e-9 <= unconstrained <= hi_b + 1e-9
    )
    proposal_matches = (
        bool(np.isfinite(unconstrained))
        and abs(unconstrained - baseline.proposed_candidate_price) <= tol
    )
    survives = (
        bool(np.isfinite(unconstrained))
        and abs(unconstrained - baseline.final_recommended_price) <= tol
    )

    return {
        "upc": ctx.upc,
        "store": ctx.store,
        "week": ctx.week,
        "current_price": ctx.current_price,
        "unit_cost": ctx.unit_cost,
        "epsilon": ctx.epsilon,
        "elasticity_source": ctx.elasticity_source,
        "risk_level": baseline.risk_level,
        "decision": baseline.decision,
        "actionable": baseline.actionable,
        "unconstrained_optimum": unconstrained,
        "analytic_unconstrained_optimum": analytic_unconstrained_optimum(ctx.unit_cost, ctx.epsilon),
        "constrained_optimum": baseline.constrained_optimum,
        "proposed_candidate_price": baseline.proposed_candidate_price,
        "final_recommended_price": baseline.final_recommended_price,
        "price_change_pct": baseline.final_recommended_price / ctx.current_price - 1.0,
        "bounds_low": baseline.bounds[0],
        "bounds_high": baseline.bounds[1],
        "first_binding_constraint": first_binding,
        "all_binding_constraints": ",".join(baseline.binding),
        "unconstrained_optimum_inside_bounds": inside,
        "proposal_matches_unconstrained_optimum": proposal_matches,
        "model_optimum_survives_policy": survives,
        "rounding_distance": rounding_distance,
        "reason_codes": ",".join(baseline.reason_codes),
        "constraints": per_constraint,
    }


def _first_binding(
    ev: Evaluation, unconstrained: float, intervals: dict[str, tuple[float, float]]
) -> str:
    """The constraint that stops the walk from the unconstrained optimum first.

    Walking from the current price toward the unconstrained optimum, the first
    guardrail encountered is the tightest bound on that side.
    """
    if not np.isfinite(unconstrained):
        return ""
    if "INSUFFICIENT_HISTORY" in ev.reason_codes or "INSUFFICIENT_PRICE_VARIATION" in ev.reason_codes:
        return "HISTORICAL_SUPPORT"
    if "HIGH_RISK_KEEP_CURRENT" in ev.reason_codes:
        return "RISK_GATE"
    lo_b, hi_b = ev.bounds
    if not np.isfinite(lo_b) or not np.isfinite(hi_b):
        return ""
    if unconstrained > hi_b + 1e-9:
        winners = [n for n, (_, hi) in intervals.items() if np.isfinite(hi) and abs(hi - hi_b) < 1e-6]
        return winners[0] if winners else ""
    if unconstrained < lo_b - 1e-9:
        winners = [n for n, (lo, _) in intervals.items() if np.isfinite(lo) and abs(lo - lo_b) < 1e-6]
        return winners[0] if winners else ""
    if "NON_MATERIAL_UPLIFT" in ev.reason_codes:
        return "MATERIALITY"
    if "HIGH_RISK_REVIEW_REQUIRED" in ev.reason_codes:
        return "RISK_GATE"
    return "NONE"


# ---------------------------------------------------------------------------
# building contexts from the real decision pool
# ---------------------------------------------------------------------------
def contexts_from_frame(model, frame, *, promotion_share=None) -> list[PricingContext]:
    """Build replica contexts from a decision-pool frame in one batched call.

    ``frame`` must be a merge of the feature table with the per-series price
    statistics (``n_obs``, ``n_distinct_prices``, ``price_cv``, ``price_min``,
    ``price_max``), i.e. exactly what ``scripts/optimize.py`` feeds the
    optimizer.
    """
    import pandas as pd

    from pricing_engine.features.build import recompute_price_features
    from pricing_engine.models.hybrid import REFERENCE_PRICE_COLUMN, attach_reference_price

    d = frame.reset_index(drop=True)
    prices = d["effective_unit_price"].to_numpy(dtype="float64")
    scored = d if REFERENCE_PRICE_COLUMN in d.columns else attach_reference_price(d, prices)
    scored = recompute_price_features(scored, prices)

    base = getattr(model, "base_model", model)
    q0 = np.asarray(base.predict(scored), dtype="float64")

    if hasattr(model, "elasticity_for"):
        eps = np.asarray(model.elasticity_for(scored), dtype="float64")
        src = np.asarray(model.elasticity_source(scored))
    else:
        eps = np.full(len(d), np.nan)
        src = np.array(["ml_native"] * len(d))

    cost = d["decision_time_unit_cost"].to_numpy(dtype="float64")
    cap = getattr(base, "prediction_cap_", None)
    promo = (
        np.asarray(promotion_share, dtype="float64")
        if promotion_share is not None
        else np.full(len(d), np.nan)
    )

    def _opt(x):
        return None if x is None or not np.isfinite(x) else float(x)

    out: list[PricingContext] = []
    for i in range(len(d)):
        row = d.iloc[i]
        out.append(
            PricingContext(
                upc=int(row["upc"]),
                store=int(row["store"]),
                week=int(row["week"]),
                current_price=float(prices[i]),
                unit_cost=_opt(cost[i]) if cost[i] > 0 else None,
                baseline_units=float(q0[i]),
                epsilon=_opt(eps[i]),
                elasticity_source=str(src[i]),
                n_obs=int(row["n_obs"]) if pd.notna(row.get("n_obs")) else None,
                n_distinct_prices=int(row["n_distinct_prices"])
                if pd.notna(row.get("n_distinct_prices"))
                else None,
                price_cv=_opt(row.get("price_cv")),
                hist_price_min=_opt(row.get("price_min")),
                hist_price_max=_opt(row.get("price_max")),
                promotion_share=_opt(promo[i]),
                prediction_cap=cap,
            )
        )
    return out


# ---------------------------------------------------------------------------
# rule-only policies (Phase M / Task 2)
# ---------------------------------------------------------------------------
def evaluate_rule(
    ctx: PricingContext,
    target_price: float,
    policy: dict[str, Any],
    thresholds: Thresholds,
) -> Evaluation:
    """Run a rule-chosen price through the *same* policy layer as the model.

    The rule supplies ``target_price`` without consulting any estimated demand
    response. Everything after that - eligibility screen, risk gate, feasible
    interval, rounding, materiality, decision state - is identical to
    :func:`evaluate`, so a comparison isolates the pricing signal rather than
    the guardrails.

    The one thing that cannot be identical is the materiality test: the model
    version compares estimated profit, and a rule has no estimated profit. The
    rule version therefore treats a price move smaller than the materiality
    threshold as immaterial, which is the closest rule-only analogue.
    """
    policy = dict(policy)
    p0 = ctx.current_price

    def risk_at(price: float):
        return assess_risk(
            n_obs=ctx.n_obs,
            n_distinct_prices=ctx.n_distinct_prices,
            price_cv=ctx.price_cv,
            recommended_price=price,
            hist_price_min=ctx.hist_price_min,
            hist_price_max=ctx.hist_price_max,
            price_change_pct=price / p0 - 1.0,
            cfg_risk=thresholds.risk_bands,
            promotion_share=ctx.promotion_share,
            elasticity_source=ctx.elasticity_source,
        )

    def keep(reasons, risk, bounds=(np.nan, np.nan), binding=None) -> Evaluation:
        return Evaluation(
            decision="KEEP_CURRENT",
            proposed_candidate_price=p0,
            final_recommended_price=p0,
            actionable=False,
            risk_level=risk.level.value,
            reason_codes=sorted(set(reasons)),
            bounds=bounds,
            constrained_optimum=np.nan,
            objective_current=np.nan,
            objective_best=np.nan,
            uplift=np.nan,
            binding=list(binding or []),
        )

    reasons: list[str] = []
    if ctx.n_obs is not None and ctx.n_obs < thresholds.eligibility_min_observations:
        reasons.append("INSUFFICIENT_HISTORY")
    if (
        ctx.n_distinct_prices is not None
        and ctx.n_distinct_prices < thresholds.eligibility_min_distinct_prices
    ) or (ctx.price_cv is not None and ctx.price_cv < thresholds.eligibility_min_price_cv):
        reasons.append("INSUFFICIENT_PRICE_VARIATION")
    if thresholds.objective == "gross_profit" and ctx.unit_cost is None:
        reasons.append("COST_UNAVAILABLE")

    preliminary_risk = risk_at(p0)
    if reasons:
        return keep([*reasons, "KEEP_CURRENT_OPTIMAL"], preliminary_risk)

    high_risk_action = str(policy.get("high_risk_action", "review_required")).lower()
    if preliminary_risk.level is RiskLevel.HIGH and high_risk_action == "keep_current":
        return keep(["HIGH_RISK_KEEP_CURRENT", "LOW_CONFIDENCE", "KEEP_CURRENT_OPTIMAL"], preliminary_risk)

    effective_max_change = float(policy.get("max_price_change_pct", 0.10))
    if preliminary_risk.level is RiskLevel.MEDIUM:
        cap = policy.get("medium_risk_max_price_change_pct")
        if cap is not None:
            effective_max_change = min(effective_max_change, float(cap))
        reasons.append("MEDIUM_RISK_CONSERVATIVE")

    intervals = constraint_intervals(ctx, policy, effective_max_change=effective_max_change)
    low = max(v[0] for v in intervals.values())
    high = min(v[1] for v in intervals.values())
    if not np.isfinite(high):
        high = p0 * RESEARCH_HIGH_MULTIPLE
    low = max(low, 0.01)
    if high < low:
        return keep([*reasons, "NO_FEASIBLE_PRICE", "KEEP_CURRENT_OPTIMAL"], preliminary_risk, (low, high))

    binding_names = _binding_at(intervals, low, high)
    price = float(np.clip(target_price, low, high))
    rounding = thresholds.price_rounding
    if rounding > 0:
        price = float(np.round(price / rounding) * rounding)
        price = float(np.clip(price, low, high))
    if abs(target_price - price) > 1e-9:
        reasons.append("CLIPPED_TO_GUARDRAILS")

    final_risk = risk_at(price)
    change = price / p0 - 1.0
    materiality = float(policy.get("materiality_threshold_pct", 0.01))
    if abs(change) < max(materiality, 1e-9):
        return keep([*reasons, "NON_MATERIAL_UPLIFT", "KEEP_CURRENT_OPTIMAL"], final_risk, (low, high), binding_names)

    decision = "RECOMMEND_CHANGE"
    if final_risk.level is RiskLevel.HIGH and high_risk_action != "recommend":
        if high_risk_action == "keep_current":
            return keep(
                [*reasons, "HIGH_RISK_KEEP_CURRENT", "LOW_CONFIDENCE", "KEEP_CURRENT_OPTIMAL"],
                final_risk, (low, high), binding_names,
            )
        decision = "REVIEW_REQUIRED"
        reasons.extend(["HIGH_RISK_REVIEW_REQUIRED", "LOW_CONFIDENCE"])

    actionable = decision == "RECOMMEND_CHANGE"
    return Evaluation(
        decision=decision,
        proposed_candidate_price=price,
        final_recommended_price=price if actionable else p0,
        actionable=actionable,
        risk_level=final_risk.level.value,
        reason_codes=sorted(set(reasons)),
        bounds=(low, high),
        constrained_optimum=price,
        objective_current=np.nan,
        objective_best=np.nan,
        uplift=np.nan,
        binding=binding_names,
    )
