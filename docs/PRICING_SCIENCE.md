# Pricing science

The whole engine rests on four equations and one uncomfortable fact.

## 1. The equations

```text
Demand        Q = f(P, X)                 X = product, store, season, promotion, history
Revenue       R(P) = P * Q(P)
Gross profit  GP(P) = (P - C) * Q(P)      C = unit cost
Elasticity    E = (%change in Q) / (%change in P) = dlogQ / dlogP
```

For the constant-elasticity case `Q = k * P^E` the profit-maximising price has
a closed form:

```text
P* = C * E / (1 + E)        (only defined for E < -1)
```

Two consequences that catch people out in interviews:

* **If demand is inelastic (|E| < 1), profit rises with price without bound**
  in this model - the optimum is set by constraints, not by the demand curve.
  `tests/test_optimizer.py::test_constant_elasticity_fixture_pushes_price_to_the_upper_bound`
  encodes exactly that behaviour.
* **The revenue optimum and the profit optimum are different prices.** Revenue
  is maximised where `E = -1`; profit is maximised at a strictly higher price
  whenever `C > 0`. In this project's Phase F run, the revenue optimum sat
  below the current price in 100% of sampled contexts while the profit optimum
  did so in only 27% - the same demand curve, two different answers.

For linear demand `Q = a - b P` the analytical optima are

```text
profit:  P* = (a + b C) / (2 b)
revenue: P* = a / (2 b)
```

These are the ground truth in the optimizer's analytical tests.

## 2. Elastic vs inelastic

| |E| | meaning | price increase does |
| --- | --- | --- | --- |
| > 1 (elastic) | volume reacts more than proportionally | reduce revenue, may still raise profit |
| < 1 (inelastic) | volume barely reacts | raise revenue and profit |
| = 1 | unit elastic | leave revenue unchanged |

Measured on this Cereals panel, the controlled estimates are around **-1.9 to
-2.4**, and the per-product spread runs from about -4.1 (p10) to -0.2 (p90).
Cereals is a promotion-heavy, highly substitutable category, so elastic demand
is expected.

## 3. The cost caveat that most portfolio projects miss

Dominick's reports `profit` as a **gross margin percentage** computed on
**Average Acquisition Cost (AAC)** - an inventory accounting measure of what
the retailer paid *on average* for the stock on hand. It is not necessarily the
economically relevant replacement cost at the moment of the decision (forward
buying, trade deals, inventory ageing).

Two rules follow, both enforced in code:

1. The implied cost column is named `estimated_unit_aac`, never
   `true_unit_cost`.
2. **Cost must be held fixed when the candidate price changes.** Reusing the
   historical margin *percentage* at a new price silently assumes the retailer
   renegotiates cost every time it changes a shelf price, which mechanically
   manufactures profit. `tests/test_simulation.py::test_cost_is_held_fixed_across_price_grid`
   fails if anyone reintroduces that shortcut.

The engine uses a **lagged, forward-filled AAC** as the decision-time cost, and
refuses gross-profit optimization (reason code `COST_UNAVAILABLE`) when no
prior cost exists.

## 4. Promotions

A recorded `sale` code (`B` Bonus Buy, `C` Coupon, `S` simple reduction) marks a
promotion, but the coding is incomplete: a missing code does **not** mean no
promotion happened. Promotions also arrive bundled with features and displays
that the data never records.

Measured here: promotion weeks show elasticity **-2.66** vs **-1.83** on
non-promotion weeks. Part of what a naive "price effect" captures is really the
marketing bundle around the price cut.

## 5. Endogeneity: correlation is not the causal effect

Prices were set by the retailer in response to demand, competition and cost -
not randomised. Formally, the data identify

```text
P(Q | price, X)      not      P(Q | do(price), X)
```

The gap is not theoretical. In this project the same dataset gives:

| specification | elasticity |
| --- | --- |
| naive pooled | -0.35 |
| + UPC fixed effects | -2.29 |
| + UPC x store fixed effects | -2.42 |
| + promotion, seasonality, trend | -1.91 |

A seven-fold move produced by controls alone. Any pricing claim built on the
first row would be indefensible; the last row is only *less* contaminated, not
causal. See `docs/CAUSAL_LIMITATIONS.md`.

## 6. Why the split must be temporal

