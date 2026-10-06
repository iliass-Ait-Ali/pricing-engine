# Interview drill

A practice sheet for explaining this project without notes. It adds no new
claims: every number below is in a committed file, named next to it. For full
model answers to the hardest questions, use
[`INTERVIEW_RED_TEAM.md`](INTERVIEW_RED_TEAM.md) (20 questions) and
[`INTERVIEW_GUIDE.md`](INTERVIEW_GUIDE.md); for the pitch and the mock case, use
[`INTERVIEW_PITCH.md`](INTERVIEW_PITCH.md).

## Why this sheet exists

This repository was built with AI coding assistants (see `docs/process/`). A
good interviewer will test whether you own the reasoning, not whether the code
runs. The test is simple: can you open a file, say what it does and why, and
survive three follow-up questions?

## How to use it

* **Say the answers aloud, standing up, before you read the model answer.** Reading
  is not practice.
* **Open the file named on each page** and find the lines the answer points to.
  If you cannot find them in a minute, that module is not yet yours.
* **One module a day for seven days, about 20 minutes each.** Then two timed run-throughs
  of the mock case in `INTERVIEW_PITCH.md` section 4 (10 minutes each), and one
  pass through the self-test at the end.
* Where you are unsure, say so in the interview and say what you would check. The
  project's own rule is "claim only what you can explain".

---

## Module 1: `data/`

**One sentence.** Turns the raw Kilts Center files into one table, UPC x store x
week, and counts every row it removes.

**Open:** `src/pricing_engine/data/cleaning.py` (`build_canonical`), then
`validator.py`.

**Number to know:** 6,602,582 raw rows become 4,707,776 canonical rows (28.7%
removed), for 489 products in 93 stores (`artifacts/metrics/second_category.json`,
cereal side).

1. **Why drop rows at all, and how do you know it did not bias anything?**
   Every exclusion is a named rule with a count (`ExclusionLog`), so the table is
   audited rather than trusted. The price-zero rows have their own report
   (`reports/09_ZERO_PRICE_AUDIT.md`) that compares them with the kept rows.
2. **The data has promotion codes. Can you trust them?**
   No. The columns are named `recorded_*` on purpose: a missing code is not proof
   that no promotion ran. The ground-truth study (`reports/25`) shows what
   unrecorded promotions do to an elasticity estimate.
3. **What did the second category teach you about the loader?**
   The crackers file has 200 blank export rows that the cereal file lacks, which
   broke the loader. `drop_blank_key_rows` removes and counts them, and the cereal
   table came out byte-identical (`CHANGELOG.md`, 1.2.0).

## Module 2: `features/`

**One sentence.** Builds model inputs under one rule: use only what an analyst
would know when setting next week's price.

**Open:** `src/pricing_engine/features/build.py` (the module docstring is the rule),
`tests/test_features.py`, `tests/test_cost_leakage.py`.

**Number to know:** none, and that is the point: this module is about a contract,
not a metric. Know the rule word for word.

1. **What is leakage, and where could it have happened here?**
   Using the target week's own outcome (units, revenue, that week's margin or its
   implied cost) as an input. The docstring lists what counts as outcome
   information. A planned promotion is allowed because a Bonus Buy is scheduled,
   not discovered.
2. **Is using average acquisition cost (AAC) as the cost leakage?**
   Only if it is the target week's. The engine uses a decision-time cost, and
   `reports/10_COST_LEAKAGE_AUDIT.md` plus `tests/test_cost_leakage.py` check it.
   Red team Q6 has the full answer.
3. **How would you show there is none?**
   You cannot prove a negative, so `tests/test_features.py` poisons one week's
   units, revenue and gross profit with a huge value and checks that none of that
   week's own features move, while next week's lag does (so the test has teeth).
   Say that, then say what it cannot cover: a leak through a column nobody thought
   to poison.

## Module 3: `economics/`

**One sentence.** Estimates how demand responds to price, separately from
forecasting, and says how uncertain each estimate is.

**Open:** `src/pricing_engine/economics/elasticity_store.py` (pooled, fixed-effects
and per-product estimates, then shrinkage), `inference.py` (clustered standard
errors).

**Numbers to know:** pooled controlled elasticity **-2.03** (training weeks); the
naive estimate is **-0.35**; **239 of 372** products have a usable own estimate;
mean shrinkage weight **0.78** after the standard-error correction (it was 0.955
before). Sources: `artifacts/metrics/second_category.json`, `reports/14`, `reports/15`.

1. **Why trust your elasticity at all?**
   Treat it as an association, not a measured effect: the retailer chose its prices
   (`docs/CAUSAL_LIMITATIONS.md`). Controls (store and product fixed effects,
   promotion, season, trend) and an out-of-time check (`reports/16`) make it
   defensible, not causal. Red team Q1 and Q3.
