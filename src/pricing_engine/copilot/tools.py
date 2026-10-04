"""The engine operations the copilot may call.

Every tool returns a small, JSON-ready dict whose numbers are already rounded
and already in the unit a person would quote (dollars to the cent, percent to
one decimal), so the language model never has to do arithmetic: the guard
rejects any number it did not receive from a tool.

Each tool wraps :class:`pricing_engine.serving.AppState`, the same object the
API serves, so the copilot and the API give identical answers.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from pricing_engine.copilot.glossary import DECISION_GLOSSARY, REASON_CODE_GLOSSARY
from pricing_engine.optimization.optimizer import OptimizerError, optimize_price_batch
from pricing_engine.serving import STAT_COLUMNS, AppState, ContextNotFound, ServingError

POLICY_PROFILES = ("conservative", "standard", "aggressive")
MAX_SIM_POINTS = 25


def money(x: Any) -> float | None:
    return None if x is None or not np.isfinite(float(x)) else round(float(x), 2)


def pct(fraction: Any) -> float | None:
    """A fraction (0.0838) as a percent to one decimal (8.4)."""
    return None if fraction is None or not np.isfinite(float(fraction)) else round(100 * float(fraction), 1)


def num(x: Any, digits: int = 1) -> float | None:
    return None if x is None or not np.isfinite(float(x)) else round(float(x), digits)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., dict[str, Any]]

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}


# ---------------------------------------------------------------------------
# tool implementations
# ---------------------------------------------------------------------------
def list_products(state: AppState, query: str | None = None, limit: int = 20) -> dict[str, Any]:
    prods = state.products()
    if query and "descrip" in prods:
        prods = prods[prods["descrip"].astype(str).str.contains(query, case=False, regex=False)]
    limit = max(1, min(int(limit), 50))
    rows = [
        {"upc": int(r.upc), "store": int(r.store), "description": str(getattr(r, "descrip", "")),
         "latest_week": int(r.week)}
        for r in prods.head(limit).itertuples()
    ]
    return {"matches": int(len(prods)), "shown": len(rows), "products": rows}


def get_model_info(state: AppState) -> dict[str, Any]:
    table = getattr(state.model, "elasticity_table", None)
    return {
        "data": state.data_label,
        "data_mode": state.data_mode,
        "price_response_method": getattr(getattr(state.model, "method", None), "value", None),
        "category_pooled_elasticity": num(table.pooled, 2) if table is not None else None,
        "model_version": state.model_version,
        "served_weeks": [state.weeks[0], state.weeks[-1]],
    }


def _recommendation(state: AppState, upc: int, store: int, policy_profile: str,
                    objective: str) -> dict[str, Any]:
    rec = state.recommend(int(upc), int(store), policy_profile=policy_profile,
                          objective=objective).as_dict()
    bounds = (rec.get("constraints") or {}).get("bounds")
    return {
        "upc": rec["upc"],
        "store": rec["store"],
        "product": rec.get("product_description"),
        "week": rec["decision_week"],
        "policy_profile": rec["policy_profile"],
        "objective": rec["objective"],
        "decision": rec["decision"],
        "decision_meaning": DECISION_GLOSSARY.get(rec["decision"], ""),
        "current_price": money(rec["current_price"]),
        "recommended_price": money(rec["final_recommended_price"]),
        "price_change_percent": pct(rec["price_change_pct"]),
        "proposed_candidate_price": money(rec["proposed_candidate_price"]),
        "proposed_change_percent": pct(rec["proposed_price_change_pct"]),
        "allowed_price_range": [money(bounds[0]), money(bounds[1])] if bounds else None,
        "unit_cost": money(rec["unit_cost_used"]),
        "risk_level": rec["risk_level"],
        "reason_codes": list(rec["reason_codes"]),
        "elasticity_used": num(rec["elasticity_used"], 2) if rec["elasticity_used"] is not None else None,
        "elasticity_source": rec["elasticity_source"],
        "predicted_units_current": num(rec["predicted_units_current"]),
        "predicted_units_recommended": num(rec["predicted_units_recommended"]),
        "expected_gross_profit_current": money(rec["expected_gross_profit_current"]),
        "expected_gross_profit_recommended": money(rec["expected_gross_profit_recommended"]),
        "model_internal_estimated_profit_uplift_percent": pct(
            rec["model_internal_estimated_profit_uplift_pct"]
        ),
    }


def recommend_price(state: AppState, upc: int, store: int, policy_profile: str = "standard",
                    objective: str = "gross_profit") -> dict[str, Any]:
    return _recommendation(state, upc, store, policy_profile, objective)


def simulate_prices(state: AppState, upc: int, store: int, min_price: float, max_price: float,
                    step: float | None = None) -> dict[str, Any]:
    min_price, max_price = float(min_price), float(max_price)
    if not 0 < min_price < max_price:
        raise ServingError("min_price must be positive and below max_price")
    step = float(step) if step else max(0.05, round((max_price - min_price) / 10, 2))
    if (max_price - min_price) / step + 1 > MAX_SIM_POINTS:
        step = round((max_price - min_price) / (MAX_SIM_POINTS - 1), 2) + 0.01
    sim = state.simulate_prices(int(upc), int(store), min_price, max_price, step)
    curve = [
        {"price": money(p["candidate_price"]), "units": num(p["predicted_units"]),
         "revenue": money(p["expected_revenue"]), "gross_profit": money(p["expected_gross_profit"])}
        for p in sim["curve"]
    ]
    best = max((c for c in curve if c["gross_profit"] is not None),
               key=lambda c: c["gross_profit"], default=None)
    return {
        "upc": sim["upc"], "store": sim["store"], "week": sim["week"],
        "unit_cost": money(sim["unit_cost_used"]),
        "elasticity_used": num(sim["elasticity_used"], 2) if sim["elasticity_used"] is not None else None,
        "curve": curve,
        "highest_gross_profit_on_this_grid": best,
        "note": "Model-internal estimates. The grid ignores the business guardrails; "
                "use recommend_price for a price the policy allows.",
    }


def compare_policies(state: AppState, upc: int, store: int) -> dict[str, Any]:
    out = []
    for profile in POLICY_PROFILES:
        r = _recommendation(state, upc, store, profile, "gross_profit")
        out.append({k: r[k] for k in (
            "policy_profile", "decision", "recommended_price", "price_change_percent", "risk_level",
            "model_internal_estimated_profit_uplift_percent", "reason_codes")})
    return {"upc": int(upc), "store": int(store), "current_price": r["current_price"], "policies": out}


def explain_reason_codes(state: AppState, codes: list[str]) -> dict[str, Any]:
    known = {**REASON_CODE_GLOSSARY, **DECISION_GLOSSARY}
    return {"meanings": {c: known.get(str(c), "Unknown code.") for c in codes}}


_PORTFOLIO_CACHE: dict[tuple[int, str, int], dict[str, Any]] = {}


def portfolio_summary(state: AppState, policy_profile: str = "standard",
                      max_contexts: int = 500) -> dict[str, Any]:
    max_contexts = max(10, min(int(max_contexts), 2000))
    key = (id(state), policy_profile, max_contexts)
    if key in _PORTFOLIO_CACHE:
        return _PORTFOLIO_CACHE[key]
    latest = state.contexts[state.contexts["week"] == state.weeks[-1]]
    take = latest.sample(min(max_contexts, len(latest)), random_state=state.cfg.seed)
    take = take.merge(state.stats[["upc", "store", *STAT_COLUMNS]], on=["upc", "store"], how="left")
    recs = optimize_price_batch(state.model, take, cfg=state.cfg, policy_profile=policy_profile,
                                series_stats=take[STAT_COLUMNS].to_dict(orient="records"))
    df = pd.DataFrame([r.as_dict() for r in recs])
    act = df[df["actionable"]]
    gp_now = float(df["expected_gross_profit_current"].sum(skipna=True))
    gp_new = float(np.nansum(np.where(df["actionable"], df["expected_gross_profit_recommended"],
                                      df["expected_gross_profit_current"])))
    codes = pd.Series([c for cs in df["reason_codes"] for c in cs]).value_counts().head(5)
    result = {
        "week": state.weeks[-1],
        "policy_profile": policy_profile,
        "contexts_scored": int(len(df)),
        "decisions": {d: {"count": int(n), "percent": pct(n / len(df))}
                      for d, n in df["decision"].value_counts().items()},
        "risk_levels": {k: int(v) for k, v in df["risk_level"].value_counts().items()},
        "median_price_change_percent_among_actionable": pct(act["price_change_pct"].median())
        if len(act) else None,
        "percent_of_changes_that_are_increases": pct((act["price_change_pct"] > 0).mean())
        if len(act) else None,
        "model_internal_estimated_portfolio_uplift_percent": pct(gp_new / gp_now - 1) if gp_now else None,
        "top_reason_codes": {k: int(v) for k, v in codes.items()},
    }
    _PORTFOLIO_CACHE[key] = result
    return result


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------
_ID = {"upc": {"type": "integer", "description": "Product code (UPC)"},
       "store": {"type": "integer", "description": "Store number"}}
_PROFILE = {"type": "string", "enum": list(POLICY_PROFILES),
            "description": "Guardrail profile; standard is the default policy"}

TOOLS: dict[str, Tool] = {t.name: t for t in (
    Tool("list_products", "Find served products by (part of) their name; returns UPC, store and "
         "description. Use it to turn a product name into a UPC and store.",
         {"type": "object", "properties": {"query": {"type": "string"},
                                           "limit": {"type": "integer"}}}, list_products),
    Tool("get_model_info", "Which data, model version and price-response method are being served.",
         {"type": "object", "properties": {}}, get_model_info),
    Tool("recommend_price", "The engine's price recommendation for one product in one store, with "
         "decision state, risk, reason codes and model-internal estimated economics.",
         {"type": "object", "properties": {**_ID, "policy_profile": _PROFILE,
                                           "objective": {"type": "string",
                                                         "enum": ["gross_profit", "revenue"]}},
          "required": ["upc", "store"]}, recommend_price),
    Tool("simulate_prices", "Estimated units, revenue and gross profit at a range of candidate "
         "prices for one product in one store (at most 25 points; ignores guardrails).",
         {"type": "object", "properties": {**_ID, "min_price": {"type": "number"},
                                           "max_price": {"type": "number"},
                                           "step": {"type": "number"}},
          "required": ["upc", "store", "min_price", "max_price"]}, simulate_prices),
    Tool("compare_policies", "The recommendation under the conservative, standard and aggressive "
         "guardrail profiles side by side.",
         {"type": "object", "properties": _ID, "required": ["upc", "store"]}, compare_policies),
    Tool("explain_reason_codes", "Plain-English meaning of reason codes or decision states.",
         {"type": "object", "properties": {"codes": {"type": "array", "items": {"type": "string"}}},
          "required": ["codes"]}, explain_reason_codes),
    Tool("portfolio_summary", "Decision mix, risk mix and model-internal estimated uplift across a "
         "sample of all products and stores in the latest week.",
         {"type": "object", "properties": {"policy_profile": _PROFILE,
                                           "max_contexts": {"type": "integer"}}}, portfolio_summary),
)}


def run_tool(state: AppState, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute a tool; domain errors come back as data the model can explain."""
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": f"Unknown tool {name!r}. Available: {sorted(TOOLS)}"}
    args = {k: v for k, v in (arguments or {}).items() if not k.startswith("_")}
    try:
        return tool.fn(state, **args)
    except (ContextNotFound, ServingError, OptimizerError, ValueError, KeyError, TypeError) as exc:
        return {"error": str(exc)}
