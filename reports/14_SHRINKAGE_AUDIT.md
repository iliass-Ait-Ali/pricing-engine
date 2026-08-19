# 14. Empirical-Bayes shrinkage audit

**Trigger.** Phase L reported a mean shrinkage weight of **0.954**. A weight
that close to 1 means the per-product coefficients were being passed through
almost untouched - the shrinkage was not doing much. That is either a genuine
finding about the data or a defect in the estimator. This report decides which.
It is both.

## 1. The estimator, derived

The standard normal-normal hierarchical model:

```
epsilon_hat_i | theta_i ~ N(theta_i, se_i^2)      sampling error
theta_i                ~ N(mu, tau^2)             between-product dispersion
```

Marginally `epsilon_hat_i ~ N(mu, tau^2 + se_i^2)`, and the posterior mean of
`theta_i` is

```
E[theta_i | epsilon_hat_i] = w_i * epsilon_hat_i + (1 - w_i) * mu
w_i                        = tau^2 / (tau^2 + se_i^2)
```

which is the formula in the code. The weight is the share of the observed
variance that is *signal*. Two properties follow immediately and are pinned by
`tests/test_shrinkage.py`:

* `w_i` is monotonically decreasing in `se_i` and lies in `[0, 1]`;
* `theta_hat_i` always lies between `epsilon_hat_i` and `mu`.

**The weight has two inputs and Phase L only audited one.** `w_i` is large
either because `tau^2` is genuinely large *or because `se_i` is too small*. A
standard error that ignores panel clustering pushes every weight toward 1 and
silently disables the shrinkage. That is what had happened.

## 2. The eight audit questions

**1. How is `tau^2` estimated?** Phase L used method of moments:
`tau^2 = max(var(estimates) - mean(se^2), 0)`. Phase M uses REML - the
restricted-likelihood fixed point
`tau^2 <- sum(w_i^2 [(e_i - mu)^2 - se_i^2 + 1/sum(w)]) / sum(w_i^2)` - because
the moment estimator is noisier and truncates at zero more often. The moment
estimator is still available behind `elasticity.shrinkage_tau2_estimator: moment`.

**2. Is sampling variance removed appropriately?** In form, yes: both estimators
net out the sampling component. But the subtraction is only correct if `se_i` is
the *true* sampling standard error. The HC1 standard errors used in Phase L
understate it by a median factor of
**3.79x** (see `reports/15`), so too
little sampling variance was removed and `tau^2` was overstated - the second
mechanism inflating the weights.

**3. Can `tau^2` be negatively biased or inflated?** Both.
*Downward*: the zero floor truncates whenever sampling noise exceeds the observed
spread, producing exact zeros in small samples - one reason REML is now the
default. *Upward*: understated `se_i`, and the fact that the moment estimator
measured dispersion around the **sample mean of the estimates** while the
shrinkage pulled toward the **pooled elasticity**. Empirically those two centres
are close here (-1.923 vs -2.029), so this particular
defect cost little - but it is a defect, and `tau^2` is now measured around the
same prior mean the shrinkage targets.

**4. Do extreme product coefficients distort `tau^2`?** Yes, strongly. The
unfiltered set gives `tau^2 = 1.246` against
`0.766` for the filtered set - wrong-signed and very imprecise
products inflate the estimated between-product dispersion substantially.

**5. Are the standard errors comparable across UPC regressions?** They are now.
Each per-UPC regression absorbs store fixed effects; Phase M added the
degrees-of-freedom charge for those absorbed effects and cluster-robust
covariance. Before that, products observed in many stores had their standard
errors understated more than products observed in few, so the weights were not
comparable across products.

**6. Does filtering before estimating `tau^2` introduce bias?** Yes, and the
direction is *conservative*. Removing wrong-signed and imprecise products
truncates the tail of the coefficient distribution, which lowers `tau^2`
(1.246 -> 0.766) and therefore shrinks the
surviving products harder. Keeping the filter is defensible on domain grounds -
a positive price coefficient is evidence of endogeneity, not of upward-sloping
demand - but the resulting `tau^2` is a within-filter dispersion and is labelled
as such.

**7. Should the prior mean be the pooled/category elasticity?** Yes, and it now
is (`shrinkage_prior_mean: pooled`). It is the same quantity used as the fallback
for products with no usable estimate, so a product does not jump when it crosses
the usability threshold. Estimating the prior mean freely from the product
coefficients gives -1.933, close enough
to the pooled -2.029 that the choice does not drive anything.

