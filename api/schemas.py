"""Pydantic request/response models for the pricing API.

Validation is deliberately strict: a pricing endpoint that silently accepts a
negative price or an impossible range is a liability.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    model_loaded: bool
    contexts_loaded: int
    decision_weeks: list[int] = Field(default_factory=list)


class ModelInfoResponse(BaseModel):
    name: str | None
    kind: str | None
    version: str | None
    price_response_method: str | None = None
    pooled_elasticity: float | None = None
    elasticity_training_weeks: list[int] = Field(default_factory=list)
    trained_at_utc: str | None
    target: str | None
    n_features: int
    feature_columns: list[str]
    train_weeks: list[int]
    valid_weeks: list[int]
    test_weeks: list[int]
    metrics: dict[str, Any]
    data_fingerprint: str | None
    disclaimer: str = (
        "Predictions are observational model estimates, not causal effects. "
        "Counterfactual economics at unobserved prices are MODEL-INTERNAL estimates: "
        "the same fitted price-response model both proposes and scores candidate "
        "prices, so they are an internal simulation, not an unbiased policy value."
    )


class PredictDemandRequest(BaseModel):
    upc: int = Field(..., description="Dominick's UPC code")
    store: int = Field(..., ge=1, description="Dominick's store number")
    week: int | None = Field(None, ge=1, description="Decision week (default: latest available)")
    price: float | None = Field(
        None, gt=0, description="Optional candidate unit price; defaults to the observed price"
    )


class PredictDemandResponse(BaseModel):
    upc: int
    store: int
    week: int
    week_start_date: str
    product_description: str | None
    price_used: float
    observed_price: float
    predicted_units: float
    model_version: str | None


class SimulatePricesRequest(BaseModel):
    upc: int
    store: int
    week: int | None = Field(None, ge=1)
    min_price: float = Field(..., gt=0)
    max_price: float = Field(..., gt=0)
    step: float = Field(0.05, gt=0, le=5.0)
    unit_cost: float | None = Field(None, gt=0, description="Override the decision-time unit cost")

    @model_validator(mode="after")
    def check_range(self) -> SimulatePricesRequest:
        if self.max_price < self.min_price:
            raise ValueError("max_price must be greater than or equal to min_price")
        if (self.max_price - self.min_price) / self.step > 500:
            raise ValueError("requested grid exceeds 500 candidate prices; widen step or narrow range")
        return self


class SimulationPoint(BaseModel):
    candidate_price: float
    predicted_units: float
    expected_revenue: float
    expected_gross_profit: float | None
    expected_margin_rate: float | None


class SimulatePricesResponse(BaseModel):
    upc: int
    store: int
    week: int
    unit_cost_used: float | None
    model_version: str | None
    price_response_method: str | None = None
    elasticity_used: float | None = None
    elasticity_source: str | None = None
    n_candidates: int
    curve: list[SimulationPoint]
    diagnostics: dict[str, Any]


class RecommendPriceRequest(BaseModel):
    upc: int
    store: int
    week: int | None = Field(None, ge=1)
    objective: Literal["gross_profit", "revenue"] = "gross_profit"
    policy_profile: Literal["conservative", "standard", "aggressive"] = "standard"
    unit_cost: float | None = Field(None, gt=0)


class RecommendPriceResponse(BaseModel):
    upc: int
    store: int
    decision_week: int
    decision_week_start_date: str
    product_description: str | None
    objective: str
    policy_profile: str
    model_version: str | None
    price_response_method: str | None
    elasticity_used: float | None
    elasticity_source: str | None
    current_price: float
    #: What the optimizer proposed. NOT a business price on its own.
    proposed_candidate_price: float
    #: What would actually be charged: the proposal only when ``actionable``,
    #: otherwise the current price.
    final_recommended_price: float
    proposed_price_change_pct: float
    #: Change of the FINAL price (0.0 whenever the decision is not actionable).
    price_change_pct: float
    decision: Literal["RECOMMEND_CHANGE", "KEEP_CURRENT", "REVIEW_REQUIRED"]
    actionable: bool
    predicted_units_current: float
    predicted_units_recommended: float
    expected_revenue_current: float
    expected_revenue_recommended: float
    expected_gross_profit_current: float | None
    expected_gross_profit_recommended: float | None
    model_internal_estimated_profit_uplift_pct: float | None
    model_internal_estimated_revenue_uplift_pct: float | None
    #: Uplift that the policy layer would actually let through (0.0 when the
    #: recommendation is not actionable).
    realisable_profit_uplift_pct: float | None
    unit_cost_used: float | None
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    reason_codes: list[str]
    risk_notes: list[str] = Field(default_factory=list)
    constraints: dict[str, Any]
    disclaimer: str = (
        "REVIEW_REQUIRED and KEEP_CURRENT are not actionable: for those states "
        "final_recommended_price equals current_price and only "
        "proposed_candidate_price carries the optimizer's proposal. Uplift "
        "figures are model-internal estimates, never realised or causal impact."
    )
