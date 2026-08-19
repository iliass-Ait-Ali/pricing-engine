# Interview red team

The twenty hardest questions a senior data scientist, pricing scientist,
econometrician or BCG X interviewer can ask about this project - with the answer
you would give if you had all day, the answer you give in 20-40 seconds, and the
thing you must not say.

The short answers are written to be spoken. The rigorous answers are written to
survive a follow-up.

---

## Q1. Why should I trust your elasticity?

**Rigorous.** You shouldn't trust it as a causal object, and I don't claim it as
one. What I can defend is a specification and an uncertainty. The headline
number is a pooled log-log coefficient of **-2.03** with UPC x store fixed
effects absorbed and promotion, seasonality and trend controls, estimated on
training weeks 2-257 only. The fixed effects mean it is identified from price
movement *within* a product-store series, not from the cross-section - which
kills the most obvious confound, that expensive products are premium products
with different buyers. The precision is not the conventional standard error:
clustering two-way by UPC and week widens the standard error from 0.0065 to
**0.106**, a factor of 16, and the honest 95% interval is
**[-2.24, -1.84]**. Three independent checks bracket it: the plain fixed-effects
specification gives -2.22, the empirical-Bayes product distribution has a median
of -1.90, and out-of-time price-change episodes in weeks 258-399 imply a median
elasticity of **-1.86** (non-promotion) to **-2.45** (all episodes). Four
different routes landing between -1.8 and -2.5 is what "trust" means here.

**Short.** Not as a causal number - as an observational one with a specification
and an interval. It's -2.03 with UPC-by-store fixed effects, so it comes from
within-series price movement, not from comparing premium to budget cereals.
Two-way clustered, the interval is -2.24 to -1.84. The plain FE spec, the
product-level median and out-of-time price-change episodes all land in the same
band, which is the real reason I believe it.

**Do not say.** "The elasticity is -2.03." (No interval.) "We validated the
elasticity causally." "Demand falls 2% for every 1% price rise" as a statement
about the world rather than about a fitted model.

---

## Q2. If guardrails dominate, why do you need ML at all?

**Rigorous.** This is the question I would ask, and the audit says you are
largely right. Under the shipped policy, **2.9%** of final recommendations come
from an interior optimum of the estimated price response; 57.1% are guardrail
corners, 25.3% are screened out before optimisation, 9.7% are risk-gated and
5.1% fail materiality. A rule that never looks at demand at all - "raise every
eligible price by the maximum allowed" - lands within one 5-cent grid step of
the model's price in **82.5%** of contexts and agrees on the decision state in
90.0%. Worse, there is an algebraic reason: under the hybrid response
`Q(p) = Q̂(p0)·(p/p0)^ε`, the baseline `Q̂(p0)` is a constant across candidates
and cancels out of the arg-max, so the *forecaster* never chooses the price at
all - only `(p0, cost, ε)` and the guardrails do. So what is the ML for? Three
things it genuinely does: it decides the **direction** (the closed form
`p* = c·ε/(1+ε)` is what tells you a cut beats an increase for some products,
which the naive rule can never propose); it produces the **volume and dollar
estimates** that make a proposal reviewable; and it drives the **screening** -
which contexts are eligible at all. What it does not do, under production
guardrails, is set the magnitude. I would not ship this as "ML pricing". I would
ship it as "rule-bounded pricing with a learned direction and a learned
screen".

**Short.** Mostly you're right, and I measured it: 2.9% of final recommendations
come from an interior model optimum, and a dumb "always take the max increase"
rule matches the model 83% of the time. There's even an algebraic reason - the
forecast level cancels out of the arg-max. What the model does earn is the
direction of the move, the screening, and the dollar estimates a reviewer needs.
I'd describe this as rule-bounded pricing with a learned direction, not as ML
pricing.

**Do not say.** "The ML model produces the recommendations." "The guardrails are
just a safety net." Anything that implies the magnitude is model-driven.

---

## Q3. How do you know price *caused* the demand changes?

