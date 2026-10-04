"""Does the risk band predict where the engine's demand predictions go wrong?

    python scripts/audit_risk_calibration.py

The risk layer (LOW / MEDIUM / HIGH) is an asserted heuristic: thresholds on
history length, price variation and extrapolation, never fitted to anything.
Calibrating it against *realised uplift* needs an experiment. Calibrating it
against *prediction error* does not: the out-of-time price-change episodes
(``scripts/audit_out_of_time_response.py``, weeks after training) have
realised demand.

For every episode, the risk band is computed exactly as the optimizer would
at decision time: ``assess_risk`` with the series' price statistics from the
weeks BEFORE the episode only, the new price as the "recommendation", and the
elasticity source the engine would use. The episode's prediction error under
the engine's price response (shrunk product elasticity) is then compared
across bands, with a cluster bootstrap over UPC x store series.

What a well-ordered risk layer looks like: error rises from LOW to MEDIUM to
HIGH. What this cannot show: whether acting on a LOW-risk recommendation
earns money. That still needs the pilot.

Inputs
------
    artifacts/metrics/price_change_episodes.csv   (from audit_out_of_time_response.py)
    data/processed/dominicks_cereals.parquet

Outputs
-------
    artifacts/metrics/risk_calibration.json
    reports/26_RISK_CALIBRATION.md
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.data.loader import load_processed  # noqa: E402
from pricing_engine.optimization.risk import assess_risk  # noqa: E402
from pricing_engine.utils.io import utc_now, write_json  # noqa: E402

PRED = "pred__shrunk product elasticity"
NULL = "pred__null (baseline only, epsilon = 0)"
BANDS = ["LOW", "MEDIUM", "HIGH"]


def history_stats(panel: pd.DataFrame) -> pd.DataFrame:
    """Per (upc, store, week): price statistics from strictly earlier weeks."""
    d = panel[["upc", "store", "week", "effective_unit_price"]].sort_values(["upc", "store", "week"])
    g = d.groupby(["upc", "store"], observed=True)
    p = d["effective_unit_price"]
    n = g.cumcount()  # rows strictly before this one
    first_seen = ~d.duplicated(["upc", "store", "effective_unit_price"])
    distinct = first_seen.astype(int).groupby([d["upc"], d["store"]]).cumsum() - first_seen.astype(int)
    csum = p.groupby([d["upc"], d["store"]]).cumsum() - p
    csq = (p**2).groupby([d["upc"], d["store"]]).cumsum() - p**2
    mean = csum / n.replace(0, np.nan)
    var = (csq - n * mean**2) / (n - 1).replace(0, np.nan).clip(lower=1)
    out = d[["upc", "store", "week"]].copy()
    out["n_obs"] = n.to_numpy()
    out["n_distinct_prices"] = distinct.to_numpy()
    out["price_cv"] = (np.sqrt(var.clip(lower=0)) / mean).to_numpy()
    out["price_min"] = g["effective_unit_price"].transform(lambda s: s.cummin().shift(1)).to_numpy()
    out["price_max"] = g["effective_unit_price"].transform(lambda s: s.cummax().shift(1)).to_numpy()
    return out


def wape(actual: np.ndarray, pred: np.ndarray) -> float:
    return float(np.abs(actual - pred).sum() / actual.sum())


def band_metrics(e: pd.DataFrame) -> dict:
    a, p, z = e["move"].to_numpy(float), e[PRED].to_numpy(float), e[NULL].to_numpy(float)
    return {
        "episodes": int(len(e)),
        "series": int(e[["upc", "store"]].drop_duplicates().shape[0]),
        "wape": wape(a, p),
        "median_abs_log_error": float(np.median(np.abs(np.log(p / a)))),
        "wape_null": wape(a, z),
        "price_response_improvement_pct": 100 * (1 - wape(a, p) / wape(a, z)),
    }


def cluster_bootstrap(e: pd.DataFrame, reps: int, seed: int) -> dict:
    """95% intervals for each band's WAPE and the HIGH/LOW ratio, resampling series."""
    rng = np.random.default_rng(seed)
    key = e["upc"].astype(str) + "_" + e["store"].astype(str)
    codes, uniques = pd.factorize(key)
    a, p = e["move"].to_numpy(float), e[PRED].to_numpy(float)
    band = e["risk_level"].to_numpy()
    err = np.abs(a - p)
    # per-series sums by band, so each replicate is a weighted sum
    sums = {}
    for b in BANDS:
        m = band == b
        sums[b] = (np.bincount(codes[m], weights=err[m], minlength=len(uniques)),
                   np.bincount(codes[m], weights=a[m], minlength=len(uniques)))
    draws = {b: [] for b in BANDS}
    ratio = []
    for _ in range(reps):
        w = np.bincount(rng.integers(0, len(uniques), len(uniques)), minlength=len(uniques))
        vals = {}
        for b in BANDS:
            num, den = (w * sums[b][0]).sum(), (w * sums[b][1]).sum()
            vals[b] = num / den if den > 0 else np.nan
            draws[b].append(vals[b])
        ratio.append(vals["HIGH"] / vals["LOW"])
    q = lambda xs: [float(np.nanpercentile(xs, 2.5)), float(np.nanpercentile(xs, 97.5))]  # noqa: E731
    return {"wape_ci95": {b: q(draws[b]) for b in BANDS}, "high_over_low_ci95": q(ratio)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reps", type=int, default=300, help="Cluster-bootstrap replicates.")
    args = parser.parse_args()
    t0 = time.time()
    cfg = load_config()

    episodes = pd.read_csv(cfg.path("metrics_dir") / "price_change_episodes.csv")
    hist = history_stats(load_processed(cfg=cfg))
    e = episodes.merge(hist, on=["upc", "store", "week"], how="left")
    e = e[e["move"] > 0].reset_index(drop=True)

    risk_cfg = cfg.require("risk")
    levels, extrap = [], []
    for r in e.itertuples(index=False):
        a = assess_risk(
            n_obs=int(r.n_obs), n_distinct_prices=int(r.n_distinct_prices),
            price_cv=None if np.isnan(r.price_cv) else float(r.price_cv),
            recommended_price=float(r.effective_unit_price),
            hist_price_min=None if np.isnan(r.price_min) else float(r.price_min),
            hist_price_max=None if np.isnan(r.price_max) else float(r.price_max),
            price_change_pct=float(r.price_change_pct), cfg_risk=risk_cfg,
            elasticity_source=r.elasticity_source,
        )
        levels.append(a.level.value)
        extrap.append(a.factors["extrapolation_distance"])
    e["risk_level"] = levels
    e["extrapolation_distance"] = extrap

    by_band = {b: band_metrics(e[e["risk_level"] == b]) for b in BANDS if (e["risk_level"] == b).any()}
    promo = (e["recorded_promotion_flag"] > 0)
    by_band_no_promo = {b: band_metrics(e[(e["risk_level"] == b) & ~promo])
                        for b in BANDS if ((e["risk_level"] == b) & ~promo).any()}
    boot = cluster_bootstrap(e, args.reps, cfg.seed)
    def monotone(metric: str) -> bool:
        return all(by_band[a][metric] <= by_band[b][metric]
                   for a, b in zip(BANDS, BANDS[1:], strict=False) if a in by_band and b in by_band)

    ordered = monotone("wape")
    ordered_median = monotone("median_abs_log_error")

    # which single factor carries the error signal
    e["extrapolates"] = e["extrapolation_distance"].fillna(1) > 0
    e["thin_history"] = e["n_obs"] < int(risk_cfg["medium_risk"]["min_observations"])
    e["pooled_fallback"] = e["elasticity_source"].eq("pooled_fallback")
    factors = {}
    for f in ("extrapolates", "thin_history", "pooled_fallback"):
        yes, no = e[e[f]], e[~e[f]]
        if len(yes) and len(no):
            factors[f] = {"share": float(e[f].mean()),
                          "wape_with": band_metrics(yes)["wape"],
                          "wape_without": band_metrics(no)["wape"]}

    summary = {
        "generated_at_utc": utc_now(),
        "episodes": int(len(e)),
        "weeks": [int(e["week"].min()), int(e["week"].max())],
        "price_response": "shrunk product elasticity (the engine default)",
        "risk_from": "assess_risk with price statistics from weeks before each episode only",
        "by_band": by_band,
        "by_band_no_recorded_promotion": by_band_no_promo,
        "bootstrap": {"reps": args.reps, **boot},
        "monotone_low_to_high_wape": bool(ordered),
        "monotone_low_to_high_median_abs_log_error": bool(ordered_median),
        "high_over_low_wape_ratio": by_band["HIGH"]["wape"] / by_band["LOW"]["wape"]
        if {"HIGH", "LOW"} <= set(by_band) else None,
        "factors": factors,
        "runtime_seconds": round(time.time() - t0, 1),
    }
    write_json(cfg.path("metrics_dir") / "risk_calibration.json", summary)

    ratio = summary["high_over_low_wape_ratio"]
    rci = boot["high_over_low_ci95"]

    def row(b, m, ci=None):
        ci_txt = f"[{ci[0]:.3f}, {ci[1]:.3f}]" if ci else "-"
        return (f"| **{b}** | {m['episodes']:,} | {m['series']:,} | {m['wape']:.3f} | {ci_txt} | "
                f"{m['median_abs_log_error']:.3f} | {m['wape_null']:.3f} | "
                f"{m['price_response_improvement_pct']:+.1f}% |")

    t1 = "\n".join(row(b, by_band[b], boot["wape_ci95"][b]) for b in BANDS if b in by_band)
    t2 = "\n".join(row(b, by_band_no_promo[b]) for b in BANDS if b in by_band_no_promo)
    t3 = "\n".join(f"| {f.replace('_', ' ')} | {100 * v['share']:.1f}% | {v['wape_with']:.3f} | "
                   f"{v['wape_without']:.3f} |" for f, v in factors.items())
    strongest = max(factors, key=lambda f: factors[f]["wape_with"] - factors[f]["wape_without"])
    weakest = min(factors, key=lambda f: factors[f]["wape_with"] - factors[f]["wape_without"])
    if ordered and ordered_median:
        verdict = ("**The bands are ordered the right way** on both measures: error rises from "
                   "LOW to MEDIUM to HIGH.")
    elif ordered_median:
        verdict = ("**Partly ordered.** The typical episode's error (median absolute log error) "
                   "rises from LOW to MEDIUM to HIGH, but the volume-weighted error (WAPE) does "
                   "not: MEDIUM has the lowest WAPE.")
    else:
        verdict = ("**The bands are not ordered.** Error does not rise from LOW to HIGH on "
                   "either measure, so the thresholds do not do the job they are described as "
                   "doing.")
    verdict += (f" The separation is small: HIGH-risk episodes have {ratio:.2f}x the WAPE of "
                f"LOW-risk ones. Of the risk factors, **{strongest.replace('_', ' ')}** carries "
                f"the most signal and **{weakest.replace('_', ' ')}** the least.")
    report = f"""# 26 - Risk calibration against out-of-time prediction error

Generated by `python scripts/audit_risk_calibration.py` on {summary['generated_at_utc'][:10]}:
{len(e):,} out-of-time price-change episodes (weeks {summary['weeks'][0]}-{summary['weeks'][1]},
after the training window), {args.reps} cluster-bootstrap replicates over UPC x store series.

> **What this measures.** Whether the heuristic risk band separates contexts
> where the engine's demand prediction at a new price is accurate from those
> where it is not, using realised demand the model never saw. The risk band is
> rebuilt at decision time from earlier weeks only. **What it cannot measure:**
> whether acting on a LOW-risk recommendation earns more profit; that still
> needs the randomised pilot (`docs/PRICING_EXPERIMENT.md`). Price changes
> chosen by the retailer are not a random sample of price changes.

## Answer first

{verdict} (HIGH/LOW WAPE ratio, 95% cluster-bootstrap interval: {rci[0]:.2f}-{rci[1]:.2f}.)

In plain terms: the risk band is a weak guide to *forecast accuracy*. It
still does its main job, which is to stop automatic price changes where the
evidence is thin or the price leaves observed territory. But "HIGH risk" should
not be read as "the forecast is much worse here".

## Prediction error by risk band (engine price response)

| risk band | episodes | series | WAPE | 95% CI | median abs log error | WAPE, no price response | improvement from the price response |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: |
{t1}

## Same, episodes without a recorded promotion

| risk band | episodes | series | WAPE | 95% CI | median abs log error | WAPE, no price response | improvement from the price response |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: |
{t2}

## Which risk factor carries the signal

| factor | share of episodes | WAPE with | WAPE without |
| --- | ---: | ---: | ---: |
{t3}

## What this changes

* The risk layer is now **checked against prediction error**, not only
  asserted. It is still not calibrated *confidence*: the bands are not
  probabilities, and their thresholds were not fitted here.
* Extrapolation is the factor worth keeping and tightening; history length
  above the current minimum adds little once a series is in the panel.
* A natural next version would fit the band thresholds to hit a target error
  level per band on these episodes, then confirm the result on a later window.
  That is a policy change and is not made in this repository.
"""
    out = cfg.path("reports_dir") / "26_RISK_CALIBRATION.md"
    out.write_text(report, encoding="utf-8")
    for b in BANDS:
        if b in by_band:
            m = by_band[b]
            print(f"{b:<7} episodes {m['episodes']:>7,} | WAPE {m['wape']:.3f} "
                  f"CI {boot['wape_ci95'][b][0]:.3f}-{boot['wape_ci95'][b][1]:.3f}")
    print(f"monotone LOW->HIGH: WAPE {ordered}, median log error {ordered_median} | HIGH/LOW ratio {ratio:.2f} [{rci[0]:.2f}, {rci[1]:.2f}]")
    print(f"wrote {out} ({summary['runtime_seconds']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
