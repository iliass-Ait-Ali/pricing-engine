"""Phase E - train and compare demand models on a strict temporal split.

    python scripts/train.py
    python scripts/train.py --sample 800000     # faster iteration
    python scripts/train.py --max-iter 150

Outputs
-------
    artifacts/models/demand_model.joblib
    artifacts/models/demand_model_metadata.json
    artifacts/metrics/model_metrics.json
    reports/04_MODEL_COMPARISON.md
    artifacts/figures/model_*.png
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.features.build import temporal_split, training_frame  # noqa: E402
from pricing_engine.models.baselines import ALL_BASELINES  # noqa: E402
from pricing_engine.models.demand_model import DemandModel, ModelMetadata  # noqa: E402
from pricing_engine.models.metrics import evaluate, evaluate_by  # noqa: E402
from pricing_engine.utils.io import (  # noqa: E402
    ensure_dir,
    environment_record,
    read_json,
    set_seed,
    utc_now,
    write_json,
)

plt.rcParams.update({"figure.dpi": 120, "figure.autolayout": True, "font.size": 9})


def fmt(v: float) -> str:
    return f"{v:,.3f}"


def metrics_row(name: str, m: dict) -> str:
    return (
        f"| {name} | {m['mae']:.3f} | {m['rmse']:.3f} | {m['wape']:.4f} | "
        f"{m['bias']:+.3f} | {m['n']:,} |"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=None, help="Subsample training rows.")
    parser.add_argument("--max-iter", type=int, default=None, help="Override HGB max_iter.")
    args = parser.parse_args()

    cfg = load_config()
    set_seed(cfg.seed)
    t0 = time.time()

    feats = pd.read_parquet(cfg.path("features_table"))
    usable = training_frame(feats)
    train, valid, test, split_info = temporal_split(usable, cfg=cfg)
    print(f"train {len(train):,} | valid {len(valid):,} | test {len(test):,}")
    print(f"weeks: {split_info['train_weeks']} {split_info['valid_weeks']} {split_info['test_weeks']}")

    train_fit = train
    if args.sample and len(train) > args.sample:
        train_fit = train.sample(args.sample, random_state=cfg.seed)
        print(f"training on a random subsample of {len(train_fit):,} rows")

    results: dict[str, dict] = {}
    predictions: dict[str, np.ndarray] = {}

    # -- baselines -----------------------------------------------------------
    for cls in ALL_BASELINES:
        b = cls().fit(train)
        pv = b.predict(valid)
        results[b.name] = {
            "kind": "baseline",
            "price_aware": False,
            "valid": evaluate(valid["move"], pv),
        }
        print(f"[baseline] {b.name:32s} valid WAPE={results[b.name]['valid']['wape']:.4f}")

    # -- interpretable regression -------------------------------------------
    t = time.time()
    ridge = DemandModel("ridge_loglog", cfg=cfg, name="M1 ridge log-log")
    ridge.fit(train_fit)
    pv = ridge.predict(valid)
    results[ridge.name] = {
        "kind": ridge.kind,
        "price_aware": True,
        "valid": evaluate(valid["move"], pv),
        "raw_log_price_coefficient": ridge.raw_price_coefficient(),
        "implied_elasticity": ridge.implied_elasticity(valid),
        "fit_seconds": round(time.time() - t, 1),
    }
    predictions[ridge.name] = pv
    print(
        f"[model   ] {ridge.name:32s} valid WAPE={results[ridge.name]['valid']['wape']:.4f} "
        f"({results[ridge.name]['fit_seconds']}s, implied elasticity="
        f"{results[ridge.name]['implied_elasticity']['median']:.3f})"
    )

    # -- gradient boosting ---------------------------------------------------
    t = time.time()
    hgb_params = {"max_iter": args.max_iter} if args.max_iter else {}
    hgb = DemandModel("hgb_poisson", cfg=cfg, name="M2 HGB poisson", params=hgb_params)
    hgb.fit(train_fit)
    pv = hgb.predict(valid)
    results[hgb.name] = {
        "kind": hgb.kind,
        "price_aware": True,
        "valid": evaluate(valid["move"], pv),
        "fit_seconds": round(time.time() - t, 1),
        "params": {k: v for k, v in hgb.estimator.get_params().items() if isinstance(v, (int, float, str, bool, type(None)))},
        "implied_elasticity": hgb.implied_elasticity(valid),
    }
    predictions[hgb.name] = pv
    print(
        f"[model   ] {hgb.name:32s} valid WAPE={results[hgb.name]['valid']['wape']:.4f} "
        f"({results[hgb.name]['fit_seconds']}s, implied elasticity="
        f"{results[hgb.name]['implied_elasticity']['median']:.3f})"
    )

    # -- selection -----------------------------------------------------------
    # Only price-aware models are eligible: a price-blind baseline cannot serve
    # a pricing engine even if its forecast accuracy is competitive.
    candidates = {k: v for k, v in results.items() if v.get("price_aware")}
    selected_name = min(candidates, key=lambda k: candidates[k]["valid"]["wape"])
    selected = ridge if selected_name == ridge.name else hgb
    print(f"\nselected on validation WAPE: {selected_name}")

    # -- final test evaluation (scored once) --------------------------------
    test_pred = selected.predict(test)
    test_metrics = evaluate(test["move"], test_pred)
    results[selected_name]["test"] = test_metrics
    print(f"test  WAPE={test_metrics['wape']:.4f} MAE={test_metrics['mae']:.3f} RMSE={test_metrics['rmse']:.3f}")

    # Baselines on the same test window, for context.
    baseline_test = {}
    for cls in ALL_BASELINES:
        b = cls().fit(pd.concat([train, valid]))
        baseline_test[b.name] = evaluate(test["move"], b.predict(test))
    for name, m in baseline_test.items():
        results[name]["test"] = m

    # -- segment analysis ----------------------------------------------------
    seg = test.copy()
    seg["pred"] = test_pred
    seg["promo_state"] = np.where(seg["recorded_promotion_flag"] == 1, "recorded promo", "no recorded promo")
    var = pd.read_csv(cfg.path("metrics_dir") / "price_variation_upc_store.csv")
    seg = seg.merge(var[["upc", "store", "price_cv", "eligible"]], on=["upc", "store"], how="left")
    seg["price_variation_bucket"] = pd.cut(
        seg["price_cv"], [-0.001, 0.05, 0.10, 0.15, 1.0],
        labels=["cv<=0.05", "0.05-0.10", "0.10-0.15", ">0.15"],
    )

    by_store = evaluate_by(seg, "move", "pred", "store", top=10)
    by_upc = evaluate_by(seg, "move", "pred", "upc", top=10)
    by_promo = evaluate_by(seg, "move", "pred", "promo_state")
    by_var = evaluate_by(seg, "move", "pred", "price_variation_bucket")
    by_upc = by_upc.merge(
        seg.groupby("upc", observed=True).agg(descrip=("descrip", "first")).reset_index(), on="upc"
    )

    results[selected_name]["segments"] = {
        "by_promo": by_promo.to_dict("records"),
        "by_price_variation": by_var.to_dict("records"),
        "worst_10_stores_by_wape": evaluate_by(seg, "move", "pred", "store")
        .sort_values("wape", ascending=False)
        .head(10)
        .to_dict("records"),
    }

    # -- figures -------------------------------------------------------------
    fig_dir = ensure_dir(cfg.path("figures_dir"))
    weekly = seg.groupby("week_start_date", observed=True).agg(
        actual=("move", "sum"), predicted=("pred", "sum")
    )
    fig, ax = plt.subplots(figsize=(7, 3.2))
    ax.plot(weekly.index, weekly["actual"] / 1000, label="actual", lw=1.0)
    ax.plot(weekly.index, weekly["predicted"] / 1000, label="predicted", lw=1.0)
    ax.set_title(f"Test window: actual vs predicted weekly units ({selected_name})")
    ax.set_ylabel("thousand units")
    ax.legend()
    fig.savefig(fig_dir / "model_actual_vs_predicted_weekly.png")
    plt.close(fig)

    s = seg.sample(min(40_000, len(seg)), random_state=cfg.seed)
    fig, ax = plt.subplots(figsize=(4.6, 4.2))
    ax.scatter(s["move"], s["pred"], s=2, alpha=0.08)
    lim = float(np.percentile(s["move"], 99.5))
    ax.plot([0, lim], [0, lim], color="crimson", lw=0.8)
    ax.set_xlim(0, lim)
    ax.set_ylim(0, lim)
    ax.set_xlabel("actual units")
    ax.set_ylabel("predicted units")
    ax.set_title("Actual vs predicted (test rows)")
    fig.savefig(fig_dir / "model_scatter.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 3.2))
    resid = (s["pred"] - s["move"]).clip(-60, 60)
    ax.hist(resid, bins=80)
    ax.axvline(0, color="crimson", lw=0.8)
    ax.set_title("Residuals (predicted - actual), test rows, clipped")
    fig.savefig(fig_dir / "model_residuals.png")
    plt.close(fig)

    # -- persist -------------------------------------------------------------
    fp = read_json(cfg.path("metrics_dir") / "dataset_fingerprint.json")
    meta = ModelMetadata(
        name=selected_name,
        kind=selected.kind,
        version=f"{selected.kind}-{time.strftime('%Y%m%d-%H%M%S')}",
        trained_at_utc=utc_now(),
        seed=cfg.seed,
        feature_columns=list(selected.prepare(test.head(1)).columns),
        categorical_features=["store", "com_code"],
        target="move",
        params={k: v for k, v in selected.estimator.get_params().items() if isinstance(v, (int, float, str, bool, type(None)))},
        train_weeks=split_info["train_weeks"],
        valid_weeks=split_info["valid_weeks"],
        test_weeks=split_info["test_weeks"],
        train_dates=split_info["train_dates"],
        valid_dates=split_info["valid_dates"],
        test_dates=split_info["test_dates"],
        n_train_rows=int(len(train_fit)),
        metrics={"valid": results[selected_name]["valid"], "test": test_metrics},
        data_fingerprint=fp.get("dataframe_fingerprint"),
        environment=environment_record(),
    )
    selected.metadata = meta
    model_path = cfg.path("models_dir") / "demand_model.joblib"
    selected.save(model_path)
    write_json(cfg.path("models_dir") / "demand_model_metadata.json", meta.as_dict())
    write_json(
        cfg.path("metrics_dir") / "model_metrics.json",
        {"split": split_info, "results": results, "selected": selected_name},
    )
    print(f"saved {model_path} ({model_path.stat().st_size / 1e6:.1f} MB)")

    # -- report --------------------------------------------------------------
    def seg_table(df: pd.DataFrame, key: str) -> str:
        head = f"| {key} | n | units | MAE | RMSE | WAPE | bias |"
        sep = "| --- | --- | --- | --- | --- | --- | --- |"
        rows = [
            f"| {r[key]} | {int(r['n']):,} | {r['units']:,.0f} | {r['mae']:.2f} | "
            f"{r['rmse']:.2f} | {r['wape']:.4f} | {r['bias']:+.2f} |"
            for _, r in df.iterrows()
        ]
        return "\n".join([head, sep, *rows])

    valid_rows = "\n".join(
        metrics_row(name + ("" if r.get("price_aware") else " (price-blind)"), r["valid"])
        for name, r in results.items()
    )
    test_rows = "\n".join(
        metrics_row(name + ("" if r.get("price_aware") else " (price-blind)"), r["test"])
        for name, r in results.items()
        if "test" in r
    )

    report = f"""# 04 - Model comparison

