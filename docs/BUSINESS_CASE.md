# Business case

## The decision this system supports

> Given a product, a store, a week, the current commercial context and a
> feasible price range, what price should we recommend to maximise expected
> gross profit (or revenue) while respecting business and scientific
> guardrails?

Category managers in grocery retail make thousands of these decisions weekly,
mostly from experience, cost-plus rules and competitor checks. The engine does
not replace that judgement; it gives it a quantitative starting point, an
explicit set of constraints, and an audit trail.

## Scale of the problem in this dataset

| | |
| --- | --- |
| observed revenue (Cereals, 93 stores, 1989-1997) | $262.0 M |
| observed gross profit | $40.1 M (15.30% of revenue) |
| decisions in scope | 36,443 UPC x store series, 4.7 M store-week observations |
| series with enough price variation to model | 19,707 (54.1%) |

Two arithmetic illustrations of that base, stated in their units because they
are not the same number: a **1% relative** increase in the gross-profit total
is about **$0.4 M** over the panel (`0.01 x $40,091,143`), while a **+1
percentage point** increase in the gross-margin *rate* on the observed revenue
is about **$2.6 M** (`0.01 x $262,008,582`). Both are illustrations of scale,
not estimates of what this engine would earn - which is why pricing work is
funded, and also why overstating the number is so tempting.

## What the engine actually produced

Batch run, decision week 399 (1997-05-01), standard policy profile, 3,000
contexts, `shrunk` elasticity price response:

| outcome | value |
| --- | --- |
| actionable price changes (`RECOMMEND_CHANGE`) | 58.9% |
| escalated to a human (`REVIEW_REQUIRED`) | 10.4% |
| keep current price | 30.8% |
| median absolute change (actionable) | 8.4% |
| share of changes that are increases | 91% |
| **model-internal estimated portfolio gross-profit uplift** | **+8.3%** |
| risk distribution | LOW 1,175 / MEDIUM 786 / HIGH 1,039 |
| HIGH-risk contexts that auto-changed a price | **0** |
| final recommendations set by an interior model optimum | **2.9%** (`reports/11`) |

Offline policy comparison over weeks 390-399 (`reports/06_BACKTEST.md`):

| policy | model-internal estimated gross profit vs historical | keeps price unchanged |
| --- | --- | --- |
| Historical (reference) | 0.00% | 100% |
| Cost-plus (20% margin) | +2.3% | 0% |
| Constant-elasticity markup | +13.7% | 6.4% |
| **ML optimizer (this engine)** | **+10.5%** | 36.9% |

## How to read those numbers honestly

**They are model-internal estimates, not realised results.** Demand at prices
that were never charged was never observed, and the same fitted price-response
model both proposes and scores the prices - an internal simulation, not an
unbiased policy value.

Worse for the headline: the number depends on which price-response assumption
is used. On 300 matched contexts the native ML response and the pooled
elasticity agree on the decision state only **79.7%** of the time, and their
median internal uplift differs by roughly a factor of two (+8.3% vs +17.6%).
Under wide what-if guardrails the candidate profit-maximising price (under model assumptions) moves a
median of **21.5%**
across plausible elasticities (-1.5 to -3.1).

Worse still, the Phase M constraint attribution (`reports/11`) shows that only
**2.9%** of final recommendations are set by an interior optimum of the
estimated price response; 57.1% sit on a guardrail corner and a rule that never
looks at demand matches the engine's price within one 5-cent step **82.5%** of
the time (`reports/12`). So the uplift number is not only self-scored, it is
mostly a property of the change cap.

The defensible business claim is therefore not "+8.3% profit". It is:

1. There is **real, exploitable price variation** in about half of the series -
   the rest cannot support a price recommendation at all, and the engine says
   so instead of guessing.
2. The engine **concentrates attention**: 58.9% of contexts get an actionable
   proposal and 10.4% are escalated for review, each with a ranked expected
   impact, so a category manager reviews the top decisions rather than all
   36,443.
3. It **encodes guardrails explicitly** - margin floors, maximum change,
   historical support, materiality - and reports which one bound each decision.
4. It produces an **auditable, reviewable artefact** per decision.

## Where the value would actually be proven

`docs/PRICING_EXPERIMENT.md`: a store-level randomised test with gross profit
per store-week as the primary metric, guardrails on complaints and extreme
changes, and a permanent holdout after rollout. Until that runs, the honest
framing to a stakeholder is:

> "This narrows thousands of pricing decisions to a few hundred worth reviewing,
> with the reasoning attached. The profit figure attached to them is a model
> estimate, and we should test it on a few dozen stores before believing it."

## Cost of being wrong

| failure | consequence |
| --- | --- |
| over-estimated elasticity | unnecessary price cuts, margin given away |
| under-estimated elasticity | price increases that lose volume and share |
| unstable recommendations | shelf re-tagging cost, customer distrust |
| extrapolated prices | decisions in a region where the model has no data |
| unnoticed promotion confound | "price effect" that was really a display effect |

Each of these has a corresponding control in the engine (materiality threshold,
change caps, stability measurement, extrapolation guardrail, promotion
reporting) - which is the real business argument for building it this way
rather than shipping a bare model with an impressive number attached.