Retail panels are time-indexed and highly autocorrelated. A random split puts
week 300 of a series in train and week 299 in test, so the model can memorise
the level of each series and the score flatters it. Every evaluation here uses
a chronological split (train weeks 2-257, validation 258-342, test 343-399),
and the backtest scores only weeks after the training window.

## 7. Why an accurate model can still be an unsafe pricer

Accuracy is measured **at the prices that were actually charged**. Optimization
asks the model about prices that were never charged. A model can be excellent
at the first task and wrong about the second:

* it may have learned a price coefficient that is really a promotion effect;
* it may be flat in price (accurate, useless - it would recommend raising price
  forever);
* it may be steeper than reality (accurate, dangerous - it recommends cuts that
  will not pay for themselves).

This project measures the third case explicitly: the selected model's implied
elasticity is about **-3.1**, steeper than the controlled econometric estimate
of -1.9 to -2.4. That is documented in
`reports/05_PRICE_RESPONSE_VALIDATION.md` and is the reason the default policy
keeps price moves small.

## 8. Price-response sanity checks

Before the optimizer is allowed to use a model, every sampled context is swept
over a price grid with all other features fixed, and the resulting curve is
checked for:

* monotone decreasing demand,
* absence of flat response,
* no negative predicted demand,
* location of the optimum relative to the swept range.

## 9. Extrapolation

A demand model has no information outside the price range a series actually
experienced. The engine therefore constrains candidates to the observed support
of that UPC x store, expanded by a configured tolerance, and intersects that
with a maximum-change window. When the guardrail binds, the recommendation
carries `OUTSIDE_EXTRAPOLATION_RANGE` so the reader knows the number is a
constrained answer, not the model's unconstrained optimum.

## 10. Materiality

A model-estimated 0.3% profit gain is noise relative to a WAPE of ~0.46. The
optimizer therefore requires the estimated improvement to exceed a configured
materiality threshold, otherwise it returns `KEEP_CURRENT_PRICE`. A pricing
engine that always finds a better price is not doing science.

## 11. Separating the forecast from the price response (Phase L)

A demand model is scored on how well it predicts units **at the prices that
were charged**. A pricing engine asks it about prices that were never charged.
Those are different questions, and this project answers them with different
tools:

```text
Q(p) = Q_hat(p0) * (p / p0) ** epsilon
```

* `Q_hat(p0)` - the ML model's baseline prediction at the current price. All
  context, seasonality, promotion state and lagged demand live here.
* `epsilon` - a separately estimated price elasticity: pooled controlled, or a
  per-UPC estimate shrunk toward the pooled value by empirical Bayes.

At `p = p0` the hybrid reproduces the ML prediction exactly. That preserves
the selected forecaster's baseline prediction **at the reference price only**;
counterfactual predictions away from the reference price are governed by the
elasticity model, whose specification, standard error and shrinkage weight are
all recorded.

It would be wrong to summarise this as "no forecast skill is lost". That would
be a claim about *all* prices. What holds is the narrower statement above: the
baseline is exact at `p0`, and counterfactual accuracy away from `p0` is a
separate empirical question, measured out of time in
`reports/16_OUT_OF_TIME_PRICE_RESPONSE.md`.

**Why it was needed here.** The selected model's implied elasticity is about
**-3.10**, while the controlled econometric estimates are **-1.91 to -2.42**.
Using the forecaster directly for counterfactuals would import that steepness
into every recommendation - systematically favouring price cuts on elastic
series and overstating estimated uplift.

### Shrinkage in one line

```text
w_i = tau^2 / (tau^2 + se_i^2)          epsilon_i = w_i * eps_hat_i + (1 - w_i) * eps_pooled
```

A precisely estimated product keeps its own coefficient; a noisy one is pulled
back to the category. `tau^2 = var(estimates) - mean(se^2)` is the part of the
observed spread that is *not* sampling noise. On the Cereals training window
`tau^2 = 0.766` (REML) and the mean weight is 0.78, i.e. products genuinely do
differ - but by less than the Phase L figure of 0.95 suggested, which was
inflated by understated standard errors (`reports/14_SHRINKAGE_AUDIT.md`).

### What shrinkage does not fix

Shrinkage improves stability, not identification. If the pooled elasticity is
contaminated by promotion effects, every shrunk estimate inherits part of that
contamination. Only an experiment fixes that (`docs/PRICING_EXPERIMENT.md`).
