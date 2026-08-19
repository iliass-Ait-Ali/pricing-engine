# Interview guide

Questions an interviewer will ask about this project, with the answers grounded
in what the code actually does. Every number below is reproducible from the
reports.

## Data

**1. What exactly is the grain of your data, and how did you verify it?**
One valid `upc x store x week` observation. Verified, not assumed: the raw file
had 0 exact duplicates and 0 duplicate keys, and `validate_processed` asserts
grain uniqueness on the canonical table (`test_canonical_grain_is_unique`).

**2. You dropped 26.6% of the raw rows. Justify it.**
Those rows have `price = 0`: the item carried no price that week (not stocked /
not priced). A unit price cannot be derived, so they cannot inform a pricing
model. A further 2.14% have `ok = 0`, which the Kilts manual defines as
suspect/trash. Every rule, its row count and its share of raw are printed in
`reports/01_DATA_AUDIT.md`; nothing was dropped silently, and no ad-hoc outlier
cut-off was applied.

**3. Why is `price / qty` necessary?**
`price` is the price of the *bundle* scanned and `qty` the number of units in
it. 0.105% of rows have `qty > 1`. Ignoring it would misprice those rows by a
factor of 2-4. Revenue is therefore `price * move / qty`.

**4. What is AAC and why does the naming matter?**
`profit` is a gross-margin percentage computed on Average Acquisition Cost - an
inventory accounting measure. It is not necessarily the replacement cost
relevant to a pricing decision. The column is named `estimated_unit_aac`, never
`true_unit_cost`, and the optimizer uses a lagged version as the decision-time
cost.

## Causality

**5. Your naive elasticity is -0.35 and your fixed-effects elasticity is -2.42.
Explain.**
The pooled regression compares *across* products: premium cereals are expensive
and sell steadily, bulk brands are cheap and sell in volume, so cross-product
composition swamps the within-product price response. UPC fixed effects remove
the composition; the estimate moves to -2.29, and adding store effects gives
-2.42. Adding promotion, seasonality and trend controls brings it to -1.91.
None of these is causal - they are progressively less contaminated.

**6. Why can't you claim causal effects?**
Prices were set by the retailer in response to expected demand, promotions,
costs and competition, so `P(Q | price)` is not `P(Q | do(price))`. Concretely:
Bonus Buys arrive with displays and feature ads the data never records;
promotion coding is incomplete (a missing code does not prove no promotion);
5.1% of UPCs even show a *significant positive* price coefficient, which is
evidence of price moving with unobserved demand.

**7. Dominick's ran experiments. Why don't you use them?**
The broader research programme did, but the Cereals movement file used here
carries no experiment assignment or treatment window that I could recover and
verify. Claiming experimental identification without that mapping would be
false.

## Modelling

**8. Why a temporal split, and what would a random split have given you?**
Retail panels are autocorrelated; a random split would let the model see week
300 of a series while predicting week 299, so it would score its own memory of
the series level. The split here is chronological: train weeks 2-257,
validation 258-342, test 343-399, selection on validation only.

**9. Your ridge beat gradient boosting. Isn't that suspicious?**
Validation WAPE 0.4135 vs 0.4226 - close, and the ridge wins on a genuinely
held-out later period, where a boosted model's fine-grained fit to the training
era transfers less well. The lag and relative-price features carry most of the
signal, which suits a linear model on log demand. The selection rule was fixed
in advance (lowest validation WAPE among **price-aware** models), and the test
window was scored once.

**10. Baselines beat by how much, and why exclude them from selection?**
Baselines land at WAPE 0.747-0.801 vs 0.4135. They are excluded from selection
because they contain no price term: they cannot answer a pricing question at
all, however accurate they are. Accuracy is not the objective.

**11. How do you know your model actually responds to price?**
It is measured, not assumed. Every price-dependent feature is recomputed at
+/-2% of the observed price and the change in predicted demand is converted to
an elasticity. Median implied elasticity: -3.10; 100% of 300 sampled contexts
give monotone decreasing demand curves; 0% flat; 0% negative predictions.

**12. Your model's implied elasticity is -3.1 but your econometrics says -1.9.
Which is wrong, and what did you do about it?**
Probably the model: it is steeper than the best-controlled estimate, most
likely because the price features partly absorb promotion effects. I did not
just document it - I changed the engine. The default pricing method is now a
hybrid: the ML model supplies baseline demand at the current price and a
separately estimated elasticity supplies the price response,
`Q(p) = Q_hat(p0) * (p/p0) ** epsilon`. At the current price the hybrid equals
the ML prediction exactly. That preserves the baseline prediction **at the
reference price only**; counterfactual predictions away from it are governed by
the elasticity model. The native ML response
is kept as `--method ml` and compared in
`reports/08_PRICE_RESPONSE_COMPARISON.md`.

