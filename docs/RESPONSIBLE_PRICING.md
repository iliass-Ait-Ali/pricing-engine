# Responsible pricing

Price optimization is one of the few analytics applications whose output lands
directly on a customer's receipt. That deserves explicit boundaries.

## What this engine optimises over

**Product, store and time context only**: UPC, store, week, seasonality,
promotion state, recent demand and cost. Nothing else exists in the data.

## What it must never optimise over

* individual customers or households;
* any protected or sensitive attribute (race, ethnicity, religion, disability,
  age, gender, sexual orientation, immigration status);
* proxies for those attributes - neighbourhood income, ZIP-code demographics,
  device type, browsing history, willingness-to-pay scores inferred from
  personal data.

Store identity deserves care for exactly this reason: a store is a place, and
places correlate with demographics. Dominick's genuinely used price zones, and
this project uses store as a decision dimension because pricing was zone-based.
But zone pricing is precisely where "price by location" can shade into "charge
more where a protected group shops". In a real deployment this would require:

* an explicit review of price differences across stores against demographic
  data,
* a cap on cross-store dispersion for essential goods,
* legal sign-off in every jurisdiction of operation.

## Price gouging

The engine's guardrails are demo-grade, not a gouging policy. A real system
needs, at minimum:

* hard caps on increases for staple and essential goods;
* an emergency mode that freezes or reduces prices during disasters, supply
  shocks or declared emergencies (many jurisdictions make this a legal
  requirement, not a courtesy);
* category-level rules: cereal is discretionary, infant formula is not, and no
  model should be allowed to treat them identically.

What exists here: maximum change per decision, a minimum-margin floor, an
extrapolation guardrail keeping prices inside historically observed levels, and
a materiality threshold that leaves prices alone when the estimated gain is
small. These *limit* movement; they do not encode an ethical policy.

## Personalised pricing

Not implemented, and not implementable on this data - there is no customer
dimension. That is stated positively because "we could not" is a weaker
guarantee than "we would not": personalised pricing based on inferred ability
to pay raises discrimination, transparency and trust problems that a portfolio
project has no business hand-waving through.

## Customer trust and transparency

* Frequent price flapping erodes trust as much as price level does. The
  backtest measures recommendation stability precisely because a model that
  re-prices every week is operationally and reputationally expensive.
* Recommendations are auditable: every one is logged with its inputs,
  constraints, model version, risk level and reason codes.
* The engine can always answer "why this price?" in plain reason codes rather
  than "the model said so".

## Legal and regulatory obligations

Not legal advice, but the obligations a real deployment must map:

* unfair or deceptive pricing practices rules;
* price-gouging statutes (often state or emergency-triggered);
* unit-pricing and price-accuracy display requirements;
* competition law - in particular, **never** use a shared model or signalling
  mechanism to coordinate prices with competitors;
* consumer-protection rules on advertised promotions.

## Human in the loop

The recommendation lifecycle is
`GENERATED -> REVIEWED -> APPROVED / REJECTED -> PUBLISHED`. This demo stops at
GENERATED and logs everything. No recommendation reaches a shelf without a
person, and the system is designed so that the person has the information to
say no: risk level, binding constraints, distance from historical prices, and
the fact that the uplift is model-estimated rather than measured.

## Honest summary

The scientific limitations are ethical limitations too. Because the price
effects here are observational rather than causal, acting on them at scale
without an experiment would impose real price changes on real customers on the
strength of a correlation. `docs/PRICING_EXPERIMENT.md` describes what it would
take to earn that right.
