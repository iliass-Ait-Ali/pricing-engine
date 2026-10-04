# Executive summary

*One page for a commercial or finance lead. Every number comes from a script
in this repository (source in brackets). Every dollar figure is a
**model-internal estimate**: no price recommended here was ever charged, so
none of it is a measured or causal result.*

## The question

A grocery chain (Dominick's Finer Foods, 93 stores, cereal category, 1989-1997)
asks: **for each product in each store this week, what price should we charge
to earn more gross profit, without taking risks we cannot defend?**

## The answer

**Pilot the engine's recommendations in a randomised set of stores before any
rollout.** The recommendations are plausibly worth **$234k to $538k a year** in
category gross profit, **+4.1% to +9.4%** of today's $5.70M, but the width of
that range is the honest uncertainty, and only a pilot can narrow it.
[`reports/23_BUSINESS_VALUE.md`]

## Why

1. **Situation.** Prices were set store by store with no consistent view of
   price sensitivity. The engine estimates how demand responds to price for
   each product, then recommends a price inside business guardrails (maximum
   weekly change, minimum margin, stay near prices customers have seen).
2. **Complication.** The data are historical, not experimental: the retailer
   chose its prices, so no model can prove what a new price would do. And the
   guardrails, not the model, set the size of most changes: only **2.9%** of
   final recommendations come from the model's own best price.
   [`reports/11_CONSTRAINT_ATTRIBUTION.md`]
3. **Resolution.** Treat the engine as **rule-bounded pricing with a learned
   direction**: the model says which way to move and how confident to be; the
   rules say how far. Measure the value with a store-randomised pilot.

## What the engine recommends (latest week, standard policy)

| decision | share of products x stores |
| --- | ---: |
| change the price (actionable) | 58.9% |
| a person must approve first (thin or risky evidence) | 10.4% |
| keep today's price | 30.8% |

[`reports/07_RECOMMENDATION_SUMMARY.md`] Most actionable changes are increases.
Products with thin or risky evidence are never changed automatically.

## The decision only a category manager can make

**Traffic drivers** (43 high-volume products almost every store carries, 43.5%
of revenue) get a price change in 78.0% of cases, and 89.1% of those are
increases. These are the prices shoppers judge the store by. Capping their
increases at +3% gives up 22% of the estimated gain.
[`reports/24_PRODUCT_ROLES.md`]

## Risks and how they are handled

| risk | mitigation in place |
| --- | --- |
| The model is wrong about price sensitivity | Value shown as a range across every evidenced elasticity; pilot before rollout |
| Large or risky price moves | Weekly change cap, margin floor, no pricing far outside observed prices; HIGH-risk cases go to a person |
| Losing price image on key products | Product-role view above; decision left to the category manager |
| Customers substitute between brands | Not modelled yet. The pilot randomises whole stores so substitution is inside the measurement |
| Unfair pricing | Prices vary only by product, store and week, never by customer (`docs/RESPONSIBLE_PRICING.md`) |

## Next steps

1. **Decide the traffic-driver policy** (cap or no cap) - 1 week.
2. **Run the pilot** - stratified store randomisation, 12 weeks plus a 4-week
   pre-period, gross profit per store-week as the primary metric
   (`docs/PRICING_EXPERIMENT.md`).
3. **Staff the review queue** - about one decision in ten needs a person; the
   dashboard's *Review queue* page is the tool.
4. **Roll out only if** the pilot's measured gain clears the low end of the
   range above.
