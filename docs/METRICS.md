# Metrics

Every metric name used in this repository, its formula, and why it was chosen.

## Business / economic metrics (observed)

| metric | formula | note |
| --- | --- | --- |
| `effective_unit_price` | `price / qty` | the decision variable |
| `revenue` | `effective_unit_price * move` | historical observed |
| `gross_margin_rate` | `profit / 100` | accounting margin, AAC-based |
| `estimated_unit_aac` | `effective_unit_price * (1 - gross_margin_rate)` | implied unit cost, **not** replacement cost |
| `gross_profit` | `revenue * gross_margin_rate` = `(p - aac) * units` | historical observed |
| `price_change_pct` | `p_new / p_old - 1` | NaN if `p_old = 0` |

## Forecast-accuracy metrics

| metric | formula | why |
| --- | --- | --- |
| **WAPE** (headline) | `sum(|y - yhat|) / sum(|y|)` | scale-free, defined for small counts, weights high-volume series correctly |
| MAE | `mean(|y - yhat|)` | interpretable in units |
| RMSE | `sqrt(mean((y - yhat)^2))` | exposes large misses (promotion spikes) |
| bias | `mean(yhat - y)` | systematic over/under-forecast |
| sMAPE | `mean(|y-yhat| / ((|y|+|yhat|)/2))` | secondary, symmetric |

**MAPE is not a headline metric here.** Weekly unit sales are small counts;
MAPE explodes near zero and would reward under-forecasting. It is not reported.

Reference values from the actual run (`reports/04_MODEL_COMPARISON.md`):

| model | validation WAPE | test WAPE |
| --- | --- | --- |
| last-week naive | 0.8007 | - |
| rolling-mean(4) naive | 0.7471 | - |
| series historical mean | 0.7607 | - |
| seasonal naive (52w) | 0.7920 | - |
| ridge log-log (selected) | **0.4135** | **0.4565** |
| HGB Poisson | 0.4226 | - |

## Price-response metrics

| metric | definition |
| --- | --- |
| arc (midpoint) elasticity | `((Q2-Q1)/mean(Q)) / ((P2-P1)/mean(P))`; NaN when undefined |
| log-log elasticity | coefficient on `log(price)` in `log(units) ~ log(price) + controls` |
| **implied model elasticity** | central finite difference on the fitted model: recompute every price-dependent feature at `(1 +/- 2%) * price` and convert the predicted-demand change to `dlogQ/dlogP` |

The third one is the operationally important number: it is what the optimizer
actually exploits, and it is comparable across model families.

## Recommendation metrics

| metric | definition |
| --- | --- |
| `predicted_units_current/recommended` | model prediction at each price, all other features fixed |
| `expected_revenue_*` | `price * predicted_units` |
| `expected_gross_profit_*` | `(price - decision_time_cost) * predicted_units`, cost held fixed |
| `model_internal_estimated_profit_uplift_pct` | `GP(recommended)/GP(current) - 1`, where both sides are scored by the same fitted price-response model - an internal simulation, never an unbiased policy value |
| `elasticity_used` / `elasticity_source` | the elasticity applied and where it came from (`shrunk_product` or `pooled_fallback`) |
| `decision` | `RECOMMEND_CHANGE` / `KEEP_CURRENT` / `REVIEW_REQUIRED` |
| `actionable` | true only for `RECOMMEND_CHANGE` |
| `share_change_price` | fraction of contexts where the engine moves the price |
| `risk_level` | HIGH / MEDIUM / LOW heuristic, **not** a calibrated confidence |

## Monitoring metrics

| metric | interpretation limit |
| --- | --- |
| PSI | 0.10 / 0.25 bands are industry folklore, not statistical thresholds; depends on bin count and sample size |
| KS statistic | reported as a statistic only; with 10^5+ rows every KS *test* rejects, so p-values are meaningless here |
| realised WAPE by period | the only metric that should gate a model |

## Naming discipline

| allowed | forbidden without experimental evidence |
| --- | --- |
| historical observed revenue / gross margin | actual uplift |
| predicted demand | causal profit uplift |
| model-estimated counterfactual revenue | proven optimal price |
| model-internal estimated gross-profit uplift | scientifically validated causal engine |
| offline policy simulation | production ready |
| model-internal estimated uplift | measured uplift |
| observational price-response estimate | |

## Elasticity metrics (Phase L)

| metric | definition |
| --- | --- |
| pooled controlled elasticity | coefficient on `log(price)` with UPC x store fixed effects absorbed plus promotion, seasonality and trend controls, training weeks only |
| per-UPC elasticity | same specification fitted within one product, store effects absorbed |
| `std_error`, `t_stat`, `ci_low/high` | HC1-robust precision of the per-UPC coefficient |
| shrinkage weight `w_i` | `tau^2 / (tau^2 + se_i^2)` - how much of its own coefficient a product keeps |
| `tau^2` | `var(estimates) - mean(se^2)`, floored at 0: the estimated dispersion of *true* product elasticities |
| `elasticity_final` | `w_i * epsilon_i + (1 - w_i) * epsilon_pooled`, clipped to the configured plausible band |

Rejection reasons recorded per product: `insufficient_observations_or_price_variation`,
`standard_error_too_large`, `wrong_sign` (a positive coefficient is evidence of
endogeneity, not of upward-sloping demand).
