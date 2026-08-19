# 19. Decision-state consistency audit

The dangerous failure mode of a pricing engine is not a wrong number - it is a
proposal that gets read as a decision. This report enumerates every combination
of risk level, decision state, actionable flag and price that occurs in the real
decision pool, and checks the invariants on all of them.

Pool: all 13,964 `UPC x store` contexts of week 399.

## 1. The three prices, kept apart

| Field | Meaning |
| --- | --- |
| `current_price` | what is charged today |
| `proposed_candidate_price` | what the optimizer wanted to do. **Not a business price.** |
| `final_recommended_price` | what would actually be charged: the proposal only when `actionable`, otherwise `current_price` |

Phase M renamed these. Before, a single `recommended_price` field held the
proposal even for REVIEW_REQUIRED rows, so any consumer that did not also read
`actionable` would show a price that the policy had refused to authorise. The
API, the dashboard, the audit log and every report now carry both fields, and
`price_change_pct` refers to the **final** price (it is 0 whenever the decision
is not actionable).

## 2. Invariants

| Invariant | standard | conservative | aggressive |
| --- | --- | --- | --- |
| HIGH risk is never actionable | PASS | PASS | 4,717 (DEMO exception) |
| actionable iff RECOMMEND_CHANGE | PASS | PASS | PASS |
| REVIEW_REQUIRED keeps the current price | PASS | PASS | PASS |
| KEEP_CURRENT keeps the current price | PASS | PASS | PASS |
| KEEP_CURRENT proposes nothing | PASS | PASS | PASS |
| RECOMMEND_CHANGE actually changes the price | PASS | PASS | PASS |

**Genuine violations across all three profiles: 0.** The one
non-`PASS` cell is the `aggressive` profile acting on HIGH risk, which is what
that profile is configured to do (`high_risk_action: recommend`), is labelled
DEMO ONLY in `configs/config.yaml`, and is never a default. It is shown here
rather than hidden so the exception stays visible.

Property-style tests over 245 parameter combinations pin the same invariants in
`tests/test_decision_states.py`.

## 3. Every observed combination

| Risk | Decision | Actionable | Proposal moves? | Final moves? | Contexts | Share |
| --- | --- | --- | --- | --- | ---: | ---: |
| LOW | RECOMMEND_CHANGE | True | yes | yes | 4,971 | 35.6% |
| HIGH | KEEP_CURRENT | False | no | no | 3,407 | 24.4% |
| MEDIUM | RECOMMEND_CHANGE | True | yes | yes | 3,294 | 23.6% |
| HIGH | REVIEW_REQUIRED | False | yes | no | 1,353 | 9.7% |
| LOW | KEEP_CURRENT | False | no | no | 540 | 3.9% |
| MEDIUM | KEEP_CURRENT | False | no | no | 399 | 2.9% |

The row that matters is `HIGH / REVIEW_REQUIRED / False / yes / no`: a proposal
exists, and the final price does not move. That is the human-in-the-loop path
working.

## 4. Why HIGH-risk contexts split between REVIEW_REQUIRED and KEEP_CURRENT

Under the `standard` profile there are **4,760** HIGH-risk contexts, and
they do not all become REVIEW_REQUIRED:

| Decision | Contexts | Share of HIGH risk |
| --- | ---: | ---: |
| KEEP_CURRENT | 3,407 | 71.6% |
| REVIEW_REQUIRED | 1,353 | 28.4% |

REVIEW_REQUIRED means *"the optimizer produced a material, feasible proposal and
the risk gate refuses to auto-apply it"*. A HIGH-risk context ends at
KEEP_CURRENT instead whenever there was **never a proposal to gate** - the
context failed earlier in the pipeline, so the risk gate has nothing to
suppress:

| Why the HIGH-risk context is KEEP_CURRENT rather than REVIEW_REQUIRED | Contexts | Share of HIGH-risk KEEP_CURRENT |
| --- | ---: | ---: |
| screened out by the eligibility rules (never reaches the optimizer) | 3,349 | 98.3% |
| no feasible price inside the guardrails | 41 | 1.2% |
| the optimum IS the current price | 3,391 | 99.5% |
| estimated uplift below the materiality threshold | 16 | 0.5% |

(The categories overlap: a context can be screened out *and* have an immaterial
uplift.) The ordering is what produces the split - eligibility screen, then risk
gate, then optimisation, then materiality, then the final risk gate. The risk
gate only ever sees contexts that survived everything before it.

Reason codes present on HIGH-risk contexts:

| Reason code | Contexts | Share of HIGH risk |
| --- | ---: | ---: |
| KEEP_CURRENT_OPTIMAL | 3,407 | 71.6% |
| INSUFFICIENT_PRICE_VARIATION | 3,053 | 64.1% |
| INSUFFICIENT_HISTORY | 2,680 | 56.3% |
| PRICE_CHANGE_LIMIT | 1,355 | 28.5% |
| HIGH_RISK_REVIEW_REQUIRED | 1,353 | 28.4% |
| LOW_CONFIDENCE | 1,353 | 28.4% |
| PROFIT_UPLIFT_POSITIVE | 1,353 | 28.4% |
| POOLED_ELASTICITY_FALLBACK | 1,188 | 25.0% |
| OUTSIDE_EXTRAPOLATION_RANGE | 343 | 7.2% |
| MARGIN_CONSTRAINT | 64 | 1.3% |
| NO_FEASIBLE_PRICE | 41 | 0.9% |
| NON_MATERIAL_UPLIFT | 16 | 0.3% |

## 5. HIGH-risk actionable count

| Profile | HIGH-risk contexts | HIGH-risk actionable |
| --- | ---: | ---: |
| `standard` | 4,760 | 0 |
| `conservative` | 4,760 | 0 |
| `aggressive` | 8,102 | 4,717 |

Zero under both default profiles. The `aggressive` profile makes
4,717 of them actionable, which is precisely what it
is documented to do and why it is labelled DEMO ONLY in
`configs/config.yaml`.

## 6. Suppressed proposals

Under `standard`, **1,353**
contexts carry a proposal that the policy layer refuses to apply. Those are the
rows a human reviewer would work through. They must never be counted in a
portfolio profit figure, and they are not: `realisable_profit_uplift_pct` is
forced to 0 for every non-actionable recommendation, and
`scripts/optimize.py` sums the current-price economics for them.

---

*Generated by `scripts/audit_decision_states.py` in 6.4s.*
