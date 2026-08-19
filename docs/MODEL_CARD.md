# Model card - Cereals weekly demand model

All figures come from the run recorded in `artifacts/models/demand_model_metadata.json`
and `reports/04_MODEL_COMPARISON.md`.

## Overview

| field | value |
| --- | --- |
| name | M1 ridge log-log |
| kind | `ridge_loglog` - Ridge regression on `log1p(units)` with one-hot store/commodity, median imputation and standardisation |
| version | `ridge_loglog-20260818-132847` |
| target | `move` (weekly units sold per UPC x store) |
| training rows | 3,206,437 |
| features | 26 model-matrix columns: 24 numeric + 2 categorical (`store`, `com_code`). The linear model drops the unbounded `time_index` trend; see `docs/FEATURE_AVAILABILITY.md` |
| seed | 42 |
| artifact | `artifacts/models/demand_model.joblib` |

Selected over a Poisson HistGradientBoosting model (validation WAPE 0.4135 vs
0.4226) and four price-blind baselines (0.747-0.801).

## Intended use

* Estimating weekly unit demand for a Cereals UPC in a Dominick's store, given
  a candidate price and decision-time context.
* Powering counterfactual price simulation and constrained price optimization
  **as a decision-support tool with a human in the loop**.
* Teaching / portfolio demonstration of pricing analytics on real retail data.

## Out-of-scope use

* Any claim of causal price effect or realised profit uplift.
* Automated price setting without human review.
* Personalised or customer-level pricing - the data have no customer dimension,
  and `docs/RESPONSIBLE_PRICING.md` explains why this is a deliberate boundary.
* Categories, retailers or eras other than Dominick's Cereals 1989-1997.
* Prices far outside a series' observed support (blocked by the extrapolation
  guardrail).

## Training data

| | |
| --- | --- |
| source | Dominick's Finer Foods store-level scanner data, Cereals (Kilts Center, Chicago Booth) |
| canonical rows | 4,707,776 valid UPC x store x week observations |
| coverage | 489 UPCs, 93 stores, 366 weeks, 1989-09-14 .. 1997-05-01 |
| exclusions | `ok = 0` (141,285 rows, 2.14%), non-positive price (1,753,521 rows, 26.56%) - all documented in `reports/01_DATA_AUDIT.md` |
| data fingerprint | recorded in `artifacts/metrics/dataset_fingerprint.json` |

## Evaluation strategy

Chronological split by Dominick's week index - no random splitting anywhere:

| split | weeks | dates | rows |
| --- | --- | --- | --- |
| train | 2-257 | 1989-09-21 .. 1994-08-11 | 3,206,437 |
| validation | 258-342 | 1994-08-18 .. 1996-03-28 | 715,856 |
| test | 343-399 | 1996-04-04 .. 1997-05-01 | 749,040 |

Model selection used validation only; the test window was scored once.

## Metrics

| metric | validation | test |
| --- | --- | --- |
| WAPE | 0.4135 | 0.4565 |
| MAE | 7.875 units | 8.503 units |
| RMSE | 39.597 units | 72.064 units |
| bias | -3.706 units | -3.883 units (under-forecast) |
| rows scored | 715,856 | 749,040 |

Backtest weeks 390-399: mean weekly WAPE 0.4777 (min 0.3638, max 0.5602).

## Uncertainty

The model produces point predictions only. No predictive interval is fitted, so
none is displayed. The `risk_level` attached to recommendations is a
**heuristic** over series history, price variation and extrapolation distance -
it is explicitly not a calibrated confidence interval.

## Price response

Probed by central finite difference on the fitted model (Phase F):

| statistic | value |
| --- | --- |
| median implied elasticity | -3.10 |
| p10 / p90 | -3.48 / -2.67 |
| curves monotone decreasing | 100% of 300 sampled contexts |
| flat (price-insensitive) curves | 0% |
| negative predicted demand | 0% |

**Known bias:** the implied elasticity is steeper than the controlled
econometric estimates (-1.91 to -2.42). The model therefore likely
over-estimates how much volume responds to price, biasing recommendations
toward price cuts on elastic series and exaggerating estimated uplift.

## Known failure modes

1. **Promotion weeks.** Error is much larger on weeks with a recorded promotion
   code; promotion coding is incomplete, so some "clean" weeks are also
   promotions.
2. **Low-variation series.** Where price barely moved historically, the price
   response is essentially unidentified. These series are screened out
   (`INSUFFICIENT_PRICE_VARIATION`).
3. **Short series.** Fewer than 40 observations triggers
   `INSUFFICIENT_HISTORY` and a keep-current default.
4. **Extrapolation.** Outside the observed price support the model is
   unconstrained by data; the optimizer refuses to go there.
5. **Level shifts.** A Ridge on log demand extrapolates a trend poorly, which
   is why `time_index` is excluded from the linear model and predictions are
   capped at 5x the largest observed weekly demand (the cap is recorded, not
   hidden).
6. **Structural change.** The panel ends in 1997; nothing in the model knows
   about later category dynamics.

## Retraining and monitoring

* Retrain when realised WAPE degrades materially on recent weeks - not on
  drift alone (`reports/MONITORING_DESIGN.md`).
* Re-run the Phase F price-response validation after every retrain; a model
  that fails it must not be allowed to price, however accurate it is.
* Regenerate `artifacts/metrics/model_metrics.json` and this card together.

## Ethical and legal considerations

See `docs/RESPONSIBLE_PRICING.md`. In summary: optimization is over product,
store and time context only; no protected or personal attribute is used, is
available, or would be acceptable to use.

---

## Phase L update - price response separated from forecasting

The card above describes the **baseline forecaster**. Since Phase L the pricing
layer no longer uses that model's own price coefficient for counterfactuals.

| component | role |
| --- | --- |
| `M1 ridge log-log` | baseline contextual demand at the current price `Q_hat(p0)` |
| elasticity table (`artifacts/models/elasticity_table.csv`) | the price response `epsilon` |
| hybrid | `Q(p) = Q_hat(p0) * (p / p0) ** epsilon` |

**Default price-response method: `shrunk`** (per-UPC empirical-Bayes elasticity,
pooled fallback). Configurable in `configs/config.yaml` under
`pricing_response.method`; `ml` keeps the native response as a benchmark.

| elasticity | value |
| --- | --- |
| native ML implied (finite difference) | -3.10 |
| pooled controlled, training weeks 2-257 | **-2.029** (se 0.083) |
| UPC x store fixed effects, training only | -2.222 (se 0.084) |
| shrunk per-UPC, median / p10 / p90 | -2.029 / -2.79 / -1.17 |
| products with a usable own estimate | 239 of 372 (rest use the pooled fallback) |
| mean empirical-Bayes shrinkage weight | 0.780 (REML tau^2 = 0.766, panel-robust standard errors) |
| pooled elasticity, two-way clustered (UPC, week) 95% CI | [-2.24, -1.82] |

Estimation window: **training weeks only** (2-257), so an elasticity applied to
a validation or test week never saw that week's outcome.

Decision states returned by the engine: `RECOMMEND_CHANGE` (actionable),
`KEEP_CURRENT`, `REVIEW_REQUIRED`. HIGH-risk contexts are never actionable
under the default profiles.