Generated by `python scripts/train.py`. Every metric below comes from this run.

## 1. Temporal evaluation design

No random split is used anywhere. The panel is split **chronologically by
Dominick's week index**:

| split | weeks | dates | rows |
| --- | --- | --- | --- |
| train | {split_info['train_weeks'][0]} - {split_info['train_weeks'][1]} | {split_info['train_dates'][0]} .. {split_info['train_dates'][1]} | {split_info['n_train']:,} |
| validation | {split_info['valid_weeks'][0]} - {split_info['valid_weeks'][1]} | {split_info['valid_dates'][0]} .. {split_info['valid_dates'][1]} | {split_info['n_valid']:,} |
| test | {split_info['test_weeks'][0]} - {split_info['test_weeks'][1]} | {split_info['test_dates'][0]} .. {split_info['test_dates'][1]} | {split_info['n_test']:,} |

Model selection uses **validation** only. The test window is scored once, after
selection, and is not used for tuning.

Target: `move` (weekly units sold). Rows without at least one week of history
are excluded ({split_info['n_train'] + split_info['n_valid'] + split_info['n_test']:,} usable rows).

## 2. Validation results

| model | MAE | RMSE | WAPE | bias | n |
| --- | --- | --- | --- | --- | --- |
{valid_rows}

## 3. Test results (selected model + baselines for context)

