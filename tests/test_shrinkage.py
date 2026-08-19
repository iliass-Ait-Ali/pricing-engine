"""Phase M / Task 4 - deterministic behaviour of the empirical-Bayes shrinkage.

These are the four limiting cases the estimator has to get right, plus the
structural properties (monotonicity, bounds, prior-mean handling) that make the
weights interpretable. If any of these break, every per-product elasticity in
`artifacts/models/elasticity_table.csv` is suspect.
"""

from __future__ import annotations

import numpy as np
import pytest

from pricing_engine.economics.elasticity_store import (
    ElasticityError,
    empirical_bayes_shrinkage,
)

POOLED = -2.0


# ---------------------------------------------------------------------------
# the four limiting cases
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method", ["reml", "moment"])
def test_extremely_imprecise_estimates_get_weight_near_zero(method):
    """se >> tau  ->  w -> 0: the product's own coefficient is discarded."""
    estimates = np.array([-1.0, -3.0, -2.0, -2.5, -1.5])
    se = np.full(5, 50.0)
    res = empirical_bayes_shrinkage(estimates, se, POOLED, method=method)
    assert res.weights.max() < 0.01
    assert np.allclose(res.shrunk, res.prior_mean, atol=0.05)


@pytest.mark.parametrize("method", ["reml", "moment"])
def test_extremely_precise_estimates_get_weight_near_one(method):
    """se << tau  ->  w -> 1: the product keeps its own coefficient."""
    estimates = np.array([-1.0, -3.0, -2.0, -2.5, -1.5])
    se = np.full(5, 1e-4)
    res = empirical_bayes_shrinkage(estimates, se, POOLED, method=method)
    assert res.weights.min() > 0.999
    assert np.allclose(res.shrunk, estimates, atol=1e-3)


def test_zero_between_product_variance_collapses_everything_to_the_prior():
    """When every product really has the same elasticity, tau^2 -> 0.

    The observed spread is then entirely sampling noise, so the estimator must
    conclude there is nothing product-specific to keep.
    """
    rng = np.random.default_rng(0)
    se = np.full(400, 0.30)
    estimates = POOLED + rng.normal(0.0, 0.30, 400)  # spread == sampling noise
    res = empirical_bayes_shrinkage(estimates, se, POOLED)
    assert res.tau2 < 0.02
    assert res.weights.max() < 0.2
    assert np.abs(res.shrunk - POOLED).max() < 0.5


def test_large_true_heterogeneity_keeps_product_signal():
    """Genuine dispersion between products -> large tau^2 -> weak pooling."""
    rng = np.random.default_rng(1)
    truth = POOLED + rng.normal(0.0, 1.5, 400)
    se = np.full(400, 0.30)
    estimates = truth + rng.normal(0.0, 0.30, 400)
    res = empirical_bayes_shrinkage(estimates, se, POOLED)
    assert res.tau2 > 1.0
    assert res.weights.min() > 0.9
    # the shrunk estimates must still track the truth better than the prior does
    assert np.mean((res.shrunk - truth) ** 2) < np.mean((POOLED - truth) ** 2)


# ---------------------------------------------------------------------------
# structural properties
# ---------------------------------------------------------------------------
def test_weights_are_monotonically_decreasing_in_the_standard_error():
    estimates = np.full(6, -2.5)
    se = np.array([0.05, 0.10, 0.25, 0.50, 1.00, 2.00])
    res = empirical_bayes_shrinkage(estimates, se, POOLED)
    assert np.all(np.diff(res.weights) < 0)
    assert np.all((res.weights >= 0) & (res.weights <= 1))


def test_shrunk_estimate_always_lies_between_the_raw_estimate_and_the_prior():
    rng = np.random.default_rng(2)
    estimates = rng.uniform(-5.0, -0.2, 200)
    se = rng.uniform(0.05, 2.0, 200)
    res = empirical_bayes_shrinkage(estimates, se, POOLED)
    lo = np.minimum(estimates, res.prior_mean)
    hi = np.maximum(estimates, res.prior_mean)
    assert np.all(res.shrunk >= lo - 1e-9)
    assert np.all(res.shrunk <= hi + 1e-9)


def test_understated_standard_errors_silently_disable_the_shrinkage():
    """The failure mode Phase M found: too-small se inflates every weight.

    Same estimates, standard errors understated by a factor of 3.4 (the median
    panel-clustering inflation measured in
    reports/15_ELASTICITY_INFERENCE_AUDIT.md) - the mean weight jumps toward 1
    and the shrinkage stops doing anything.
    """
    rng = np.random.default_rng(3)
    truth = POOLED + rng.normal(0.0, 0.9, 300)
    honest_se = rng.uniform(0.20, 0.60, 300)
    estimates = truth + rng.normal(0.0, honest_se)

    honest = empirical_bayes_shrinkage(estimates, honest_se, POOLED)
    optimistic = empirical_bayes_shrinkage(estimates, honest_se / 3.4, POOLED)

    assert optimistic.weights.mean() > honest.weights.mean() + 0.1
    assert optimistic.tau2 > honest.tau2


def test_prior_mean_can_be_the_pooled_estimate_or_estimated_freely():
    rng = np.random.default_rng(4)
    estimates = -1.2 + rng.normal(0.0, 0.5, 200)   # centred far from POOLED
    se = np.full(200, 0.4)

    at_pooled = empirical_bayes_shrinkage(estimates, se, POOLED, prior_mean="pooled")
    free = empirical_bayes_shrinkage(estimates, se, POOLED, prior_mean="estimated")

    assert at_pooled.prior_mean == pytest.approx(POOLED)
    assert free.prior_mean == pytest.approx(-1.2, abs=0.15)
    # tau^2 around a misplaced prior absorbs the mean gap and is therefore larger
    assert at_pooled.tau2 > free.tau2


def test_reml_is_less_prone_to_the_zero_floor_than_the_moment_estimator():
    """Method of moments truncates at zero more often; REML is the default."""
    rng = np.random.default_rng(5)
    truncations_moment = truncations_reml = 0
    for _ in range(40):
        se = np.full(30, 0.5)
        estimates = POOLED + rng.normal(0.0, 0.10, 30) + rng.normal(0.0, 0.5, 30)
        truncations_moment += empirical_bayes_shrinkage(
            estimates, se, POOLED, method="moment"
        ).tau2 == 0.0
        truncations_reml += empirical_bayes_shrinkage(estimates, se, POOLED).tau2 == 0.0
    assert truncations_reml <= truncations_moment


def test_unknown_method_is_rejected():
    with pytest.raises(ElasticityError):
        empirical_bayes_shrinkage(np.array([-2.0, -3.0]), np.array([0.1, 0.1]), POOLED,
                                  method="magic")
