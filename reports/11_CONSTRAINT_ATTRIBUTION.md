# 11. Constraint-binding attribution

**Question.** When this engine recommends a price, is the price chosen by the
estimated demand response, or is it simply the nearest point the guardrails
allow?

**Method.** Every one of the 13,964 `UPC x store` decision contexts of week
399 (the last week of the test window) is run through the full pipeline
four times over: once with no guardrails on a wide research grid
(0.2x .. 5x the current price), once under the `standard` policy, and
once per constraint with that constraint - and only that constraint - relaxed
(leave-one-out). Price response: `shrunk`.

The optimizer used here is `pricing_engine.optimization.attribution.evaluate`,
a fast replica of `optimize_price`. `tests/test_attribution.py` asserts that it
reproduces the real optimizer's decision state, final price and reason codes
exactly on 60 parameter combinations; it was also checked against 400 real
week-399 contexts with zero mismatches.

## 1. Where the price comes from

| Quantity | Definition |
| --- | --- |
| `unconstrained_optimum` | arg-max of expected gross profit on the wide research grid, no business guardrails |
| `constrained_optimum` | arg-max inside the feasible interval the policy layer produces |
| `proposed_candidate_price` | the constrained optimum after the materiality check |
| `final_recommended_price` | what would actually be charged: the proposal only when the decision state is actionable |

Under the hybrid response `Q(p) = Q_hat(p0) (p/p0)^e`, the unconstrained
gross-profit optimum has a closed form:

```
p* = c * e / (1 + e)        for e < -1
p* -> unbounded             for -1 < e < 0   (inelastic: raising price always pays)
```

`Q_hat(p0)` is a positive constant across candidate prices, so it **cancels out
of the arg-max**. The demand *forecast* therefore sets the predicted volume and
the dollar amounts, but it does not choose the price: `(p0, c, e)` and the
guardrails do. See `reports/12_MODEL_VALUE_ABLATION.md`.

Median unconstrained optimum vs the current price:
**+49.0%**
(p10 -4.7%,
p90 +174.6%).
86.7% of contexts have
an unconstrained optimum **above** the current price.

Median FINAL price change actually recommended:
**+3.7%**.

Three progressively stricter readings of "the learned price signal decided this
recommendation":

| Reading | Contexts | Share |
| --- | ---: | ---: |
| The unconstrained optimum is inside the feasible interval at all | 968 | 6.9% |
| The optimizer's **proposal** lands on it (within one 5c grid step) | 638 | 4.6% |
| The **final** price lands on it (it also survived materiality and the risk gate) | 635 | 4.5% |

The gap between the first two rows is price rounding: the 5-cent candidate grid
is anchored on the lower bound, so the grid optimum sits a median of
$0.025 (p90 $0.043) away from the
continuous optimum inside the same interval. The gap between the second and
third rows is the non-actionable states: a REVIEW_REQUIRED or KEEP_CURRENT
context ships the current price, whatever the optimizer proposed.

## 2. Constraint attribution

Columns are counts of contexts (out of 13,964):

* **Present** - configured and applicable to this context.
* **Removes candidates** - excludes at least one price from the wide research
  grid.
* **Binding at optimum** - its own bound is the active edge of the feasible
  interval at the chosen price (for the non-interval guardrails: it is the rule
  that fired).
* **Changed decision state / final price** - leave-one-out: relaxing only this
  constraint changes the decision state / the final price.

| Constraint | Present | Removes candidates | Binding at optimum | Changed decision state | Changed final price |
| --- | ---: | ---: | ---: | ---: | ---: |
| Max price change | 13,964 (100.0%) | 13,964 (100.0%) | 10,329 (74.0%) | 166 (1.2%) | 7,874 (56.4%) |
| Absolute minimum price | 13,964 (100.0%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) |
| Absolute maximum price | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) |
| Minimum gross margin | 13,964 (100.0%) | 13,964 (100.0%) | 982 (7.0%) | 78 (0.6%) | 802 (5.7%) |
| Cost floor (p >= c) | 13,964 (100.0%) | 13,964 (100.0%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) |
| Extrapolation guardrail | 13,964 (100.0%) | 13,964 (100.0%) | 2,103 (15.1%) | 1,166 (8.4%) | 1,165 (8.3%) |
| Price rounding / grid step | 13,964 (100.0%) | 0 (0.0%) | 6,978 (50.0%) | 0 (0.0%) | 6,978 (50.0%) |
| Materiality threshold | 13,964 (100.0%) | 0 (0.0%) | 716 (5.1%) | 716 (5.1%) | 700 (5.0%) |
| Risk gate (HIGH / MEDIUM) | 13,964 (100.0%) | 0 (0.0%) | 3,370 (24.1%) | 1,354 (9.7%) | 2,988 (21.4%) |
| Historical support (eligibility) | 13,964 (100.0%) | 0 (0.0%) | 3,373 (24.2%) | 2,668 (19.1%) | 24 (0.2%) |

At least one interval constraint binds in 10,436 contexts
(74.7%).

## 3. Which constraint stops the model first

Walking from the current price toward the unconstrained optimum, the first
guardrail encountered:

| First binding constraint | Contexts | Share |
| --- | ---: | ---: |
| MAX_PRICE_CHANGE | 6,222 | 44.6% |
| HISTORICAL_SUPPORT | 3,373 | 24.2% |
| NONE | 1,924 | 13.8% |
| EXTRAPOLATION | 1,877 | 13.4% |
| MATERIALITY | 565 | 4.0% |
| RISK_GATE | 3 | 0.0% |

## 4. Decision states

| Decision state | Contexts | Share |
| --- | ---: | ---: |
| RECOMMEND_CHANGE | 8,265 | 59.2% |
| KEEP_CURRENT | 4,346 | 31.1% |
| REVIEW_REQUIRED | 1,353 | 9.7% |

## 5. What actually determined each final recommendation

One mutually exclusive bucket per context, assigned in priority order
(screened out > risk gate > materiality > guardrail corner > learned signal):

| Determinant of the final price | Contexts | Share |
| --- | ---: | ---: |
| guardrail corner (optimum outside the feasible interval) | 7,967 | 57.1% |
| screened out before optimisation (eligibility / cost) | 3,528 | 25.3% |
| risk gate (REVIEW_REQUIRED, not actionable) | 1,353 | 9.7% |
| materiality threshold (KEEP_CURRENT) | 716 | 5.1% |
| learned signal (interior optimum, actionable) | 400 | 2.9% |

**2.9%** of final
recommendations are set by an interior optimum of the estimated price response
inside an unbinding feasible interval. Everything else is decided by a
guardrail, a screen or a gate. Note that even the "learned signal" bucket is
mostly the *elasticity*, not the demand forecast: see Section 1.

## 6. Reading of the result

See `reports/13_GUARDRAIL_ABLATION.md` for what happens when the guardrails are
loosened one layer at a time, and `docs/INTERVIEW_RED_TEAM.md` Q2 for how to
defend this architecture. The short version is in
`reports/VALIDATION_SUMMARY.md`.

---

*Generated by `scripts/audit_constraints.py` in 15.8s.
Data: `artifacts/metrics/constraint_attribution.csv` (one row per context).*