**12b. Why shrinkage rather than a plain per-product elasticity?**
Per-product coefficients are noisy: of 372 products, 100 have too little data,
24 come out wrong-signed and 9 are too imprecise. Empirical Bayes weights each
product by its own precision, `w_i = tau^2 / (tau^2 + se_i^2)`, with `tau^2`
estimated by REML at **0.766** on the training window. Precise products keep
most of their own coefficient (mean weight **0.78**); the rest fall back to the
pooled estimate and are flagged `POOLED_ELASTICITY_FALLBACK`.

Phase M found and fixed a defect here: the per-UPC standard errors were HC1 on
a clustered panel and therefore too small by a median factor of 3.8, which
inflates `w_i` from both directions at once. The mean weight was 0.95 before the
correction and 0.78 after. See `reports/14_SHRINKAGE_AUDIT.md` and
`reports/15_ELASTICITY_INFERENCE_AUDIT.md`.

**12c. How much does the choice of price response actually matter?**
On 300 matched contexts: ml vs pooled disagree by $0.087 on average (2.7% of
price) and agree on the decision state 79.7% of the time; ml vs shrunk 86.7%.
Under wide what-if guardrails the candidate profit-maximising price (under model assumptions) moves by a
median of 21.5%
across the -1.5 to -3.1 elasticity scenarios; under production guardrails the
constraints bind first, so the spread collapses to zero - which tells you the
guardrails, not the elasticity, are deciding most prices.

**12d. How do you know the elasticity did not leak the evaluation period?**
It is estimated on the model's training weeks only (2-257), the window is
recorded in the elasticity table's metadata, and the estimation script aborts
if any input row exceeds it.

## Optimization

**13. Show me that your optimizer is actually correct.**
Analytical fixtures. For linear demand `Q = a - b p` and constant cost `c`, the
profit optimum is `p* = (a + b c) / (2 b)`; the test builds that fixture and
asserts the optimizer lands within one grid step. Revenue optimum `a / (2 b)`
is tested the same way, plus a constant-elasticity fixture, plus tests for each
constraint, materiality, rounding and missing cost.

**13b. A HIGH-risk context produced an automatic price change. Acceptable?**
No, and it no longer happens. Risk now means risk (HIGH = risky), the bands are
stricter than the eligibility screen, and `high_risk_action` gates them:
`review_required` under the standard profile, `keep_current` under
conservative. Only the explicitly labelled DEMO `aggressive` profile can act on
HIGH risk. Latest batch of 3,000 contexts: 1,039 HIGH-risk, **zero** actionable,
311 routed to `REVIEW_REQUIRED`.

**13c. What is `REVIEW_REQUIRED` for?**
It preserves information that a binary change/keep decision throws away: the
engine has a proposal, but the evidence is too thin to apply it automatically.
It is a human queue, and I count it (10.4% of contexts) rather than hiding it
inside "keep current".

**14. Why does your engine ever recommend "no change"?**
Because most of the time that is the right answer. 31.4% of the batch run were
keep-current: failed eligibility (insufficient history or price variation),
already optimal, or estimated gain below the materiality threshold. Given a
weekly WAPE of ~0.46, a 0.3% estimated improvement is noise.

**15. Someone tells you the engine promises +7.4% profit. What do you say?**
That it is a **model-estimated** portfolio figure from an offline simulation in
which the same model chose and scored the prices, on a model whose implied
elasticity is steeper than the econometric benchmark. Demand at those prices was
never observed. The claim to make is "worth testing on a few dozen stores", and
`docs/PRICING_EXPERIMENT.md` designs that test: store-level randomisation,
gross profit per store-week, 12 weeks, clustered inference, guardrails on
complaints and extreme changes.

## Engineering

**16. How do you prevent leakage?**
Ten channels are enumerated in `docs/DATA_LEAKAGE_AUDIT.md`, each with a
control and a test. The sharp one: rolling windows are computed on the
*already shifted* series, and a test poisons week `t`'s outcome, rebuilds, and
asserts nothing at weeks <= `t` changes while `t+1` does.

**17. How does the simulator guarantee it isn't cheating?**
Two invariants, both tested: only price-dependent features change with the
candidate price (context features are asserted constant), and cost is held
fixed across the grid so that margin rate varies with price rather than being
mechanically reused from history.

**18. Why a grid instead of a solver?**
The model may be a step function, so gradients are meaningless; a grid makes
constraints, reason codes and tests exact. It is vectorised - one batched
prediction per context, asserted by a test.

**19. What would you do differently with more time?**
Run the experiment. Then: cross-price effects within the category (substitution
is the biggest missing mechanism), a proper promotion-calendar feature,
quantile or distributional forecasts so the optimizer can weigh risk rather
than expectations, and per-series price-response validation gating each
recommendation rather than a sampled audit.

**20. What is the weakest part of this project?**
The counterfactual evaluation. Everything up to the demand model is verifiable;
everything after it depends on a price response that no experiment in this data
can identify. That is why the reports separate "verifiable" from
"model-estimated" explicitly, and why the engine is built to be conservative
rather than confident.
