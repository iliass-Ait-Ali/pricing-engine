# Pricing experiment design (hypothetical)

This document designs the experiment that would be needed to turn this engine's
**model-estimated** uplift into a **measured** one. No experiment was run, and
no results are invented here.

## Hypothesis

Applying the constrained optimizer's recommended prices (standard policy
profile, gross-profit objective) to eligible UPC x store series increases gross
profit per store-week relative to the retailer's current pricing, without
material harm to units or customer complaints.

## Randomisation unit

**Store x category-block x week** - i.e. randomise whole stores into treatment
and control for a defined block of Cereals UPCs.

Why not randomise individual UPCs within a store: shoppers substitute heavily
within cereal. If Kellogg's Corn Flakes is treated and Post Toasties is not,
the "control" product absorbs the treated product's lost or gained volume, and
the estimated effect is contaminated by cannibalisation.

Why not randomise weeks within a store (switchback): promotion calendars,
seasonality and shopper stock-piling create carry-over across weeks; a
switchback would need a long washout that eats the sample.

Trade-off accepted: store-level randomisation means fewer independent units and
requires clustered inference.

## Design

* Two arms: **control** (current pricing process) and **treatment** (optimizer
  recommendations, human-reviewed, standard profile).
* Stratified assignment on pre-period gross profit, store size and price zone,
  to reduce imbalance with a small number of clusters.
* Duration: **12 weeks** minimum - long enough to cover a promotion cycle and
  post-promotion dips, short enough to limit drift. Plus a 4-week pre-period
  used only for stratification and pre-trend checks.
* Freeze the recommendation policy for the whole test; no mid-flight tuning.

## Primary metric

**Gross profit per store-week for the treated category block.**

Chosen because it is the objective the engine optimises, it is measurable
weekly, and it cannot be gamed by shifting volume between prices the way
revenue or units alone can.

## Secondary metrics

* revenue per store-week
* unit sales per store-week
* average selling price
* realised gross margin rate
* category share of store sales (to detect substitution out of the category)

## Guardrail metrics

* customer complaints per 1,000 transactions
* share of items with a price increase above a defined threshold
* out-of-stock rate for treated items (a price cut that empties the shelf is
  not a win)
* returns / refunds where applicable
* basket size and store traffic (to detect store-level spillover)
* any legally sensitive category flags

Guardrails get **pre-registered stopping rules**: breach means the arm is
paused, not explained away.

## Sample size and power (concept)

For a clustered design the usable sample is the number of **stores**, not
store-weeks. With `k` stores per arm, `T` weeks, within-store weekly gross
profit standard deviation `sigma`, and intra-cluster correlation `rho`, the
effective standard error scales roughly as

```text
SE  ~  sigma * sqrt( (1 + (T - 1) * rho) / (k * T) )
```

Detecting a small relative effect (say 2-3% of gross profit) in a category with
high weekly volatility typically requires **tens of stores per arm and a
multi-week horizon**. The honest planning step is to compute `sigma` and `rho`
from the pre-period of the actual chain and solve for `k` - not to assert a
number here. Note this project's own demand model has a weekly WAPE around
0.46; category-level aggregation reduces noise, but the design must be powered
against measured variance, not hope.

## Analysis plan (pre-registered)

* Primary estimator: difference-in-differences on store-week gross profit with
  store and week fixed effects, standard errors **clustered by store**.
* Pre-trend check on the pre-period; report it whether or not it is flattering.
* One primary metric, one primary test. Secondary metrics are reported with a
  multiplicity correction (Holm or Benjamini-Hochberg) and labelled
  exploratory.
* Analysis code written and frozen before unblinding.

## Threats and how the design handles them

| threat | handling |
| --- | --- |
| **spillover** between stores (cross-shopping) | randomise at store level, prefer geographically separated stores, measure traffic |
| **within-category substitution** | treat whole category blocks, not single UPCs |
| **seasonality** | week fixed effects; run across a full promotion cycle |
| **contamination** from chain-wide promotions | log promotion calendars; exclude weeks with chain-wide events or model them explicitly |
| **novelty effects** | discard the first week from the primary window (pre-registered) |
| **competitor response** | monitor local competitor prices where available; a 12-week window limits exposure |
| **implementation failure** | audit that treated shelves actually carry the recommended price; an experiment measures what was executed, not what was recommended |

## Rollout safety

1. Start with a **small pilot** (few stores, tight conservative profile).
2. Expand only after guardrails hold and the primary estimate is stable.
3. Keep a permanent holdout so the effect can be re-measured after full
   rollout - models decay, and without a holdout nobody can tell.
4. Keep the human review step throughout.

## What would be learned

Only with this experiment could the project say "prices set by this engine
**caused** a change in gross profit of X%". Until then, everything the engine
reports is a model-estimated counterfactual, and it is labelled as such
everywhere in the code, reports and dashboard.
