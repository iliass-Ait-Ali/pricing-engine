# 15. Robust inference for the elasticity estimates

**Question.** The reported elasticity standard errors assume a dependence
structure. Retail scanner panels do not have that structure. How much of the
reported precision survives?

**Data.** Training weeks 2-257 only (3,206,437 rows,
3,206,437 after dropping zero-sales weeks). Nothing here uses validation or
test outcomes.

## 1. Which dependence structures are present

| Source | Why it matters | Cluster level that absorbs it |
| --- | --- | --- |
| Store-level demand shocks persisting over weeks | a store's residuals are serially correlated | `store` |
| Product-level shocks hitting all stores at once | national advertising, competitor launches | `UPC` |
| Category-wide weekly shocks | holidays, weather, chain-wide promotion calendar | `week` |
| Series-level persistence | the same UPC in the same store, week after week | `UPC x store panel` |

A single conventional or HC1 standard error assumes **none** of these exist.

## 2. Pooled and fixed-effects specifications

### A. pooled controlled (UPC x store FE + promo/season/trend), 1.2M subsample

n = 1,200,000 | absorbed FE groups = 26,413 | controls: recorded_promotion_flag, sin52, cos52, trend

| Covariance | Elasticity | SE | vs conventional | 95% CI | t | Clusters |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| classical | -2.0289 | 0.0065 | 1.00x | [-2.042, -2.016] | -311.2 | - |
| hc1 (heteroskedasticity-robust) | -2.0289 | 0.0116 | 1.78x | [-2.052, -2.006] | -174.7 | - |
| clustered by UPC | -2.0289 | 0.0837 | 12.84x | [-2.193, -1.865] | -24.2 | 369 |
| clustered by store | -2.0289 | 0.0305 | 4.67x | [-2.089, -1.969] | -66.6 | 86 |
| clustered by week | -2.0289 | 0.0812 | 12.46x | [-2.188, -1.870] | -25.0 | 255 |
| clustered by UPC x store panel | -2.0289 | 0.0153 | 2.35x | [-2.059, -1.999] | -132.3 | 26,413 |
| two-way (UPC, week) | -2.0289 | 0.1058 | 16.24x | [-2.236, -1.821] | -19.2 | - |
### B. UPC x store fixed effects only, 1.2M subsample

n = 1,200,000 | absorbed FE groups = 26,413 | controls: none

| Covariance | Elasticity | SE | vs conventional | 95% CI | t | Clusters |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| classical | -2.2217 | 0.0050 | 1.00x | [-2.231, -2.212] | -446.7 | - |
| hc1 (heteroskedasticity-robust) | -2.2217 | 0.0074 | 1.49x | [-2.236, -2.207] | -299.9 | - |
| clustered by UPC | -2.2217 | 0.0849 | 17.07x | [-2.388, -2.055] | -26.2 | 369 |
| clustered by store | -2.2217 | 0.0492 | 9.89x | [-2.318, -2.125] | -45.2 | 86 |
| clustered by week | -2.2217 | 0.0564 | 11.34x | [-2.332, -2.111] | -39.4 | 255 |
| clustered by UPC x store panel | -2.2217 | 0.0132 | 2.65x | [-2.248, -2.196] | -168.8 | 26,413 |
| two-way (UPC, week) | -2.2217 | 0.0971 | 19.52x | [-2.412, -2.031] | -22.9 | - |
### C. pooled controlled, FULL training sample

n = 3,206,437 | absorbed FE groups = 27,105 | controls: recorded_promotion_flag, sin52, cos52, trend

| Covariance | Elasticity | SE | vs conventional | 95% CI | t | Clusters |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| classical | -2.0443 | 0.0040 | 1.00x | [-2.052, -2.037] | -516.9 | - |
| hc1 (heteroskedasticity-robust) | -2.0443 | 0.0072 | 1.82x | [-2.058, -2.030] | -283.7 | - |
| clustered by UPC | -2.0443 | 0.0826 | 20.89x | [-2.206, -1.882] | -24.7 | 372 |
| clustered by store | -2.0443 | 0.0306 | 7.74x | [-2.104, -1.984] | -66.8 | 86 |
| clustered by week | -2.0443 | 0.0803 | 20.29x | [-2.202, -1.887] | -25.5 | 255 |
| clustered by UPC x store panel | -2.0443 | 0.0122 | 3.08x | [-2.068, -2.020] | -167.9 | 27,105 |
| two-way (UPC, week) | -2.0443 | 0.1047 | 26.47x | [-2.249, -1.839] | -19.5 | - |

The conventional standard error understates the sampling uncertainty of the
pooled elasticity by a factor of
**16.2x** at the widest defensible clustering
(`two-way (UPC, week)`).

## 3. Per-UPC estimates

The per-UPC regressions absorb store fixed effects inside each product and are
reported in the production table with HC1 standard errors. Clustering them:

| Statistic | Value |
| --- | ---: |
| Products estimated | 262 |
| Median HC1 standard error | 0.1071 |
| Median SE clustered by store | 0.1445 |
| Median SE clustered by week | 0.3832 |
| Median inflation, store clustering | 1.18x |
| Median inflation, week clustering | 3.38x |
| Median inflation, worst of the two | 3.38x |
| p90 inflation, worst of the two | 6.83x |
| Share significant at 5% under HC1 | 90.1% |
| Share significant at 5% under the robust SE | 75.2% |

## 4. Choice of estimator, and why

**Adopted for the production elasticity table: the larger of the store-clustered
and week-clustered standard error, per product.**

Reasoning:

* Two-way clustering by `(store, week)` is the textbook answer for this panel,
  but inside a *single product's* regression the number of week clusters
  (~214) and store clusters
  (~86) is modest, and the
  Cameron-Gelbach-Miller subtraction is unstable at that size. Taking the max of
  the two one-way estimators is the conservative reading of the same evidence
  and never reports a smaller interval than either.
* At the pooled level the sample is large enough that two-way clustering is
  stable, so it is reported above and used to describe the category estimate.
* Clustering by `UPC x store panel` is *not* adopted for the per-UPC table: it
  is the finest of the candidate levels and would leave the week-level common
  shock uncontrolled.

**Consequence for shrinkage.** Larger `se_i` mechanically reduces the empirical
Bayes weight `w_i = tau^2 / (tau^2 + se_i^2)`, so the elasticity table shrinks
harder toward the category estimate. That is the correction described in
`reports/14_SHRINKAGE_AUDIT.md`, and it is the main reason the mean shrinkage
weight moved away from 0.95.

## 5. What this does and does not change

It does **not** make the elasticity causal. Wider intervals on an observational
price-response coefficient are a more honest observational coefficient. Price
endogeneity - the retailer setting prices in response to expected demand - is a
separate problem and is discussed in `docs/CAUSAL_LIMITATIONS.md`.

---

*Generated by `scripts/audit_elasticity_inference.py` in 22.3s.*