**Rigorous.** I don't, and nothing in the project claims it. Dominick's prices
were set by the retailer, so `P(Q | price) ≠ P(Q | do(price))`. Three specific
threats: (1) **reverse causality / anticipation** - prices are cut *because* a
demand spike is expected, which biases the coefficient away from zero; (2)
**omitted variables** - display, feature advertising and end-cap placement move
with price and are only partially captured by the promotion code; (3)
**selection** - weeks in which the retailer changed price are not a random
sample of weeks. What I did instead is show the *direction and size* of the
contamination: the naive raw association is far steeper than the controlled
within-series estimate, which is itself a demonstration that the raw number is
not the causal effect. And I wrote down the experiment that would identify it -
a store-randomised price test, `docs/PRICING_EXPERIMENT.md`. The out-of-time
check in `reports/16` is labelled naturalistic prediction, not identification,
in its first line.

**Short.** I don't, and I say so. Prices weren't randomised, so this is
association, not `do(price)`. The three threats are anticipation, omitted display
and feature activity, and selection into which weeks get a price change. What I
can show is how much the contamination moves the number - the raw association is
far steeper than the within-series estimate - and I've specified the
store-randomised experiment that would actually identify it.

**Do not say.** "The controlled specification removes the endogeneity." "Fixed
effects make it causal." "We validated the price effect."

---

## Q4. Your WAPE is 45.7%. Isn't that terrible?

**Rigorous.** For this target it is good, and the comparison that matters is the
baseline, not an absolute threshold. Weekly units per UPC per store are small
counts with a heavy tail - the median is single digits, the maximum is over
20,000 - and WAPE on small counts is dominated by items where being off by three
units is a 100% error. The best naive baseline (rolling 4-week mean) scores
**0.7326** on the same test rows; the model scores **0.4565**, a 38% reduction,
on a strictly chronological split with 749,040 held-out rows. If someone quotes
a 15% WAPE for demand forecasting, they are almost certainly aggregating - to
category, chain or month - and aggregation is where WAPE goes to look good. I
would rather report the number at the decision grain, which is the grain the
optimizer actually prices at.

**Short.** It's measured at UPC-by-store-by-week, which is the grain we price
at, and weekly unit counts there are tiny and heavy-tailed. The right comparison
is the naive baseline at 0.733 against the model at 0.457 - a 38% reduction on
749k held-out rows in a chronological split. Aggregate to category or month and
that number would look far prettier and mean far less.

**Do not say.** "WAPE is high because the data is noisy." (True but evasive.)
Any comparison to a published WAPE at a different aggregation level.

---

## Q5. Why did Ridge beat gradient boosting?

**Rigorous.** Two reasons, and one of them is a limitation. First, the target is
multiplicative: log demand is close to linear in log price, lagged log demand and
seasonality, which is exactly the functional form a log-log ridge encodes and
which trees have to approximate with splits. Second - the limitation - the test
window is the *future*, and the boosting model has a `time_index` feature it can
split on but cannot extrapolate beyond; trees saturate at the last training
value. I removed `time_index` from the linear model's feature set for the
opposite reason: standardising an unbounded trend and extrapolating it into the
future explodes once you exponentiate. So the comparison is partly a statement
about which inductive bias survives a temporal shift, not about which family is
stronger in general. On the validation window the gap is smaller. I'd want to
re-run this with target encoding and a proper time-aware CV before drawing a
general conclusion.

**Short.** The demand process is multiplicative, and log-log ridge encodes that
directly while trees have to approximate it with splits. The bigger reason is
the temporal split: trees can't extrapolate a time trend past their last
training value. So it's a statement about which inductive bias survives a
forward shift, not that ridge beats boosting in general.

**Do not say.** "Linear models are better for demand." "Boosting overfit." (I
didn't establish that; early stopping was off and I'd need a learning curve.)

---

## Q6. Why use AAC as your cost, and is that leakage?

