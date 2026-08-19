# 12. Model value ablation - learned price response vs rule-only pricing

**Question.** Does the learned demand / elasticity layer produce materially
different decisions from reasonable rules?

**Not the question.** Whether the model earns more money. Scoring a rule with
the model's own demand curve and declaring the model better is circular - the
same fitted curve would be both proposer and judge. No profit comparison is
made here. See `docs/CAUSAL_LIMITATIONS.md`.

**Setup.** All 13,964 `UPC x store` contexts of week 399, policy profile
`standard`, price response `shrunk`. Every rule is pushed
through the *same* policy layer as the model - eligibility screen, risk gate,
feasible interval, rounding, materiality, decision state - so the comparison
isolates the pricing signal, not the guardrails. Rule inputs (median margin,
modal price) are computed from **training weeks only**.

## 1. The rules

| Rule | Definition |
| --- | --- |
| `R0 keep current price` | never move a price; the only policy whose outcome was actually observed |
| `R1 hold historical margin` | p = c / (1 - m), m = that series' median historical gross-margin rate |
| `R2 cost-plus 25% margin` | p = c / (1 - 0.25), one fixed category margin target |
| `R3 nearest historical modal price` | snap to the most frequent historical price of that series |
| `R4 always take the max allowed increase` | p = p0 * (1 + effective change cap); no demand model at all |

## 2. Decision agreement with the model policy

| Rule | Final price differs at all | Differs by > 1 grid step (5c) | Mean abs diff | Median abs diff | Median abs diff % | Same decision state | Model keeps / rule changes | Model changes / rule keeps |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| R0 keep current price | 59.7% | 59.1% | $0.141 | $0.130 | 4.3% | 30.7% | 0.5% | 59.1% |
| R1 hold historical margin | 61.4% | 51.5% | $0.215 | $0.120 | 4.2% | 90.0% | 5.7% | 4.0% |
| R2 cost-plus 25% margin | 59.7% | 34.9% | $0.107 | $0.030 | 1.0% | 88.8% | 5.6% | 3.8% |
| R3 nearest historical modal price | 62.2% | 49.5% | $0.179 | $0.050 | 2.5% | 78.9% | 4.6% | 7.6% |
| R4 always take the max allowed increase | 59.2% | 17.5% | $0.060 | $0.019 | 0.6% | 90.0% | 5.6% | 4.2% |

**The headline.** The rule that never looks at demand at all - `R4 always take
the max allowed increase` - lands within one 5-cent grid step of the model's
final price in **82.5%** of
contexts and agrees on the decision state in
90.0%; the median absolute price difference
is $0.019
(0.6% of price). The exact-match rate
is lower (40.8%) only because the 5-cent
candidate grid rarely contains the cap itself. The closest rule overall is
`R4 always take the max allowed increase`.

This is the same fact `reports/11_CONSTRAINT_ATTRIBUTION.md` reports from the
other direction: the model's unconstrained optimum sits above the change cap in
most contexts, so the constrained answer is "go to the cap", which is exactly
what R4 does by construction.

## 3. Where the model and the rules do differ

The learned layer is not decorative. It differs from R4 in the contexts where

* the estimated elasticity implies an optimum *below* the current price
  (a price cut), which R4 can never propose;
* the optimum is interior, so the recommended move is smaller than the cap;
* the estimated uplift is immaterial and the model keeps the price while R4
  would still move it.

Model decisions: {'RECOMMEND_CHANGE': 8265, 'KEEP_CURRENT': 4346, 'REVIEW_REQUIRED': 1353}
Median actionable price change (model): +8.36%

## 4. Distribution of recommendation magnitudes (actionable contexts only)

|                                         |   <1% |   1-2.5% |   2.5-5% |   5-7.5% |   7.5-10% |   >10% |
|:----------------------------------------|------:|---------:|---------:|---------:|----------:|-------:|
| MODEL (shrunk elasticity)               |     0 |        2 |     2620 |      284 |      5150 |    209 |
| R0 keep current price                   |     0 |        8 |        9 |        1 |        76 |      0 |
| R1 hold historical margin               |     0 |      742 |     2038 |     1877 |      2859 |    982 |
| R2 cost-plus 25% margin                 |     0 |      334 |     2398 |     2026 |      2319 |   1431 |
| R3 nearest historical modal price       |     0 |      621 |     1584 |     1603 |      2404 |   1638 |
| R4 always take the max allowed increase |     0 |        0 |     1603 |      959 |       898 |   5008 |

## 5. Risk distribution

|                                         |   HIGH |   LOW |   MEDIUM |
|:----------------------------------------|-------:|------:|---------:|
| MODEL (shrunk elasticity)               |   4760 |  5511 |     3693 |
| R0 keep current price                   |   4760 |  7162 |     2042 |
| R1 hold historical margin               |   4762 |  7102 |     2100 |
| R2 cost-plus 25% margin                 |   5101 |  6083 |     2780 |
| R3 nearest historical modal price       |   4760 |  7162 |     2042 |
| R4 always take the max allowed increase |   5358 |  5263 |     3343 |

## 6. Where each policy's actionable price sits in the feasible interval

The feasible interval is identical for every policy (it depends only on the
context and the guardrails), so what separates the policies is *where inside it
they land*. A policy that always sits on a bound is not being priced by its
demand signal.

|                                         |   at upper bound |   at lower bound |   interior |   interior % |
|:----------------------------------------|-----------------:|-----------------:|-----------:|-------------:|
| MODEL (shrunk elasticity)               |             7112 |              689 |        464 |          5.6 |
| R0 keep current price                   |                0 |               94 |          0 |          0   |
| R1 hold historical margin               |             1118 |             3998 |       3382 |         39.8 |
| R2 cost-plus 25% margin                 |             3091 |             2714 |       2703 |         31.8 |
| R3 nearest historical modal price       |             2180 |             2696 |       2974 |         37.9 |
| R4 always take the max allowed increase |             8468 |                0 |          0 |          0   |

## 7. Conclusion

The learned elasticity layer changes *which direction and how far* to move in a
minority of contexts, and is otherwise dominated by the change cap. The honest
portfolio claim is therefore:

> The elasticity layer decides the direction of the price move and identifies
> the contexts where a cut is better than an increase; the guardrails decide the
> magnitude in most contexts.

It is **not** defensible to claim that the ML pipeline is what produces the
recommended prices in the standard configuration.

---

*Generated by `scripts/audit_model_value.py` in 9.6s.*