| model | MAE | RMSE | WAPE | bias | n |
| --- | --- | --- | --- | --- | --- |
{test_rows}

**Selected model: {selected_name}** (lowest validation WAPE among price-aware
models). Baselines are reported for context but are ineligible for selection:
they contain no price term, so they cannot answer a pricing question at all.
This is the "accuracy is not the objective" point made concrete.

## 3b. Implied price response of each model

Accuracy says nothing about the price dimension, so each model is probed
directly: every price-dependent feature is recomputed at +/-2% of the observed
price and the resulting change in predicted units is converted to an
elasticity (central finite difference, validation rows).

| model | median implied elasticity | p10 | p90 | share negative | rows probed |
| --- | --- | --- | --- | --- | --- |
| {ridge.name} | {results[ridge.name]['implied_elasticity']['median']:.3f} | {results[ridge.name]['implied_elasticity']['p10']:.3f} | {results[ridge.name]['implied_elasticity']['p90']:.3f} | {100*results[ridge.name]['implied_elasticity']['share_negative']:.1f}% | {results[ridge.name]['implied_elasticity']['n']:,} |
| {hgb.name} | {results[hgb.name]['implied_elasticity']['median']:.3f} | {results[hgb.name]['implied_elasticity']['p10']:.3f} | {results[hgb.name]['implied_elasticity']['p90']:.3f} | {100*results[hgb.name]['implied_elasticity']['share_negative']:.1f}% | {results[hgb.name]['implied_elasticity']['n']:,} |