**Rigorous.** Dominick's does not publish cost. It publishes a gross-margin
percentage per row, from which an average acquisition cost can be implied:
`AAC = price × (1 - margin)`. The contemporaneous margin is an **outcome** of the
week being priced, so using it would be leakage of the worst kind - it would
mechanically fix the margin percentage and fabricate profit at any candidate
price. The feature actually used is `decision_time_unit_cost`: the AAC implied by
the *previous* week, forward-filled within the series. `scripts/audit_cost_leakage.py`
and `tests/test_cost_leakage.py` assert that the value used at week `t` is
computable from weeks `< t` only, and the counterfactual simulator holds cost
fixed while price varies, so `(p - c)·Q(p)` never reuses a margin. The residual
weakness is real and disclosed: AAC is an *average* acquisition cost, so it lags
actual replacement cost and is contaminated by inventory effects and by
retroactive trade deals.

**Short.** There's no published cost, only a margin percentage, and the
contemporaneous margin is an outcome - using it would fabricate profit. So I use
the AAC implied by the *previous* week, forward-filled, and the simulator holds
it fixed while price varies. There's a leakage audit and a test that assert
that. The honest weakness is that average acquisition cost lags true replacement
cost.

**Do not say.** "We have unit costs." "The margins are accurate."

---

## Q7. Why exclude the price-zero rows?

**Rigorous.** A price of zero on a scanner row is a data artifact, not a free
cereal: it appears where the movement record has no valid price, and log price is
undefined there anyway. But "it's an artifact" is an assertion, so I audited it -
`reports/09_ZERO_PRICE_AUDIT.md` characterises the excluded rows on volume,
store, week and product mix and checks whether the exclusion is selective on
anything that matters. The exclusion is applied identically to every model and
baseline, so no comparison benefits from it. The manual's `ok = 0` "suspect"
flag is handled separately and is also documented.

**Short.** Zero price is a missing-price artifact, and log price is undefined
there. I didn't just assert that - there's a dedicated audit report profiling the
excluded rows against the kept ones, and the exclusion is applied identically to
every model so no comparison gains from it.

**Do not say.** "They were outliers." "It was only a small percentage." (Say the
percentage, and say what the rows look like.)

---

## Q8. What's circular about your offline uplift number?

**Rigorous.** Everything, and that is why it is labelled
`model_internal_estimated_profit_uplift_pct` in the code and not "uplift". The
same fitted demand curve both proposes the price and scores it. If the curve is
biased in a direction that favours price increases, the optimizer will propose
increases and then congratulate itself with the same bias. It is a self-scored
number and cannot be evidence that the policy is good. This is exactly why the
rule-only ablation in `reports/12` deliberately does **not** compare profits: it
compares *decisions*. The only outcome actually observed in this dataset is the
one produced by the prices Dominick's actually charged. Any profit claim beyond
that requires the randomised test in `docs/PRICING_EXPERIMENT.md`.