**8. Do the weights behave monotonically with the standard error?** Yes -
verified on the shipped table itself. Sorting the 239 usable
products by standard error, the weights are monotonically non-increasing:
**True**.

## 3. What each choice does, on the real data

| Variant | n | Median se | tau^2 | tau | Prior mean | Mean w | Median w | Median shrunk | p10 / p90 shrunk |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Phase L: moment tau^2, sample-mean prior, HC1 se, filtered | 239 | 0.101 | 0.906 | 0.952 | -1.923 | 0.955 | 0.989 | -1.84 | [-3.17, -0.70] |
| moment tau^2, pooled prior, HC1 se, filtered | 239 | 0.101 | 0.913 | 0.956 | -2.029 | 0.955 | 0.989 | -1.85 | [-3.17, -0.70] |
| REML tau^2, pooled prior, HC1 se, filtered | 239 | 0.101 | 0.901 | 0.949 | -2.029 | 0.955 | 0.989 | -1.85 | [-3.17, -0.70] |
| moment tau^2, pooled prior, ROBUST se, filtered | 239 | 0.390 | 0.673 | 0.820 | -2.029 | 0.761 | 0.815 | -1.90 | [-3.03, -0.98] |
| Phase M (shipped): REML tau^2, pooled prior, ROBUST se, filtered | 239 | 0.390 | 0.766 | 0.875 | -2.029 | 0.780 | 0.834 | -1.90 | [-3.05, -0.95] |
| REML tau^2, pooled prior, ROBUST se, UNFILTERED | 272 | 0.404 | 1.246 | 1.116 | -2.029 | 0.813 | 0.884 | -1.82 | [-3.04, -0.56] |
| REML tau^2, free prior, ROBUST se, filtered | 239 | 0.390 | 0.759 | 0.871 | -1.933 | 0.778 | 0.833 | -1.87 | [-3.04, -0.94] |

Reading the table:

* Switching the `tau^2` estimator alone (moment -> REML) barely moves the mean
  weight.
* Switching the prior mean alone barely moves it either.
* Switching the **standard errors** from HC1 to panel-robust moves the mean
  weight from 0.955 to 0.780. That
  is the whole story.

## 4. Why the mean weight is still 0.78

Because the between-product dispersion is genuinely large.
`tau = 0.88` says the standard deviation of *true* product
elasticities is about 0.88 - cereals really do differ, and a
family-size branded cereal is not a store-brand bran flake. The median robust
standard error is 0.39. With
`tau (0.88)` comfortably larger than
`se (0.39)`, the arithmetic gives a high weight and that is
the correct answer, not a bug. The Phase L value of 0.95 was too high;
0.78 is what the data support once the standard errors
are honest.

## 5. Weight distribution in the shipped table

| Shrinkage weight | Products | Share |
| --- | ---: | ---: |
| <0.25 | 0 | 0.0% |
| 0.25-0.5 | 25 | 10.5% |
| 0.5-0.75 | 48 | 20.1% |
| 0.75-0.9 | 96 | 40.2% |
| 0.9-0.99 | 70 | 29.3% |
| >0.99 | 0 | 0.0% |

Products with a weight below 0.5 are priced mostly by the category elasticity
even though they passed the usability screen. That is the intended behaviour.

## 6. What changed in the shipped artifact

| Quantity | Phase L | Phase M |
| --- | ---: | ---: |
| `tau^2` | 0.906 | 0.766 |
| `tau` | 0.952 | 0.875 |
| Mean shrinkage weight | 0.955 | 0.780 |
| Median usable standard error | 0.101 | 0.390 |
| Usable products | 247 | 239 |
| Median shrunk elasticity | -1.842 | -1.898 |
| p10 / p90 shrunk elasticity | -3.17 / -0.70 | -3.05 / -0.95 |

The per-product elasticity distribution is materially tighter, which is the
point: less of the observed spread is now taken at face value.

## 7. Verdict

The Phase L estimator was **not wrong in its formula** - the posterior-mean
algebra was correct and the code implemented it faithfully. It was wrong in its
**inputs**: HC1 standard errors on a clustered panel, and a `tau^2` measured
around a different centre from the one the shrinkage targeted. Both are fixed.
The mean weight is now 0.780, and the 0.95 figure has been
removed from every report and document.

---

*Generated by `scripts/audit_shrinkage.py` in 0.0s.
Deterministic behaviour is pinned by `tests/test_shrinkage.py`.*