Compare these with the controlled econometric estimates in
`reports/03_ELASTICITY_ANALYSIS.md` (UPC x store fixed effects: -2.42;
with promotion/seasonality controls: -1.91). A demand model whose implied
elasticity is near zero would be accurate and useless for pricing: it would
predict that price changes do not move demand, and the optimizer would happily
raise prices forever. This table is the check that catches that failure.

## 4. Error by segment (test window, selected model)

### By recorded promotion state

{seg_table(by_promo, 'promo_state')}

### By within-series price variation

{seg_table(by_var, 'price_variation_bucket')}

### Ten highest-volume stores

{seg_table(by_store, 'store')}

### Ten highest-volume UPCs

{seg_table(by_upc.drop(columns=['descrip']), 'upc')}

Products: {', '.join(str(x)[:22] for x in by_upc['descrip'].tolist())}

![actual vs predicted weekly](../artifacts/figures/model_actual_vs_predicted_weekly.png)
![scatter](../artifacts/figures/model_scatter.png)
![residuals](../artifacts/figures/model_residuals.png)

## 5. Reproducibility

| property | value |
| --- | --- |
| model version | `{meta.version}` |
| trained at (UTC) | {meta.trained_at_utc} |
| seed | {meta.seed} |
| training rows used | {meta.n_train_rows:,} |
| features | {len(meta.feature_columns)} |
| dataset fingerprint | `{meta.data_fingerprint}` |
| python / sklearn | {meta.environment['python']} / {meta.environment['scikit_learn']} |
| artifact | `artifacts/models/demand_model.joblib` |

## 6. Caveats

1. A low WAPE does **not** mean the model's price response is correct. Phase F
   (`reports/05_PRICE_RESPONSE_VALIDATION.md`) tests the price dimension
   specifically, and it is that test - not this table - that licenses the
   optimizer.
2. Error is strongly heterogeneous across stores and products; the optimizer's
   risk layer uses that heterogeneity.
3. The Poisson objective is chosen because the target is a weekly count; it
   also guarantees non-negative predictions, which the optimizer relies on.
4. Total runtime of this training run: {time.time() - t0:,.0f}s.
"""
    out = cfg.path("reports_dir") / "04_MODEL_COMPARISON.md"
    out.write_text(report, encoding="utf-8")
    print(f"wrote {out}")
    print(f"total {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