**Short.** It's completely circular - the same fitted curve proposes the price
and grades it. That's why the field is called `model_internal_estimated_profit_
uplift_pct` and why the rule-only comparison in the audit compares decisions, not
profits. The only outcome we actually observe is what Dominick's actually
charged.

**Do not say.** "The engine delivers an 8.6% profit uplift." "Backtested
profit." "Simulated profit gains."

---

## Q9. What does the empirical-Bayes shrinkage actually do?

**Rigorous.** It is the posterior mean of a normal-normal hierarchical model.
Assume `ε̂ᵢ | θᵢ ~ N(θᵢ, seᵢ²)` and `θᵢ ~ N(μ, τ²)`. Then
`E[θᵢ | ε̂ᵢ] = wᵢ·ε̂ᵢ + (1-wᵢ)·μ` with `wᵢ = τ²/(τ² + seᵢ²)`. The weight is the
share of the observed variance that is signal rather than sampling noise, so a
product with a precise coefficient keeps it and a product whose coefficient is
mostly noise gets pulled back to the category estimate. Concretely: it stops a
UPC with 210 observations and a wild -5.8 coefficient from being priced as if
demand really collapses that fast. `μ` is the pooled controlled elasticity - the
same number used as the fallback for products with no usable estimate, so a
product does not jump when it crosses the usability threshold. `τ²` is estimated
by REML. Two properties are pinned by tests: the weight is monotone decreasing in
`seᵢ`, and the shrunk estimate always lies between the raw estimate and the prior.

**Short.** It's the posterior mean of a normal-normal hierarchical model: each
product's estimate is a precision-weighted blend of its own coefficient and the
category elasticity, `w = τ²/(τ²+se²)`. Practically, it stops a thin-data UPC
with a wild -5.8 coefficient from being priced as if that were real. The prior
mean is the pooled estimate, which is also the fallback, so nothing jumps at the
usability boundary.

**Do not say.** "It regularises the elasticities." (Vague.) "It's like ridge."
(Different object; the weight here has a specific statistical meaning.)

---

## Q10. Your mean shrinkage weight was 0.95. Isn't the shrinkage doing nothing?

**Rigorous.** That was the right question to ask, and it found a defect. A weight
of 0.95 means the product coefficients pass through nearly untouched. The weight
has two inputs, and only one had been audited: `w` is large either because `τ²`
is genuinely large *or because `seᵢ` is too small*. The per-UPC regressions were
reporting HC1 standard errors on a panel with obvious within-store and
within-week dependence. Clustering them inflates the standard error by a median
factor of **3.4** - and it hits twice, because `τ² = f(spread - mean(se²))` is
simultaneously overstated when `se` is understated. Correcting the standard
errors moved `τ²` from 0.91 to **0.77** and the mean weight from 0.955 to
**0.780**; the product elasticity distribution tightened from p10/p90 of
-3.17/-0.70 to -3.05/-0.95. It is still 0.78, and that part is real: `τ ≈ 0.88`
means the standard deviation of true product elasticities is genuinely large
relative to a median robust standard error of 0.39, so the data really do support
substantial product-specific signal. The estimator's *formula* was never wrong;
its *inputs* were.

**Short.** It was a defect and I fixed it. The weight has two inputs and only τ²
had been audited - the standard errors were HC1 on a clustered panel, understated
by a median factor of 3.4, which inflates the weight from both directions.
Correcting them moved the mean weight from 0.955 to 0.780. It's still high
because τ is genuinely about 0.88 against a median robust SE of 0.39 - cereals
really do differ.

**Do not say.** "0.95 just means products are heterogeneous." (That was the
comfortable answer, and it was half wrong.)

---

## Q11. How would you validate this before changing real prices?

**Rigorous.** A staged rollout, and I would not skip stages. **Stage 0 - shadow
mode:** run the engine daily against live data, log every recommendation, change
nothing, and measure recommendation stability and reviewer agreement for one
quarter. **Stage 1 - a randomised price test:** the design is written up in
`docs/PRICING_EXPERIMENT.md`. Randomise at the *store* level, not the product
level, to avoid within-basket substitution contaminating control stores; stratify
on store volume and format; power it on the elasticity interval, not on a hoped-
for effect - with `ε` in [-2.24, -1.84] and a ±10% price move, the detectable
demand effect is roughly 18-22%, and the sample size follows from the residual
variance of weekly units. Pre-register the primary endpoint as **category** gross
profit, not item gross profit, so cannibalisation cannot be booked as a win.
**Stage 2 - limited live rollout** on LOW-risk contexts only, with the change cap
halved and a weekly kill switch on category margin. **Stage 3** - widen. At no
point does a HIGH-risk context change automatically.

**Short.** Three stages. Shadow mode for a quarter - log recommendations, change
nothing, measure stability and reviewer agreement. Then a store-randomised price
test, stratified on volume, powered off the elasticity interval, with *category*
gross profit as the pre-registered endpoint so cannibalisation can't be booked as
a win. Then a limited rollout on LOW-risk contexts with a halved change cap and a
kill switch.

**Do not say.** "We'd A/B test it." (Say the unit of randomisation and the
endpoint, or it isn't an answer.)

---

## Q12. What happens with substitution and cannibalisation?

**Rigorous.** This is the largest scientific gap in the project and I would lead
with it rather than wait to be asked. Every elasticity here is an **own-price**
elasticity estimated one product at a time. If I raise the price of one cereal by
10% and a shopper switches to the cereal next to it, the model books the units
lost and none of the units gained. The category-level effect of applying the
engine to many products simultaneously is therefore **not** the sum of the
per-product effects, and could be much smaller or even negative. Two things
partially bound the damage: the recommendations are dominated by the ±10% change
cap, so the substitution induced is small per product; and the guardrails prevent
the coordinated large moves that would do the most damage. The fix is a
cross-price demand system - at minimum, adding competitor-price and
category-price-index features and estimating a nested logit or an AIDS-style
system on the segment. That is real work and I did not do it. It is disclosed in
`KNOWN_LIMITATIONS.md`, and it is why the pre-registered endpoint of any live
test must be category profit.

**Short.** It's the biggest gap and I'd raise it unprompted. Every elasticity
here is own-price, one product at a time - if a shopper switches to the cereal
next door, I book the loss and not the gain. So the category effect isn't the sum
of the item effects. The proper fix is a cross-price demand system; I didn't build
one. It's why any live test has to be powered on category profit, not item
profit.

**Do not say.** "The effect is probably small." "Fixed effects handle it."

---

## Q13. What happens when elasticity changes over time?

**Rigorous.** I measured it rather than assumed it. Re-estimating the whole stack
on three disjoint historical windows inside the training period: the pooled
elasticity ranges over 0.42 across windows, but the largest pairwise z-statistic
using two-way clustered standard errors is **1.36** - not significant, so the
*category* elasticity is stable within the precision the data supports. The
product level is a different story: the median pairwise rank correlation of
shrunk product elasticities across disjoint windows is **+0.19**. Sign stability
is high, but the *ordering* of products by price sensitivity does not reproduce.
The system already responds to this in three ways: the empirical-Bayes shrinkage
pulls unstable products toward the stable category number; the risk layer
downgrades thin-evidence contexts; and pooled-fallback contexts carry a reason
code. What I would add for production is a scheduled re-estimation with a
drift alarm on the pooled coefficient, and a rule that a product-specific
elasticity expires if it is not re-confirmed.

**Short.** I tested it on three disjoint windows. The category elasticity is
stable - the biggest pairwise z is 1.36, so within noise. The product level isn't:
rank correlation across windows is only +0.19, so the *ordering* of products by
price sensitivity doesn't reproduce even though the signs do. That's exactly what
the shrinkage is for, and it's why I'd expire product-specific estimates that
aren't re-confirmed.

**Do not say.** "Elasticity is stable." (Only the category one is.) "We
re-estimate regularly." (Not implemented here.)

---

## Q14. Why not causal forests / double ML / an IV?

**Rigorous.** Because none of them manufacture identification, and identification
is what's missing. A causal forest estimates heterogeneous treatment effects
*given* unconfoundedness; here the treatment is price, set by the retailer in
response to expected demand, so unconfoundedness fails and the forest would give
me a beautifully heterogeneous biased answer. Double ML has the same requirement.
An IV is the right instinct, and I looked for one - wholesale cost shocks are the
classic instrument for retail price - but Dominick's gives me AAC, which is
*derived from* price and margin, so it fails the exclusion restriction by
construction. Hausman-style instruments (prices of the same product in other
markets) are contaminated by national demand shocks, which is precisely the
confound I'm worried about. So the honest answer is: I have no credible
instrument in this dataset, and the correct next step is not a fancier estimator
but a randomised price test. Where I *would* use causal ML is after that
experiment, to estimate heterogeneous treatment effects across stores and
segments from randomised variation.

**Short.** They all assume identification I don't have. A causal forest gives
heterogeneous effects *under unconfoundedness*, and price here is set in response
to expected demand. IV is the right instinct, but the only cost variable is
derived from price and margin, so it fails exclusion by construction, and
Hausman-style instruments are contaminated by exactly the national demand shocks
I'm worried about. The fix is an experiment, not a better estimator - and then
causal ML on top of the randomised variation.

**Do not say.** "Causal forests would solve the endogeneity." "We could use AAC
as an instrument."

---

## Q15. Why not reinforcement learning?

**Rigorous.** RL solves the exploration-exploitation problem when you can
actually explore. Here there is no environment to interact with - the data is a
fixed historical log, so any RL agent would be doing offline RL, which needs
either a reliable simulator or off-policy evaluation with importance weights. The
simulator would be my own fitted demand model, which puts me straight back in the
circularity of Q8: the agent would learn to exploit the model's biases, not the
market's. Off-policy evaluation needs the behaviour policy's propensities, and I
do not know why Dominick's set each price. Beyond the technical objection there
is a business one: a pricing system that explores is a pricing system that
deliberately charges some customers a wrong price to learn, which is a fairness
and trust decision, not a modelling decision, and it needs an owner outside the
data science team. If exploration were authorised, the right first step is a
designed experiment, not a bandit - and only then a contextual bandit with
guardrails.

**Short.** There's no environment to explore - it's a fixed historical log. So
it'd be offline RL, and the simulator would be my own demand model, which means
the agent learns to exploit my model's biases rather than the market. Off-policy
evaluation needs the behaviour propensities and I don't know why Dominick's set
each price. And exploring in pricing means deliberately charging some customers
the wrong price to learn, which isn't a modelling decision.

**Do not say.** "RL is overkill." "We could add a bandit later." (Say why the
data doesn't support it.)

---

## Q16. Why grid search instead of directly optimising a differentiable model?

**Rigorous.** Three reasons. First, the selected model family includes gradient
boosting, whose response surface is a step function - gradients are zero almost
everywhere and undefined on the splits, so a gradient method has nothing to
follow. Second, the feasible set is a small interval, typically ±10% of the
current price with a 5-cent grid, so the whole candidate set is 5-40 points; a
solver's asymptotic advantage is irrelevant and a grid gives me an exact global
optimum over the set the business would accept. Third and most importantly, the
grid makes every constraint check, every rejected candidate and every reason code
explicit and testable - I can assert that a specific candidate was excluded by a
specific constraint, which is what makes `reports/11` possible at all. The cost is
quantisation: the 5-cent grid puts the chosen price a median of 2.5 cents from the
continuous optimum, which I measured rather than ignored. For the
constant-elasticity hybrid I also have the closed form `p* = c·ε/(1+ε)` and use it
as the unconstrained reference in the audit.

**Short.** The tree model's response surface is a step function, so gradients are
useless, and the feasible set is 5-40 candidate prices anyway - a grid gives the
exact optimum over the set the business would accept. Mostly, though, it's
auditability: I can point at a specific candidate and say which constraint
excluded it. The cost is a median 2.5-cent quantisation error, which I measured.

**Do not say.** "Grid search is more robust." "The optimizer finds the global
optimum." (Over the grid, inside the constraints, under the model.)

---

## Q17. You report 4.7 million observations. What does the model actually see?

**Rigorous.** 4,707,776 `UPC x store x week` rows survive the raw audit, and the
chronological split is 3,206,437 train / 715,856 validation / 749,040 test by
week. But scale is not the same as information for the pricing question. The
elasticity funnel is the honest number: of 489 UPCs in the processed data, 372
appear in the training weeks, 272 clear the observation and price-variation
screens, 248 have a negative coefficient, and **239** have a panel-robust
standard error small enough to use. Those 239 cover **64%** of decision contexts,
because the surviving products are the ones sold in many stores over many weeks.
So the correct summary is: 4.7 million rows of forecasting data, and identifying
price variation for 239 products.

**Short.** 4.7 million rows across a 70/15/15 chronological split, but scale
isn't information for the pricing question. Of 489 UPCs, 239 end up with a usable
own-price estimate - and those cover 64% of decision contexts because they're the
ones sold widely. So: 4.7 million rows of forecasting data, identifying price
variation for 239 products.

**Do not say.** "We modelled 4.7 million observations" as if all of them
informed the elasticity.

---

## Q18. What does your risk score actually measure? Is it calibrated?

**Rigorous.** It is explicitly **not** calibrated, and the code and config say so
in those words. It is a transparent rule over four factors that make a
recommendation unreliable in this dataset: number of observations in the series,
number of distinct prices, distance of the proposed price outside the observed
price support, and whether the series is promotion-heavy or falls back to the
pooled elasticity. LOW means plenty of history and an in-support price; HIGH means
thin evidence or extrapolation. It is a heuristic reliability score, not a
probability, and calling it "confidence" would be the mistake. If I had to
calibrate it, the target would be out-of-sample forecast error conditional on the
risk band, and I would fit it as a classifier on realised error rather than assert
thresholds. The thresholds today are chosen so that the risk bands are *stricter*
than the eligibility screen - eligibility answers "can this be modelled", risk
answers "should we act on it".

**Short.** It's a transparent heuristic over four things - series length, price
variation, how far the proposal sits outside observed prices, and promotion
contamination - and it is deliberately not calibrated. Calling it confidence would
be wrong. To calibrate it I'd fit realised out-of-sample error against the bands
rather than assert thresholds.

**Do not say.** "High confidence recommendations." "The risk score is a
probability."

---

## Q19. Why is `shrunk` the default rather than the ML model's own price response?

**Rigorous.** Because the ML response is too steep, and I have out-of-time
evidence, not just a prior. The selected forecaster's implied elasticity via
finite differences is about **-3.10**, against a controlled econometric estimate
of -2.03 and out-of-time observed implied elasticities of -1.86 (non-promotion) to
-2.45. The forecaster is fitted to explain *observed* demand at *observed* prices,
where price moves are bundled with display and feature activity, so it attributes
the whole promotional lift to price. Using it for counterfactuals imports that
bias into every recommendation, and because the profit optimum is
`c·ε/(1+ε)`, a steeper `ε` systematically pushes toward lower prices. The
out-of-time check confirms the ranking: on non-promotion price-change episodes,
shrunk scores WAPE **0.560**, native ML **0.588**, pooled **0.594**, against a
no-price-response null of 0.715. So `shrunk` is the default on evidence, with
`pooled` as the fallback where a product has no usable estimate, and the native ML
response retained as a reported benchmark.

**Short.** The forecaster's implied elasticity is about -3.10 because it's fitted
where price moves are bundled with display and feature, so it credits promotional
lift to price. The controlled estimate is -2.03 and out-of-time episodes imply
-1.86 to -2.45. On unseen price-change episodes without promotions, shrunk beats
native ML and pooled on WAPE. So it's default on evidence, not preference.

**Do not say.** "The hybrid is more accurate." (More accurate at counterfactuals
on these episodes; the forecaster is still the better forecaster at observed
prices.)

---

## Q20. If I gave you two more weeks, what would you fix first?

**Rigorous.** In order, and the order is the point. **(1) Cross-price effects.**
It is the only limitation that could make the recommendations directionally wrong
at category level rather than merely imprecise. I would add category price index
and nearest-substitute price features and estimate a segment-level demand system,
then re-run the whole audit with category profit as the objective. **(2) Make
the guardrail dominance a design decision rather than a finding.** Right now the
±10% cap is an inherited demo number that happens to bind 44% of the time. I
would sweep the cap against the elasticity confidence interval and set it so that
the cap binds only where the elasticity interval is genuinely uninformative -
that turns "the rules dominate" into "the rules dominate exactly where the
evidence is weak", which is defensible rather than accidental. **(3) Calibrate
the risk layer** against realised out-of-sample error instead of asserted
thresholds. What I would *not* do is add models. The project's problem is not a
shortage of methods.

**Short.** Cross-price effects first - it's the only gap that could make the
recommendations directionally wrong at category level, not just imprecise. Then
I'd make the guardrail dominance deliberate: sweep the change cap against the
elasticity interval so the cap binds where the evidence is weak rather than
everywhere. Third, calibrate the risk layer on realised error. I wouldn't add
more models - that isn't the bottleneck.

**Do not say.** "Add deep learning." "Move it to the cloud." "Add reinforcement
learning."

---

## The three things to volunteer before you are asked

1. **The guardrails, not the model, set the magnitude of most recommendations**
   (`reports/11`, `reports/12`, `reports/13`). Saying it first turns your
   weakest point into evidence that you audit your own work.
2. **Nothing here is causal, and the profit numbers are self-scored**
   (`docs/CAUSAL_LIMITATIONS.md`, Q8).
3. **Own-price elasticities only - no substitution** (Q12).

## The three numbers to have exactly right

* Test WAPE **0.4565** on 749,040 rows, best naive baseline **0.7326**.
* Pooled controlled elasticity **-2.03**, two-way clustered 95% CI
  **[-2.24, -1.84]**.
* **0** HIGH-risk contexts actionable under both default policy profiles.
