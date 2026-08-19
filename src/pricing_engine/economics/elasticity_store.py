"""Estimate, shrink and serve price elasticities for counterfactual pricing.

Phase L. The demand model is good at *forecasting* at observed prices; that is
not the same as being right about the *price response*. This module estimates
the price response separately, from training-period data only, and exposes it
as a lookup table the pricing layer can use.

Three levels of estimate:

A. **pooled controlled** - one elasticity for the whole category, with
   UPC x store fixed effects absorbed plus promotion / seasonality / trend
   controls.
B. **UPC x store fixed-effects specification** - the same design, reported for
   comparison.
C. **per-UPC** - one coefficient per product, with its standard error,
   confidence interval, t statistic, observation count and price variation.

Noisy per-UPC coefficients are never trusted as-is. They are shrunk toward the
pooled estimate by empirical Bayes:

    w_i         = tau^2 / (tau^2 + se_i^2)
    epsilon_i   = w_i * epsilon_hat_i + (1 - w_i) * epsilon_pooled

where ``tau^2`` (the between-product variance of *true* elasticities) is
estimated as ``var(estimates) - mean(se^2)``, floored at zero. A product with a
precise estimate (small ``se_i``) keeps most of its own coefficient; a product
whose coefficient is mostly noise is pulled back to the category.

**Leakage rule:** estimation must be restricted to the model's training weeks,
so an elasticity used to price week 399 never saw week 399's outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from pricing_engine.config import Config, load_config
from pricing_engine.economics.elasticity import fit_loglog, prepare_loglog_frame
from pricing_engine.economics.inference import (
    InferenceError,
    cluster_cov,
    hc1_cov,
    keep_independent_columns,
    ols_fit,
    std_errors,
)

REQUIRED_COLUMNS = ("upc", "store", "week", "move", "effective_unit_price")


class ElasticityError(ValueError):
    """Raised when elasticities cannot be estimated or served."""


def fixed_elasticity_table(epsilon: float) -> ElasticityTable:
    """A table that applies one fixed elasticity everywhere.

    Used for the sensitivity scenarios in
    reports/08_PRICE_RESPONSE_COMPARISON.md - these are 'what if the true
    elasticity were X' probes, not estimates.
    """
    return ElasticityTable(
        pooled=float(epsilon),
        pooled_se=float("nan"),
        products=pd.DataFrame(columns=["upc", "elasticity_final", "elasticity_source"]),
        metadata={"kind": "fixed_scenario", "epsilon": float(epsilon)},
    )


@dataclass
class ElasticityTable:
    """Per-UPC elasticities plus the pooled fallback and its provenance."""

    pooled: float
    pooled_se: float
    products: pd.DataFrame
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- lookup ------------------------------------------------------------
    def epsilon_for(self, upcs: np.ndarray | pd.Series) -> np.ndarray:
        """Return the elasticity to use for each UPC (shrunk, else pooled)."""
        if self.products.empty or "elasticity_final" not in self.products.columns:
            return np.full(len(np.asarray(upcs)), self.pooled, dtype="float64")
        lookup = self.products.set_index("upc")["elasticity_final"]
        values = pd.Series(np.asarray(upcs)).map(lookup)
        return values.fillna(self.pooled).to_numpy(dtype="float64")

    def source_for(self, upcs: np.ndarray | pd.Series) -> np.ndarray:
        """Whether each UPC uses its own shrunk estimate or the pooled fallback."""
        if self.products.empty or "elasticity_source" not in self.products.columns:
            return np.array(["pooled_fallback"] * len(np.asarray(upcs)))
        lookup = self.products.set_index("upc")["elasticity_source"]
        values = pd.Series(np.asarray(upcs)).map(lookup)
        return values.fillna("pooled_fallback").to_numpy()

    # -- persistence -------------------------------------------------------
    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        frame = self.products.copy()
        frame["pooled_elasticity"] = self.pooled
        frame["pooled_se"] = self.pooled_se
        for key, value in self.metadata.items():
            if isinstance(value, (int, float, str)):
                frame[f"meta_{key}"] = value
        frame.to_csv(p, index=False)
        return p

    @classmethod
    def load(cls, path: str | Path) -> ElasticityTable:
        p = Path(path)
        if not p.exists():
            raise ElasticityError(
                f"Elasticity table not found at {p}. Run `python scripts/estimate_elasticity.py` "
                "before using an elasticity-based pricing response."
            )
        frame = pd.read_csv(p)
        pooled = float(frame["pooled_elasticity"].iloc[0])
        pooled_se = float(frame["pooled_se"].iloc[0])
        meta = {
            c.removeprefix("meta_"): frame[c].iloc[0] for c in frame.columns if c.startswith("meta_")
        }
        keep = [c for c in frame.columns if not c.startswith("meta_") and not c.startswith("pooled_")]
        return cls(pooled=pooled, pooled_se=pooled_se, products=frame[keep], metadata=meta)


# ---------------------------------------------------------------------------
# estimation
# ---------------------------------------------------------------------------
def _controls(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    woy = out["week_of_year"].astype("float64") if "week_of_year" in out.columns else 0.0
    out["sin52"] = np.sin(2 * np.pi * woy / 52.0)
    out["cos52"] = np.cos(2 * np.pi * woy / 52.0)
    out["trend"] = out["week"].astype("float64") / 100.0
    if "recorded_promotion_flag" not in out.columns:
        out["recorded_promotion_flag"] = 0.0
    return out


def estimate_per_upc(
    df: pd.DataFrame,
    *,
    min_obs: int,
    min_distinct_prices: int,
    control_cols: list[str] | None = None,
) -> pd.DataFrame:
    """Per-UPC log-log elasticity with full diagnostics.

    Store fixed effects are absorbed within each product, so the coefficient is
    identified from *within store* price movement rather than from cross-store
    price-level differences.

    Standard errors
    ---------------
    Each product's regression pools ~90 stores over ~250 weeks. Its residuals
    are correlated within store (persistent local demand shocks) and within week
    (category-wide shocks: holidays, weather, the chain promotion calendar).
    HC1 assumes neither exists and is therefore far too small - a median factor
    of about 3.4 on this data, see ``reports/15_ELASTICITY_INFERENCE_AUDIT.md``.

    ``std_error`` is the **larger of the store-clustered and the week-clustered**
    standard error, which is the conservative reading of the two one-way
    estimators. Two-way clustering is reported at the pooled level, where there
    are enough clusters for it to be stable; inside one product the
    Cameron-Gelbach-Miller subtraction is not. ``std_error_hc1`` is kept in the
    output for comparison, and the ratio is what drives the shrinkage weights.
    """
    controls = control_cols or ["recorded_promotion_flag", "sin52", "cos52", "trend"]
    rows: list[dict[str, Any]] = []
    prepared, _ = prepare_loglog_frame(df, zero_handling="drop")

    for upc, sub in prepared.groupby("upc", observed=True):
        n_obs = len(sub)
        n_prices = int(sub["effective_unit_price"].nunique())
        price_cv = float(sub["effective_unit_price"].std() / sub["effective_unit_price"].mean())
        record = {
            "upc": int(upc),
            "n_obs": int(n_obs),
            "n_distinct_prices": n_prices,
            "n_stores": int(sub["store"].nunique()),
            "n_weeks": int(sub["week"].nunique()),
            "price_cv": price_cv,
            "price_min": float(sub["effective_unit_price"].min()),
            "price_max": float(sub["effective_unit_price"].max()),
        }
        blank = {
            "elasticity_raw": np.nan, "std_error": np.nan, "std_error_hc1": np.nan,
            "std_error_cluster_store": np.nan, "std_error_cluster_week": np.nan,
            "se_inflation_vs_hc1": np.nan, "t_stat": np.nan, "ci_low": np.nan,
            "ci_high": np.nan, "r_squared": np.nan, "significant_negative": False,
            "usable": False,
        }
        if n_obs < min_obs or n_prices < min_distinct_prices:
            record.update({**blank, "reject_reason": "insufficient_observations_or_price_variation"})
            rows.append(record)
            continue

        x_cols = ["log_p"] + [c for c in controls if c in sub.columns]
        X = sub[x_cols].astype("float64").to_numpy()
        y = sub["log_q"].to_numpy(dtype="float64")

        # absorb store fixed effects within the product
        store_codes = pd.factorize(sub["store"])[0]
        n_stores = int(store_codes.max()) + 1
        if n_stores > 1:
            X = _demean(X, store_codes)
            y = _demean(y.reshape(-1, 1), store_codes).ravel()

        X = np.column_stack([np.ones(len(y)), X])
        # A control that does not vary inside this product is exactly zero after
        # the within transformation; keeping it would make X'X singular.
        kept = keep_independent_columns(X)
        if len(kept) < 2 or kept[1] != 1:
            record.update({**blank, "reject_reason": "no_price_variation_after_fixed_effects"})
            rows.append(record)
            continue
        X = X[:, kept]
        try:
            fit = ols_fit(X, y, n_absorbed=max(n_stores - 1, 0))
            se_hc1 = float(std_errors(hc1_cov(fit))[1])
            week_codes = pd.factorize(sub["week"])[0]
            se_store = (
                float(std_errors(cluster_cov(fit, store_codes))[1]) if n_stores > 1 else np.nan
            )
            se_week = (
                float(std_errors(cluster_cov(fit, week_codes))[1])
                if int(week_codes.max()) + 1 > 1
                else np.nan
            )
        except (InferenceError, np.linalg.LinAlgError) as exc:
            record.update({**blank, "reject_reason": f"regression_failed: {type(exc).__name__}"})
            rows.append(record)
            continue

        beta = float(fit.beta[1])
        se = float(np.nanmax([se_store, se_week, se_hc1]))
        if not np.isfinite(se) or se <= 0:
            record.update({**blank, "reject_reason": "standard_error_not_computable"})
            rows.append(record)
            continue

        t_stat = beta / se
        record.update(
            {
                "elasticity_raw": beta,
                "std_error": se,
                "std_error_hc1": se_hc1,
                "std_error_cluster_store": se_store,
                "std_error_cluster_week": se_week,
                "se_inflation_vs_hc1": se / se_hc1 if se_hc1 > 0 else np.nan,
                "t_stat": t_stat,
                "ci_low": beta - 1.96 * se,
                "ci_high": beta + 1.96 * se,
                "r_squared": fit.r_squared,
                "significant_negative": bool(beta < 0 and t_stat < -1.96),
                "usable": True,
                "reject_reason": "",
            }
        )
        rows.append(record)

    return pd.DataFrame(rows)


def _demean(X: np.ndarray, codes: np.ndarray) -> np.ndarray:
    out = np.empty_like(X, dtype="float64")
    n_groups = codes.max() + 1
    counts = np.bincount(codes, minlength=n_groups).astype("float64")
    for j in range(X.shape[1]):
        sums = np.bincount(codes, weights=X[:, j], minlength=n_groups)
        means = sums / np.maximum(counts, 1.0)
        out[:, j] = X[:, j] - means[codes]
    return out


@dataclass
class ShrinkageResult:
    """Output of the empirical-Bayes step, with everything needed to audit it."""

    shrunk: np.ndarray
    weights: np.ndarray
    tau2: float
    prior_mean: float
    method: str
    n_used: int
    tau2_moment: float
    prior_mean_estimated: float
    converged: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "tau2": self.tau2,
            "tau": float(np.sqrt(self.tau2)),
            "prior_mean": self.prior_mean,
            "prior_mean_estimated_freely": self.prior_mean_estimated,
            "method": self.method,
            "n_used": self.n_used,
            "tau2_method_of_moments": self.tau2_moment,
            "converged": self.converged,
            "mean_weight": float(np.mean(self.weights[self.weights > 0]))
            if np.any(self.weights > 0)
            else 0.0,
        }


def _tau2_moment(est: np.ndarray, se: np.ndarray, prior_mean: float | None) -> float:
    """Method-of-moments between-product variance.

    Around the sample mean when ``prior_mean`` is None (the Phase L behaviour),
    otherwise around the prior mean that the shrinkage actually targets - which
    is the internally consistent choice, because ``tau^2`` is supposed to be the
    dispersion of true elasticities *around the prior*, not around whatever the
    sample happens to average to.
    """
    if prior_mean is None:
        spread = float(np.var(est, ddof=1))
    else:
        spread = float(np.mean((est - prior_mean) ** 2))
    return max(spread - float(np.mean(se**2)), 0.0)


def _tau2_reml(
    est: np.ndarray, se: np.ndarray, prior_mean: float | None, *, max_iter: int = 500, tol: float = 1e-12
) -> tuple[float, float, bool]:
    """REML estimate of ``tau^2`` for ``est_i ~ N(mu, tau^2 + se_i^2)``.

    The standard restricted-likelihood fixed-point iteration used in
    random-effects meta-analysis:

        w_i    = 1 / (tau^2 + se_i^2)
        mu     = sum(w_i est_i) / sum(w_i)                    (or fixed)
        tau^2 <- sum(w_i^2 [(est_i - mu)^2 - se_i^2 + 1/sum(w)]) / sum(w_i^2)

    The ``+ 1/sum(w)`` term is the restricted (REML) correction for having
    estimated ``mu``; dropping it gives the ML estimator, which is biased
    downward. Floored at zero, because a negative variance is not a variance.
    """
    tau2 = _tau2_moment(est, se, prior_mean)
    converged = False
    mu = prior_mean if prior_mean is not None else float(np.mean(est))
    for _ in range(max_iter):
        w = 1.0 / (tau2 + se**2)
        if prior_mean is None:
            mu = float(np.sum(w * est) / np.sum(w))
        numerator = float(np.sum(w**2 * ((est - mu) ** 2 - se**2 + 1.0 / np.sum(w))))
        new = max(numerator / float(np.sum(w**2)), 0.0)
        if abs(new - tau2) < tol:
            tau2 = new
            converged = True
            break
        tau2 = new
    return tau2, mu, converged


def empirical_bayes_shrinkage(
    estimates: np.ndarray,
    std_errors: np.ndarray,
    pooled: float,
    *,
    method: str = "reml",
    prior_mean: str = "pooled",
) -> ShrinkageResult:
    """Shrink noisy per-product estimates toward the category elasticity.

    Model (a standard normal-normal hierarchical model):

        epsilon_hat_i | theta_i ~ N(theta_i, se_i^2)      sampling
        theta_i                ~ N(mu, tau^2)             between products

    The posterior mean of ``theta_i`` is

        theta_hat_i = w_i * epsilon_hat_i + (1 - w_i) * mu
        w_i         = tau^2 / (tau^2 + se_i^2)

    so a product with a precise estimate (``se_i`` small relative to ``tau``)
    keeps its own coefficient, and a product whose coefficient is mostly noise
    is pulled back to the category. The weight is monotonically decreasing in
    ``se_i`` by construction, and lies in ``[0, 1]``.

    Parameters
    ----------
    method:
        ``"reml"`` (default) - restricted maximum likelihood for ``tau^2``.
        ``"moment"`` - the method-of-moments estimator
        ``max(spread - mean(se^2), 0)``. Kept because it is transparent and is
        what Phase L used; REML is the default because the moment estimator is
        noisier and truncates at zero more often.
    prior_mean:
        ``"pooled"`` (default) - shrink toward the pooled controlled elasticity,
        which is also the fallback used for products with no usable estimate, so
        the two are consistent. ``"estimated"`` - estimate the prior mean from
        the product coefficients themselves.

    **Both inputs matter equally.** ``tau^2`` is only half of the weight: if the
    standard errors are understated - for example by ignoring the panel
    clustering in the underlying regressions - every ``w_i`` is pushed toward 1
    and the shrinkage silently stops working. See
    ``reports/14_SHRINKAGE_AUDIT.md``.
    """
    est = np.asarray(estimates, dtype="float64")
    se = np.asarray(std_errors, dtype="float64")
    finite = np.isfinite(est) & np.isfinite(se) & (se > 0)
    if finite.sum() < 2:
        return ShrinkageResult(
            shrunk=np.full_like(est, pooled),
            weights=np.zeros_like(est),
            tau2=0.0,
            prior_mean=float(pooled),
            method=method,
            n_used=int(finite.sum()),
            tau2_moment=0.0,
            prior_mean_estimated=float(pooled),
            converged=True,
        )

    e, s_ = est[finite], se[finite]
    target = float(pooled) if prior_mean == "pooled" else None
    tau2_mom = _tau2_moment(e, s_, target)
    _, mu_free, _ = _tau2_reml(e, s_, None)

    if method == "moment":
        tau2, converged = tau2_mom, True
        mu = target if target is not None else float(np.mean(e))
    elif method == "reml":
        tau2, mu, converged = _tau2_reml(e, s_, target)
    else:
        raise ElasticityError(f"unknown shrinkage method {method!r}; use 'reml' or 'moment'")

    weights = np.where(finite, tau2 / (tau2 + np.where(se > 0, se**2, np.inf)), 0.0)
    shrunk = np.where(finite, weights * est + (1.0 - weights) * mu, mu)
    return ShrinkageResult(
        shrunk=shrunk,
        weights=weights,
        tau2=float(tau2),
        prior_mean=float(mu),
        method=method,
        n_used=int(finite.sum()),
        tau2_moment=float(tau2_mom),
        prior_mean_estimated=float(mu_free),
        converged=bool(converged),
    )


def _pooled_robust_inference(
    d: pd.DataFrame, *, sample_size: int, seed: int
) -> dict[str, float]:
    """Pooled controlled elasticity under conventional, HC1 and clustered covariance."""
    from pricing_engine.economics.inference import demean, elasticity_inference

    prepared, _ = prepare_loglog_frame(d, zero_handling="drop")
    if len(prepared) > sample_size:
        prepared = prepared.sample(sample_size, random_state=seed)
    controls = ["recorded_promotion_flag", "sin52", "cos52", "trend"]
    cols = ["log_p"] + [c for c in controls if c in prepared.columns]
    panel = pd.factorize(
        prepared["upc"].astype("int64").astype(str) + "_" + prepared["store"].astype("int64").astype(str)
    )[0]
    X = np.column_stack([np.ones(len(prepared)), demean(prepared[cols].to_numpy(dtype="float64"), panel)])
    y = demean(prepared["log_q"].to_numpy(dtype="float64"), panel)
    X = X[:, keep_independent_columns(X)]
    res = elasticity_inference(
        X, y,
        cluster_specs={
            "UPC": pd.factorize(prepared["upc"])[0],
            "week": pd.factorize(prepared["week"])[0],
        },
        two_way=("UPC", "week"),
        n_absorbed=int(panel.max()) + 1,
    )
    tw = res["specifications"]["two-way (UPC, week)"]
    return {
        "coefficient": res["coefficient"],
        "hc1_se": res["specifications"]["hc1 (heteroskedasticity-robust)"]["std_error"],
        "classical_se": res["specifications"]["classical"]["std_error"],
        "two_way_se": tw["std_error"],
        "two_way_ci_low": tw["ci_low"],
        "two_way_ci_high": tw["ci_high"],
        "two_way_inflation_vs_classical": tw["ratio_to_classical"],
    }


def build_elasticity_table(
    train_df: pd.DataFrame,
    *,
    cfg: Config | None = None,
    training_weeks: tuple[int, int] | None = None,
) -> ElasticityTable:
    """Estimate pooled, fixed-effects and per-UPC elasticities, then shrink.

    ``train_df`` MUST contain training-period rows only.
    """
    cfg = cfg or load_config()
    missing = [c for c in REQUIRED_COLUMNS if c not in train_df.columns]
    if missing:
        raise ElasticityError(f"elasticity estimation requires columns {missing}")

    ecfg = cfg.require("elasticity")
    pcfg = cfg.require("pricing_response")
    d = _controls(train_df)

    # A. pooled controlled elasticity (UPC x store FE + controls)
    pooled_fit = fit_loglog(
        d,
        name="pooled controlled (UPC x store FE + promo/season/trend)",
        absorb=["upc", "store"],
        controls=["recorded_promotion_flag", "sin52", "cos52", "trend"],
        cluster_col="upc",
        sample_size=1_200_000,
        seed=cfg.seed,
    )
    # B. plain UPC x store fixed-effects specification (no extra controls)
    fe_fit = fit_loglog(
        d,
        name="UPC x store fixed effects",
        absorb=["upc", "store"],
        cluster_col="upc",
        sample_size=1_200_000,
        seed=cfg.seed,
    )

    # A'. the same pooled design, with every covariance assumption, so the
    #      published precision is not the conventional one. See
    #      reports/15_ELASTICITY_INFERENCE_AUDIT.md.
    robust = _pooled_robust_inference(d, sample_size=1_200_000, seed=cfg.seed)

    # C. per-UPC estimates
    products = estimate_per_upc(
        d,
        min_obs=int(ecfg["min_obs_per_upc"]),
        min_distinct_prices=int(ecfg["min_distinct_prices_per_upc"]),
    )

    pooled = float(pooled_fit.price_elasticity)
    max_se = float(ecfg.get("max_se_for_product_estimate", 1.5))

    # Reject imprecise or wrong-signed products before shrinking: a positive
    # coefficient is evidence of endogeneity, not of upward-sloping demand.
    usable = (
        products["usable"]
        & products["std_error"].le(max_se)
        & products["elasticity_raw"].lt(0)
    )
    products["usable_for_pricing"] = usable
    products.loc[products["usable"] & ~usable, "reject_reason"] = np.where(
        products.loc[products["usable"] & ~usable, "elasticity_raw"] >= 0,
        "wrong_sign",
        "standard_error_too_large",
    )

    est = np.where(usable, products["elasticity_raw"], np.nan)
    se = np.where(usable, products["std_error"], np.nan)
    shrinkage = empirical_bayes_shrinkage(
        est, se, pooled,
        method=str(ecfg.get("shrinkage_tau2_estimator", "reml")),
        prior_mean=str(ecfg.get("shrinkage_prior_mean", "pooled")),
    )
    shrunk, weights, tau2 = shrinkage.shrunk, shrinkage.weights, shrinkage.tau2

    lo, hi = float(pcfg["min_abs_elasticity"]), float(pcfg["max_abs_elasticity"])
    clipped = -np.clip(np.abs(shrunk), lo, hi)

    products["shrinkage_weight"] = weights
    products["elasticity_shrunk"] = shrunk
    products["elasticity_final"] = clipped
    products["elasticity_source"] = np.where(usable, "shrunk_product", "pooled_fallback")
    products["clipped"] = ~np.isclose(clipped, shrunk, atol=1e-9)

    metadata = {
        "pooled_elasticity": pooled,
        "pooled_se": float(pooled_fit.std_error),
        "pooled_ci_low": float(pooled_fit.ci_low),
        "pooled_ci_high": float(pooled_fit.ci_high),
        "pooled_n_obs": int(pooled_fit.n_obs),
        "fe_elasticity": float(fe_fit.price_elasticity),
        "fe_se": float(fe_fit.std_error),
        "fe_n_obs": int(fe_fit.n_obs),
        "tau2_between_product_variance": tau2,
        **{f"shrinkage_{k}": v for k, v in shrinkage.as_dict().items()},
        "pooled_se_two_way_cluster": robust["two_way_se"],
        "pooled_ci_low_two_way": robust["two_way_ci_low"],
        "pooled_ci_high_two_way": robust["two_way_ci_high"],
        "pooled_se_hc1": robust["hc1_se"],
        "median_per_upc_se_inflation_vs_hc1": float(
            products.loc[usable, "se_inflation_vs_hc1"].median()
        ) if usable.any() else None,
        "n_products": int(len(products)),
        "n_products_usable": int(usable.sum()),
        "mean_shrinkage_weight": float(np.nanmean(weights[usable])) if usable.any() else 0.0,
        "training_weeks_low": training_weeks[0] if training_weeks else None,
        "training_weeks_high": training_weeks[1] if training_weeks else None,
        "n_training_rows": int(len(train_df)),
        "shrinkage": str(ecfg.get("shrinkage", "empirical_bayes")),
        "clip_band": f"[{lo}, {hi}]",
    }
    return ElasticityTable(
        pooled=float(-min(max(abs(pooled), lo), hi)),
        pooled_se=float(pooled_fit.std_error),
        products=products,
        metadata=metadata,
    )
