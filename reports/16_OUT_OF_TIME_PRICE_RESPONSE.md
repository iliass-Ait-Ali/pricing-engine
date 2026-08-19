# 16. Out-of-time price-response check

> **This is predictive / naturalistic validation, not causal identification.**
> Prices in the Dominick's data were set by the retailer, not randomised. An
> observed demand move after a price move confounds the price effect with
> whatever caused the retailer to change the price. Nothing in this report
> identifies a causal price effect, and no number here may be described as one.
> See `docs/CAUSAL_LIMITATIONS.md`.

## What this does answer

Given a price change that actually happened in weeks the elasticity estimator
never saw, which price-response method predicts the demand that followed?

## Method

* Elasticities were estimated on training weeks 2-257 only.
  Every episode below is from weeks
  258-399
  (validation + test).
* An **episode** is a `UPC x store` series observed in two consecutive weeks,
  both with positive price and positive units, where the price moved by at least
  5% and at most 60%.
  **154,899 episodes** qualify
  (3 further episodes were dropped because the contextual
  baseline predicted zero units, which makes every ratio metric undefined).
* The contextual baseline is the demand model's prediction *at the previous
  price* using week `t`'s context - so seasonality, trend, promotion coding and
  the lagged demand level are absorbed, and the elasticity only has to explain
  the deviation the price move caused:

```
baseline_t  = Q_ML(p_{t-1} | context of week t)
predicted_t = baseline_t * (p_t / p_{t-1}) ** epsilon
```

* `epsilon = 0` (baseline only) is included as the **null model**. A
  price-response method that cannot beat it is not adding anything.
* 73.3% of episodes carry a
  recorded promotion code in either week; those are reported separately because
  a Bonus Buy moves price and display and feature all at once.

## 1. All episodes

| Method | n | WAPE | MAE (units) | Sign accuracy | Median abs error in log demand change | Rank corr |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| null (baseline only, epsilon = 0) | 154,899 | 0.8086 | 45.43 | n/a | 0.697 | n/a |
| pooled elasticity | 154,899 | 0.6249 | 35.11 | 81.7% | 0.436 | +0.796 |
| shrunk product elasticity | 154,899 | 0.5853 | 32.89 | 81.7% | 0.437 | +0.784 |
| native ML price response | 154,899 | 0.6042 | 33.95 | 81.7% | 0.459 | +0.786 |

## 2. Episodes with no recorded promotion

| Method | n | WAPE | MAE (units) | Sign accuracy | Median abs error in log demand change | Rank corr |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| null (baseline only, epsilon = 0) | 41,310 | 0.7152 | 24.05 | n/a | 0.440 | n/a |
| pooled elasticity | 41,310 | 0.5944 | 19.99 | 68.3% | 0.392 | +0.553 |
| shrunk product elasticity | 41,310 | 0.5596 | 18.82 | 68.3% | 0.396 | +0.543 |
| native ML price response | 41,310 | 0.5877 | 19.76 | 68.3% | 0.436 | +0.543 |

## 3. Episodes with a recorded promotion in either week

| Method | n | WAPE | MAE (units) | Sign accuracy | Median abs error in log demand change | Rank corr |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| null (baseline only, epsilon = 0) | 113,589 | 0.8263 | 53.21 | n/a | 0.819 | n/a |
| pooled elasticity | 113,589 | 0.6307 | 40.61 | 86.6% | 0.454 | +0.818 |
| shrunk product elasticity | 113,589 | 0.5902 | 38.00 | 86.6% | 0.454 | +0.810 |
| native ML price response | 113,589 | 0.6073 | 39.10 | 86.6% | 0.467 | +0.812 |

## 4. Price increases

| Method | n | WAPE | MAE (units) | Sign accuracy | Median abs error in log demand change | Rank corr |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| null (baseline only, epsilon = 0) | 66,623 | 0.6767 | 10.75 | n/a | 0.513 | n/a |
| pooled elasticity | 66,623 | 0.4109 | 6.53 | 78.6% | 0.353 | +0.398 |
| shrunk product elasticity | 66,623 | 0.4189 | 6.66 | 78.6% | 0.370 | +0.377 |
| native ML price response | 66,623 | 0.4379 | 6.96 | 78.6% | 0.412 | +0.335 |

## 5. Price decreases

| Method | n | WAPE | MAE (units) | Sign accuracy | Median abs error in log demand change | Rank corr |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| null (baseline only, epsilon = 0) | 88,276 | 0.8269 | 71.60 | n/a | 0.939 | n/a |
| pooled elasticity | 88,276 | 0.6545 | 56.68 | 84.1% | 0.524 | +0.684 |
| shrunk product elasticity | 88,276 | 0.6084 | 52.68 | 84.1% | 0.503 | +0.654 |
| native ML price response | 88,276 | 0.6272 | 54.32 | 84.1% | 0.502 | +0.675 |

## 6. Calibration by price-change bucket

The observed implied elasticity of an episode is
`log(actual / baseline) / log(p_t / p_{t-1})` - what elasticity would have been
needed to explain the demand that actually followed, given the contextual
baseline.

| Price change bucket | Episodes | Median implied elasticity | Median, no promotion | n (no promotion) |
| --- | ---: | ---: | ---: | ---: |
| -60..-30% | 37,368 | -3.23 | -3.74 | 4,671 |
| -30..-15% | 26,520 | -2.41 | -1.51 | 5,308 |
| -15..-5% | 24,388 | -2.00 | -1.33 | 13,343 |
| 5..15% | 21,008 | -1.93 | -1.46 | 9,896 |
| 15..30% | 15,972 | -1.76 | -1.11 | 4,856 |
| 30..60% | 29,643 | -1.92 | -1.64 | 3,236 |

Median observed implied elasticity across all episodes:
**-2.45**
(no-promotion episodes: **-1.86**).
The pooled elasticity in the shipped table is
**-2.03**.

## 7. Reading

* Lowest WAPE over all episodes: **shrunk product elasticity**.
* Lowest WAPE on non-promotion episodes: **shrunk product elasticity**.

Sign accuracy is identical across the three price-response methods in every
split, and that is expected: they disagree about *how much* demand moves, never
about *which way*. Sign accuracy therefore tests the sign of the elasticity, not
the choice of method.

The calibration table is the sharper test. Non-promotion episodes imply a median
elasticity of -1.86 and
all episodes -2.45, bracketing the
shipped pooled estimate of -2.03. The native ML response's implied
elasticity of about -3.10 (`reports/08`) sits outside that range on the steep
side, which is the out-of-time evidence for the Phase L decision to stop using it
as the default.

Three warnings about over-reading this table:

1. **The null model is strong.** The contextual baseline already knows last
   week's units, the season and the promotion flag. Beating it by a small margin
   is not evidence of a good price-response model.
2. **Promotion episodes flatter the steeper methods.** A recorded promotion
   bundles a price cut with display and feature activity, so demand jumps far
   more than any pure price elasticity would predict. A method that
   over-predicts the price response will look better on those episodes for the
   wrong reason. That is why the non-promotion split is the one to read.
3. **Selection.** Weeks in which the retailer changed price are not a random
   sample of weeks. The implied elasticities above are the elasticities *of the
   episodes the retailer chose to create*.

---

*Generated by `scripts/audit_out_of_time_response.py` in 5.7s.
Episode-level data: `artifacts/metrics/price_change_episodes.csv`.*