2. **Why did the naive number come out at -0.35 and the controlled one at -2.03?**
   The naive pooled slope mixes price levels across products (premium and bulk
   brands sell differently) with each product's own response to price. Product and
   product-by-store fixed effects remove that mix and move the estimate a lot
   (`reports/03_ELASTICITY_ANALYSIS.md`, section 2). It still is not a measured
   effect: unrecorded promotions and price set on unseen demand remain.
3. **What does the shrinkage do, and is a weight of 0.78 doing anything?**
   It pulls noisy per-product estimates toward the category mean in proportion to
   their noise. At 0.78 most of each product's own estimate survives, but thin
   series are pulled hard. The weight moved from 0.955 once the standard errors
   were clustered by store, product and week (they had been too small by a median
   factor of 3.8). Red team Q9 and Q10.

## Module 4: `models/`

**One sentence.** A demand forecaster for the level, plus a price response taken
from the econometric estimate rather than from the forecaster.

**Open:** `src/pricing_engine/models/hybrid.py`, `demand_model.py`.

**Numbers to know:** test WAPE **0.4565** for the selected ridge log-log model; its
own implied elasticity is **-3.10**, steeper than the controlled estimate
(-1.91 to -2.42), which is why the hybrid exists.

The formula to say aloud: `Q(p) = Q_hat(p0) * (p / p0) ** epsilon`.

1. **A WAPE of 0.46 sounds bad. Is it?**
   It is measured at the grain the engine prices at (one product, one store, one
   week), where unit counts are small and heavy-tailed. The comparison that
   matters is the best naive baseline, 0.7326 against 0.4565 on 749,040 held-out
   rows, a 38% reduction (red team Q4). Aggregating to category or month would
   look better and mean less.
2. **Why did a linear model beat gradient boosting?**
   Demand is multiplicative, and a log-log ridge encodes that directly. The bigger
   reason is the forward-in-time split: trees cannot extrapolate a time trend past
   their last training value. So it says which bias survives a temporal shift, not
   that ridge beats boosting in general (red team Q5).
3. **Why not use the ML model's own price response?**
   Its implied elasticity (-3.10) is steeper than the controlled estimates, so
   using it would build that bias into every recommendation (red team Q19).

## Module 5: `optimization/`

**One sentence.** Finds the best price inside a feasible interval, explains which
rule bound it, and decides whether a person must approve.

**Open:** `constraints.py` (the interval), `optimizer.py` (`optimize_price`,
`DecisionState`, `ReasonCode`), `attribution.py` (what actually set the price),
`risk.py` (the heuristic risk score).

**Numbers to know:** only **2.9%** of final prices are the model's own best price;
a no-model rule lands within one 5-cent step of the engine **82.5%** of the time;
**58.9% / 10.4% / 30.8%** change / review / keep; **0** HIGH-risk contexts changed
automatically; **17** reason codes.

1. **If the guardrails set most prices, why do you need a model?**
   For direction and confidence, not size. Say it before you are asked: the
   guardrails set the magnitude, the model says which way and how sure
   (`reports/11`, red team Q2). The same holds in a second category (`reports/27`).
2. **What does the risk score mean? Is it calibrated?**
   It is a transparent rule score, not a probability. Checked against out-of-time
   error it separates error only weakly, and extrapolation carries most of the
   signal (`reports/26`, red team Q18).
3. **What is circular about the uplift number?**
   The model that picks a price also scores it, so every gain is model-internal.
   That is why the value is a range (+4.1% to +9.4%) and why the answer to "is it
   real?" is a store-randomised pilot (`docs/PRICING_EXPERIMENT.md`, red team Q8).

### The derivation to do by hand

With `Q(p) = Q0 * (p / p0) ** e` and profit `(p - c) * Q(p)`:

1. Differentiate: `Q(p) + (p - c) * e * Q(p) / p = 0`.
2. `Q(p)` is positive, so divide it out and multiply by `p`: `p + e * (p - c) = 0`.
3. Solve: `p * (1 + e) = c * e`, so **`p* = c * e / (1 + e)`**, valid for `e < -1`.

`Q0 = Q_hat(p0)` multiplies the whole profit curve by a positive constant, so it
drops out of the argmax: the forecaster sets the *size* of profit, not the best
price. As an illustration only, with `e = -2.03` and a cost of `$2.00`,
`p* = 2.00 * -2.03 / -1.03`, about `$3.94`: far above a typical shelf price, which
is why the guardrails (a weekly change cap, a stay-near-observed-prices rule), not
this formula, set most real moves. Compare with `analytic_unconstrained_optimum`
in `attribution.py`.

The follow-up to expect: *what if demand is inelastic, `e` between -1 and 0?*
Then profit rises without bound in price and the formula has no answer. The code
returns infinity (`analytic_unconstrained_optimum`), which is why an inelastic
estimate must never be allowed to price on its own and why the guardrails and the
shrinkage matter.

## Module 6: `serving.py`

