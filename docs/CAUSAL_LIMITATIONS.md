# Causal limitations

## The core problem

The engine estimates

```text
P(units | price, context)          <- what the data supports
```

and the pricing decision needs

```text
P(units | do(price), context)      <- what we would like
```

These differ whenever price is chosen in a way that correlates with unobserved
demand drivers. In the Dominick's data, it certainly is.

## Evidence from this project's own numbers

Fitting `log(units) ~ log(price)` on the Cereals panel
(`reports/03_ELASTICITY_ANALYSIS.md`, 1.2 M-row sample):

| specification | estimated elasticity |
| --- | --- |
| naive pooled | **-0.35** |
| + UPC fixed effects | **-2.29** |
| + UPC x store fixed effects | **-2.42** |
| + promotion, seasonality, trend | **-1.91** |

A seven-fold change in the headline number produced purely by controls is the
clearest possible demonstration that the naive association is not a causal
effect. Nothing guarantees that the last row is the causal effect either - it
is only the least contaminated of the four.

## Specific threats to identification

1. **Promotion bundling.** Bonus Buys arrive together with feature ads, display
   space and end-caps that the data does not record. A price cut therefore
   proxies for a whole marketing bundle, and the estimated price response
   absorbs the effect of the bundle. Estimated separately, promotion weeks show
   elasticity -2.66 vs -1.83 on non-promotion weeks.
2. **Incomplete promotion coding.** A missing `sale` code does not prove no
   promotion occurred, so the "clean" subsample is not clean. Every control for
   promotion is therefore partial by construction.
3. **Retailer anticipation / reverse causality.** Prices are cut *because* a
   demand surge is expected (holidays, seasonal events, competitor activity).
   That makes low prices coincide with high demand for reasons unrelated to the
   price cut, biasing elasticity away from the truth in an unknown direction.
4. **Seasonality and trend.** Category demand and price levels both drift over
   eight years; without time controls part of that co-movement is attributed to
   price.
5. **Store heterogeneity and zone pricing.** Dominick's priced by zones tied to
   local competition and demographics. Cross-store price differences reflect
   local demand, not exogenous variation - which is why store fixed effects
   move the estimate.
6. **Product heterogeneity.** The per-UPC spread is p10 = -4.09 to
   p90 = -0.24, and 5.1% of UPCs show a *significant positive* coefficient.
   Positive coefficients are not "wrong data"; they are evidence that for those
   series price moves with unobserved demand.
7. **Stock-outs and supply constraints.** Zero or suppressed sales caused by
   unavailability look identical to demand collapse in scanner data.
8. **Competitive response.** Rival stores' prices are unobserved.

## About the Dominick's experiments

The Dominick's research programme at Chicago Booth included in-store pricing
experiments in several categories. **This project does not claim any of its
estimates are experimental.** The Cereals movement file used here contains no
experiment assignment, treatment window or zone-experiment label that we could
recover and verify. Any claim of randomised identification would require that
mapping to be established first.

## What the project claims instead

Allowed and used throughout:

* "historical observed revenue / gross margin"
* "predicted demand"
* "model-estimated counterfactual revenue"
* "model-internal estimated gross-profit uplift"
* "offline policy simulation"
* "observational price-response estimate"

Never claimed:

* causal profit uplift, actual uplift, proven optimal price, validated causal
  engine, production ready.

## How the system compensates in practice

Since identification is weak, the engine is built to be **conservative rather
than confident**:

* extrapolation guardrail: recommendations stay inside observed price support;
* materiality threshold: small model-internal estimated gains produce `KEEP_CURRENT`;
* eligibility screen: series with too little price variation are never priced
  by the model;
* heuristic risk level (HIGH = risky) that routes HIGH-risk contexts to
  `REVIEW_REQUIRED` or `KEEP_CURRENT` and never to an automatic price change;
* a price response taken from a separately estimated elasticity rather than
  from the forecaster's own price coefficient (Phase L);
* price-response validation (Phase F) that rejects a model whose demand curve
  slopes the wrong way for a series before it is allowed to price it.

## The only real fix

A randomised or quasi-randomised price experiment. `docs/PRICING_EXPERIMENT.md`
designs one: randomisation unit, duration, power, guardrails and rollout. Until
such an experiment runs, every number this engine produces about alternative
prices is a **model-internal estimated counterfactual**: the same fitted price
response both proposes and scores it.
