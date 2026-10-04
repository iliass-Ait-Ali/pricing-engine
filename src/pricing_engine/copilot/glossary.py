"""Plain-English meaning of every reason code the optimizer can emit."""

from __future__ import annotations

from pricing_engine.optimization.optimizer import DecisionState, ReasonCode

REASON_CODE_GLOSSARY: dict[str, str] = {
    ReasonCode.KEEP_CURRENT_OPTIMAL: (
        "The engine leaves today's price in place: either nothing better was found inside "
        "the allowed range, or an earlier check stopped the optimisation."
    ),
    ReasonCode.LOW_CONFIDENCE: (
        "The evidence for this product and store is thin or risky, so the proposal is not "
        "applied automatically."
    ),
    ReasonCode.INSUFFICIENT_HISTORY: (
        "Too few weeks of sales history for this product in this store to estimate a price "
        "response."
    ),
    ReasonCode.INSUFFICIENT_PRICE_VARIATION: (
        "The price has barely changed in the past, so there is no evidence of how demand "
        "reacts to a different price."
    ),
    ReasonCode.OUTSIDE_EXTRAPOLATION_RANGE: (
        "The best candidate would sit outside the range of prices this product has been "
        "sold at, so the engine stops at the edge of observed prices."
    ),
    ReasonCode.COST_UNAVAILABLE: (
        "No usable unit cost is known at decision time, so profit cannot be computed."
    ),
    ReasonCode.MARGIN_CONSTRAINT: (
        "The minimum gross-margin rule limits how low the price can go."
    ),
    ReasonCode.PRICE_CHANGE_LIMIT: (
        "The maximum weekly price-change rule (for example +/-10% in the standard policy) "
        "limits the move. The model would have gone further."
    ),
    ReasonCode.PROFIT_UPLIFT_POSITIVE: (
        "At the recommended price the model-internal estimated gross profit is higher than "
        "at today's price."
    ),
    ReasonCode.REVENUE_UPLIFT_POSITIVE: (
        "At the recommended price the model-internal estimated revenue is higher than at "
        "today's price."
    ),
    ReasonCode.NO_FEASIBLE_PRICE: (
        "The business rules contradict each other for this product (for example the margin "
        "floor sits above the maximum allowed price), so no price satisfies them all."
    ),
    ReasonCode.NON_MATERIAL_UPLIFT: (
        "The estimated gain is below the materiality threshold, so changing the price is "
        "not worth the disruption."
    ),
    ReasonCode.DEMAND_CURVE_NOT_DECREASING: (
        "Warning: the estimated demand did not fall as price rose over the candidate range, "
        "which is economically implausible."
    ),
    ReasonCode.HIGH_RISK_REVIEW_REQUIRED: (
        "HIGH risk: a person must approve this proposal before it is applied."
    ),
    ReasonCode.HIGH_RISK_KEEP_CURRENT: (
        "HIGH risk under a cautious policy: today's price is kept."
    ),
    ReasonCode.MEDIUM_RISK_CONSERVATIVE: (
        "MEDIUM risk: the allowed price change is reduced (for example to 5%)."
    ),
    ReasonCode.POOLED_ELASTICITY_FALLBACK: (
        "This product has no reliable price sensitivity of its own, so the category-wide "
        "estimate is used instead. Such recommendations are never rated LOW risk."
    ),
}

DECISION_GLOSSARY: dict[str, str] = {
    DecisionState.RECOMMEND_CHANGE: "Change the price; it can be applied without further approval.",
    DecisionState.REVIEW_REQUIRED: "A proposal exists, but a person must approve it first.",
    DecisionState.KEEP_CURRENT: "Leave today's price unchanged.",
}