**One sentence.** One state object that the API, the dashboard and the copilot all
use, so they cannot disagree.

**Open:** `src/pricing_engine/serving.py` (`AppState`, `build_state`), then
`api/routes/pricing.py` (thin adapters).

**Number to know:** it serves the most recent **8** weeks (`SERVING_WEEKS`),
because a decision context needs its lag history.

1. **How do you keep three front ends consistent?**
   They call the same `AppState` methods. The copilot's tools wrap them, so a
   recommendation from the chat equals the API's.
2. **What happens for a product that is not served?**
   `ContextNotFound`: the API returns an error, and the copilot's tool returns it as
   data so the model can explain it.
3. **What would you add before this is production?**
   Authentication, rate limiting, load testing, monitoring on live data, and a
   model registry (`KNOWN_LIMITATIONS.md`). Say that it is a portfolio demo.

## Module 7: `copilot/`

**One sentence.** A language model that routes questions to engine tools and
explains the results, with a deterministic guard on every answer.

**Open:** `tools.py` (7 tools, `resolve_upc`), `guard.py`, `agent.py`;
`docs/COPILOT_CARD.md`; `evals/copilot/`.

**Numbers to know:** first live run **8 of 20**, after fixes **17 of 20** on the
same questions (a development figure, not a held-out score); **0** ungrounded
numbers shown in any run; **3** cases still fail.

1. **Why can it not invent a number?**
   Every number in an answer must equal a tool result, a tool argument or a number
   from the question, at the precision stated. One rewrite is allowed, then a
   template built from the tool results.
2. **What does the guard miss?**
   A real number with the wrong label or unit. A live answer said "$2.15 per unit"
   for a weekly gross profit and passed. The guard checks where a number comes
   from, not what the sentence says it is.
3. **Is 17 of 20 a good score?**
   It is a development figure from a small local model on questions I used while
   fixing it, so it says the fixes worked on those cases, not how it would do on
   new ones. The first run's 8 of 20 is published beside it.

---

## The three hardest questions

**"This was built with AI. How do I know you understand it?"**
Do not get defensive. Say what you decided and can defend: the rule that features
may not use outcome information, the choice to estimate price response separately
from forecasting, the finding that guardrails set most prices, and the decision to
publish the first copilot run at 8 of 20. Then offer to walk through any module and
open the file.

**"Why should I believe an uplift the model scored itself?"**
You should not, and the project says so on every page. The value is a range across
the elasticities the evidence supports (+4.1% to +9.4% in cereal), and the claim is
"worth piloting", not "worth this much". In crackers the low end of the range
reaches -0.1%, which is reported rather than hidden.

**"Your copilot passed 17 of 20 on questions you tuned it on."**
Agree first. It is a development figure, the documents say so, and the first run is
published at 8 of 20. What I would do next: write new questions before looking at
any answers, and run a larger model. The part that does not depend on the score is
the guard's invariant: no answer shown contained a number the engine did not return.

## Numbers to have cold

| number | meaning | where it comes from |
| --- | --- | --- |
| 2.9% | final prices that are the model's own best price | `reports/11`, `docs/EXECUTIVE_SUMMARY.md` |
| 82.5% | a no-model rule lands within one step of the engine | `reports/12` |
| +4.1% to +9.4% | value range, cereal, model-internal | `reports/23` |
| 10.4% | decisions that need a person | `reports/07`, `reports/19` |
| 22% | gain given up by capping traffic drivers at +3% | `reports/24` |
| -2.03 | pooled controlled elasticity (training weeks) | `reports/15`, `artifacts/metrics/elasticity_inference.json` |
| 0.4565 | test WAPE | `reports/04` |
| 7 of 8 | findings that replicate in crackers | `reports/27` |
| 8 of 20, then 17 of 20 | copilot, first run and after fixes, same questions | `evals/copilot/` |

## Ten-minute self-test

Answer each in under 45 seconds, out loud. Answers are the pages above.

1. Say the rule that decides whether a feature is allowed.
2. Why is the naive elasticity -0.35 and the controlled one -2.03?
3. Write `Q(p)` and say why the ML model's own elasticity is not used.
4. Derive `p* = c * e / (1 + e)` and say why `Q_hat(p0)` drops out.
5. Which share of prices comes from the model's own optimum, and what sets the rest?
6. Is the risk score a probability? What did the check against out-of-time error show?
7. What does "model-internal estimated" mean, and why is every uplift labelled so?
8. What would a randomised pilot measure that this cannot?
9. What does the copilot guard check, and what can it not check?
10. What did crackers show that cereal did not?

## What not to say

* "The model finds the optimal price." (The guardrails set most of the move.)
* "It increases profit." (Say "estimated to", and "model-internal".)
* "The elasticity is X." (Say "the estimate is X, as an association".)
* "The copilot is 85% accurate." (It passed 17 of 20 on a development set.)
* "It is production ready." (It is a portfolio demo with documented limits.)
