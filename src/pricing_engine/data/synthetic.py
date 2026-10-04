"""Synthetic Dominick's-shaped panel with a KNOWN price response.

Two jobs:

* **Public demo.** The Dominick's licence forbids redistributing the panel, so
  the hosted demo and the CI end-to-end tests run the unchanged pipeline on
  data generated here (``configs/demo.yaml``). Every surface that shows it is
  labelled synthetic.
* **Ground-truth study.** Because the true elasticities and costs are known,
  ``scripts/ground_truth_study.py`` can measure how far the engine's estimates
  and prices land from the truth under controlled confounding - the one test
  of the estimator that observational data cannot supply.

Data-generating process (one row per UPC x store x week)::

    log mu = b_i + a_s + e_i * log(p / p_ref_i) + lift * P + S_t + g_t + u_ist
    move   ~ Poisson(mu)

* ``e_i``    true own-price elasticity of product ``i`` (all elastic, < -1)
* ``a_s``    store demand level; with ``store_confounding > 0`` high-demand
             stores also charge more (removed by store fixed effects)
* ``P``      promotion: a price cut AND a display lift; only a share
             ``promo_record_rate`` of promotions carries a sale code, so the
             rest is an unrecorded confounder
* ``S_t``    seasonality; promotions cluster in high season when
             ``promo_seasonality > 0``
* ``u_ist``  AR(1) latent demand shock; with ``price_endogeneity != 0`` the
             retailer partly prices on it, which no control can remove

Costs follow an exogenous wholesale path with occasional steps, and the
accounting margin in ``profit`` is computed from the true cost, so the
pipeline's AAC proxy equals the true cost here (it does not on real data).

The truth tables are written to a separate directory that no pipeline step
reads.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from pricing_engine.optimization.attribution import analytic_unconstrained_optimum
from pricing_engine.utils.io import ensure_dir, sha256_file, utc_now

GENERATOR_VERSION = "1.0"

#: Synthetic ids sit far outside the Dominick's ranges so the two can never mix.
UPC_BASE = 9_900_000_000
STORE_BASE = 900

BRANDS = ("NORTHFIELD", "SUNVALE", "GOLDEN ACRE", "BRIGHTMORN", "MEADOWLARK", "PINE RIDGE")
PRODUCT_TYPES = (
    "OAT RINGS", "CORN FLAKES", "HONEY CLUSTERS", "BRAN CRUNCH", "RICE PUFFS",
    "FROSTED SQUARES", "GRANOLA BITES", "WHEAT BISCUITS", "FRUIT LOOPS", "COCOA STARS",
    "RAISIN BRAN", "MUESLI", "PUFFED WHEAT", "CINNAMON CRISPS", "MULTIGRAIN O'S",
)
RECORDED_SALE_CODES = ("B", "C", "S")


@dataclass(frozen=True)
class SyntheticSpec:
    """Every knob of the data-generating process. Defaults = the public demo."""

    n_upcs: int = 20
    n_stores: int = 15
    n_weeks: int = 200
    seed: int = 7

    # true own-price elasticities (all elastic, so a finite profit optimum exists)
    elasticity_mean: float = -2.3
    elasticity_sd: float = 0.6
    elasticity_bounds: tuple[float, float] = (-4.0, -1.2)

    n_brands: int = 4
    sizes_oz: tuple[int, ...] = (10, 12, 15, 18, 20)
    base_log_demand: float = 2.6
    base_log_demand_sd: float = 0.5
    store_sd: float = 0.3
    markup_range: tuple[float, float] = (1.25, 1.45)

    # regular-price movement: cost pass-through plus occasional short price tests
    cost_step_prob: float = 0.04
    cost_step_mean: float = 0.03
    cost_step_sd: float = 0.03
    price_test_prob: float = 0.20
    price_test_range: tuple[float, float] = (0.03, 0.10)

    # promotions
    promo_rate: float = 0.12
    promo_depth: tuple[float, float] = (0.10, 0.30)
    promo_lift: float = 0.35
    promo_record_rate: float = 1.0
    promo_seasonality: float = 0.5

    # confounding
    store_confounding: float = 0.5
    price_endogeneity: float = 0.0

    # demand dynamics
    seasonal_amplitude: float = 0.15
    week_shock_sd: float = 0.10
    latent_ar: float = 0.5
    latent_sd: float = 0.15

    # realism: late-entering series, bundles, suspect rows
    late_entry_share: float = 0.2
    multipack_share: float = 0.03
    bad_row_share: float = 0.005


@dataclass
class SyntheticPanel:
    movement: pd.DataFrame          # Dominick's raw movement schema
    upc_meta: pd.DataFrame          # Dominick's raw UPC metadata schema
    truth: pd.DataFrame             # per-UPC true parameters
    cost_path: pd.DataFrame         # upc, store, week, true_unit_cost, latent shock
    spec: SyntheticSpec = field(default_factory=SyntheticSpec)


def _round_to_99(prices: np.ndarray) -> np.ndarray:
    """Retail-style regular prices ending in 9 (e.g. 3.49), never below $0.19."""
    return np.maximum(np.floor(prices * 10.0) / 10.0 + 0.09, 0.19)


def generate_panel(spec: SyntheticSpec | None = None) -> SyntheticPanel:
    """Generate one deterministic panel (same spec and seed, same bytes)."""
    spec = spec or SyntheticSpec()
    rng = np.random.default_rng(spec.seed)
    n_u, n_s, n_w = spec.n_upcs, spec.n_stores, spec.n_weeks

    # -- products ------------------------------------------------------------
    upcs = UPC_BASE + np.arange(1, n_u + 1)
    lo, hi = spec.elasticity_bounds
    elasticity = np.clip(rng.normal(spec.elasticity_mean, spec.elasticity_sd, n_u), lo, hi)
    brand_idx = rng.integers(0, min(spec.n_brands, len(BRANDS)), n_u)
    size_oz = rng.choice(np.asarray(spec.sizes_oz), n_u)
    base_log_q = rng.normal(spec.base_log_demand, spec.base_log_demand_sd, n_u)
    p_ref = np.round(0.19 * size_oz * rng.uniform(0.85, 1.20, n_u), 2)
    markup = rng.uniform(*spec.markup_range, n_u)
    cost0 = p_ref / markup

    # -- stores --------------------------------------------------------------
    stores = STORE_BASE + np.arange(1, n_s + 1)
    store_level = rng.normal(0.0, spec.store_sd, n_s)
    # High-demand stores charge more when store_confounding > 0.
    store_price_index = np.exp(0.15 * spec.store_confounding * store_level / max(spec.store_sd, 1e-9))

    # -- weeks ---------------------------------------------------------------
    weeks = np.arange(1, n_w + 1)
    season = spec.seasonal_amplitude * np.sin(2.0 * np.pi * weeks / 52.0)
    week_shock = rng.normal(0.0, spec.week_shock_sd, n_w)

    # -- wholesale cost path per product (shared by all stores) ---------------
    steps = rng.random((n_u, n_w)) < spec.cost_step_prob
    step_size = rng.normal(spec.cost_step_mean, spec.cost_step_sd, (n_u, n_w))
    log_cost = np.log(cost0)[:, None] + np.cumsum(np.where(steps, step_size, 0.0), axis=1)
    cost = np.exp(log_cost)                                           # (u, w)

    # -- latent AR(1) demand shock per series ---------------------------------
    latent = np.empty((n_u, n_s, n_w))
    latent[:, :, 0] = rng.normal(0.0, spec.latent_sd, (n_u, n_s))
    innov_sd = spec.latent_sd * np.sqrt(max(1.0 - spec.latent_ar**2, 1e-9))
    for t in range(1, n_w):
        latent[:, :, t] = spec.latent_ar * latent[:, :, t - 1] + rng.normal(0.0, innov_sd, (n_u, n_s))

    # -- regular price: cost pass-through x store index, short price tests ----
    regular = _round_to_99(
        cost[:, None, :] * markup[:, None, None] * store_price_index[None, :, None]
    )
    tests = rng.random((n_u, n_s, n_w)) < spec.price_test_prob
    test_dir = rng.choice([-1.0, 1.0], (n_u, n_s, n_w))
    test_size = rng.uniform(*spec.price_test_range, (n_u, n_s, n_w))
    shelf = np.where(tests, regular * (1.0 + test_dir * test_size), regular)

    # -- promotions (cluster in high season) ----------------------------------
    promo_prob = spec.promo_rate * (1.0 + spec.promo_seasonality * np.sin(2.0 * np.pi * weeks / 52.0))
    promo = rng.random((n_u, n_s, n_w)) < np.clip(promo_prob, 0.0, 1.0)[None, None, :]
    depth = rng.uniform(*spec.promo_depth, (n_u, n_s, n_w))
    price = np.where(promo, shelf * (1.0 - depth), shelf)
    # Endogenous pricing: the retailer partly sees the latent shock.
    price = price * np.exp(-spec.price_endogeneity * latent)
    price = np.round(np.maximum(price, 0.10), 2)

    # -- demand ----------------------------------------------------------------
    log_mu = (
        base_log_q[:, None, None]
        + store_level[None, :, None]
        + elasticity[:, None, None] * np.log(price / p_ref[:, None, None])
        + spec.promo_lift * promo
        + (season + week_shock)[None, None, :]
        + latent
    )
    move = rng.poisson(np.exp(np.minimum(log_mu, 12.0))).astype(float)

    true_cost = np.broadcast_to(cost[:, None, :], price.shape)
    profit = (price - true_cost) / price * 100.0

    # -- recorded promotion codes (only a share of promotions is recorded) ------
    recorded = promo & (rng.random(promo.shape) < spec.promo_record_rate)
    code_idx = rng.integers(0, len(RECORDED_SALE_CODES), promo.shape)
    sale = np.where(recorded, np.asarray(RECORDED_SALE_CODES)[code_idx], None)

    # -- flatten ----------------------------------------------------------------
    U, S, W = np.meshgrid(np.arange(n_u), np.arange(n_s), np.arange(n_w), indexing="ij")
    frame = pd.DataFrame(
        {
            "store": stores[S.ravel()].astype("int32"),
            "upc": upcs[U.ravel()].astype("int64"),
            "week": weeks[W.ravel()].astype("int32"),
            "move": move.ravel(),
            "qty": 1.0,
            "price": price.ravel(),
            "sale": sale.ravel(),
            "profit": np.round(profit.ravel(), 2),
            "ok": 1,
        }
    )
    cost_path = pd.DataFrame(
        {
            "upc": frame["upc"],
            "store": frame["store"],
            "week": frame["week"],
            "true_unit_cost": true_cost.ravel(),
            "true_promotion": promo.ravel().astype("int8"),
            "latent_shock": latent.ravel(),
        }
    )

    # Late-entering series: drop their early weeks (thin history -> higher risk).
    n_series = n_u * n_s
    late = rng.random(n_series) < spec.late_entry_share
    entry_week = np.where(late, rng.integers(int(0.3 * n_w), int(0.7 * n_w) + 1, n_series), 1)
    series_entry = pd.DataFrame(
        {
            "upc": np.repeat(upcs, n_s),
            "store": np.tile(stores, n_u),
            "entry_week": entry_week,
        }
    )
    frame = frame.merge(series_entry, on=["upc", "store"], how="left")
    keep = frame["week"] >= frame["entry_week"]
    frame = frame.loc[keep].drop(columns="entry_week").reset_index(drop=True)
    cost_path = cost_path.loc[keep.to_numpy()].reset_index(drop=True)

    # Bundles (qty 2 at twice the unit price) and suspect rows (ok = 0).
    bundle = rng.random(len(frame)) < spec.multipack_share
    frame.loc[bundle, "qty"] = 2.0
    frame.loc[bundle, "price"] = np.round(frame.loc[bundle, "price"] * 2.0, 2)
    frame.loc[rng.random(len(frame)) < spec.bad_row_share, "ok"] = 0

    # -- metadata and truth -------------------------------------------------------
    names = [
        f"{BRANDS[b]} {PRODUCT_TYPES[i % len(PRODUCT_TYPES)]}"
        for i, b in enumerate(brand_idx)
    ]
    upc_meta = pd.DataFrame(
        {
            "com_code": 9999,
            "upc": upcs.astype("int64"),
            "descrip": names,
            "size": [f"{s} OZ" for s in size_oz],
            "case": 12,
            "nitem": np.arange(1, n_u + 1),
        }
    )
    truth = pd.DataFrame(
        {
            "upc": upcs.astype("int64"),
            "descrip": names,
            "brand": [BRANDS[b] for b in brand_idx],
            "size_oz": size_oz,
            "true_elasticity": elasticity,
            "base_log_demand": base_log_q,
            "reference_price": p_ref,
            "markup": markup,
            "initial_unit_cost": cost0,
        }
    )
    return SyntheticPanel(frame, upc_meta, truth, cost_path, spec)


def oracle_price(cost: float | np.ndarray, elasticity: float | np.ndarray) -> np.ndarray:
    """Profit-maximising price under the synthetic constant-elasticity truth.

    ``p* = c * e / (1 + e)`` - the closed form the attribution audit already
    uses (:func:`analytic_unconstrained_optimum`), vectorised.
    """
    fn = np.vectorize(analytic_unconstrained_optimum, otypes=[float])
    return fn(np.asarray(cost, dtype=float), np.asarray(elasticity, dtype=float))


def write_panel(
    panel: SyntheticPanel,
    raw_dir: str | Path,
    truth_dir: str | Path,
    *,
    movement_file: str = "wcer.csv",
    upc_file: str = "upccer.csv",
) -> dict:
    """Write the raw CSVs (pipeline input) and the truth (never read by it)."""
    raw_dir, truth_dir = ensure_dir(Path(raw_dir)), ensure_dir(Path(truth_dir))
    movement = panel.movement.rename(columns=str.upper)
    meta = panel.upc_meta.rename(columns=str.upper)
    movement.to_csv(raw_dir / movement_file, index=False, encoding="latin-1")
    meta.to_csv(raw_dir / upc_file, index=False, encoding="latin-1")

    source = {
        "source": "synthetic",
        "description": "Generated by pricing_engine.data.synthetic - NOT Dominick's data.",
        "generator_version": GENERATOR_VERSION,
        "generated_at_utc": utc_now(),
        "spec": asdict(panel.spec),
        "files": {
            name: {
                "rows": rows,
                "bytes": (raw_dir / name).stat().st_size,
                "sha256": sha256_file(raw_dir / name),
                "archive_member": "generated",
            }
            for name, rows in ((movement_file, len(panel.movement)), (upc_file, len(panel.upc_meta)))
        },
    }
    (raw_dir / "SOURCE.json").write_text(json.dumps(source, indent=2), encoding="utf-8")

    panel.truth.to_csv(truth_dir / "truth_products.csv", index=False)
    panel.cost_path.to_parquet(truth_dir / "truth_cost_path.parquet", index=False)
    (truth_dir / "spec.json").write_text(json.dumps(asdict(panel.spec), indent=2), encoding="utf-8")
    return source
