"""Phase L - estimate the price elasticities used for counterfactual pricing.

    python scripts/estimate_elasticity.py

Estimation uses **training weeks only** (the window recorded in the model
metadata), so an elasticity applied to a validation/test week never saw that
week's outcome.

Outputs
-------
    artifacts/models/elasticity_table.csv
    artifacts/metrics/elasticity_estimation.json
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.economics.elasticity_store import build_elasticity_table  # noqa: E402
from pricing_engine.features.build import temporal_split, training_frame  # noqa: E402
from pricing_engine.models.demand_model import load_model  # noqa: E402
from pricing_engine.utils.io import display_path, write_json  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None, help="Override the output CSV path.")
    args = parser.parse_args()

    cfg = load_config()
    t0 = time.time()

    feats = pd.read_parquet(cfg.path("features_table"))
    usable = training_frame(feats)

    model_path = cfg.path("models_dir") / "demand_model.joblib"
    if model_path.exists():
        meta = load_model(model_path, cfg=cfg).metadata
        lo, hi = meta.train_weeks
        print(f"using the trained model's training window: weeks {lo}-{hi} ({meta.version})")
    else:
        train, _, _, info = temporal_split(usable, cfg=cfg)
        lo, hi = info["train_weeks"]
        print(f"no model artifact yet; using the configured training window: weeks {lo}-{hi}")

    train_rows = usable[(usable["week"] >= lo) & (usable["week"] <= hi)]
    print(f"estimating elasticities on {len(train_rows):,} TRAINING-ONLY rows")
    if train_rows["week"].max() > hi:
        raise SystemExit("leakage guard: training rows exceed the training window")

    table = build_elasticity_table(train_rows, cfg=cfg, training_weeks=(int(lo), int(hi)))

    out = Path(args.out) if args.out else cfg.root / str(
        cfg.get("pricing_response.elasticity_table", "artifacts/models/elasticity_table.csv")
    )
    table.save(out)

    prods = table.products
    usable_mask = prods["usable_for_pricing"]
    summary = {
        **table.metadata,
        "final_elasticity_median": float(prods["elasticity_final"].median()),
        "final_elasticity_p10": float(prods["elasticity_final"].quantile(0.10)),
        "final_elasticity_p90": float(prods["elasticity_final"].quantile(0.90)),
        "raw_elasticity_median_usable": float(prods.loc[usable_mask, "elasticity_raw"].median())
        if usable_mask.any()
        else None,
        "n_wrong_sign": int((prods["reject_reason"] == "wrong_sign").sum()),
        "n_se_too_large": int((prods["reject_reason"] == "standard_error_too_large").sum()),
        "n_insufficient": int(
            (prods["reject_reason"] == "insufficient_observations_or_price_variation").sum()
        ),
        "n_clipped": int(prods["clipped"].sum()),
        "runtime_seconds": round(time.time() - t0, 1),
        "output": display_path(out, REPO_ROOT),
    }
    write_json(cfg.path("metrics_dir") / "elasticity_estimation.json", summary)

    print(f"\npooled controlled elasticity : {table.metadata['pooled_elasticity']:.3f} "
          f"(se {table.metadata['pooled_se']:.4f}, n={table.metadata['pooled_n_obs']:,})")
    print(f"UPC x store FE elasticity    : {table.metadata['fe_elasticity']:.3f} "
          f"(se {table.metadata['fe_se']:.4f})")
    print(f"products                     : {table.metadata['n_products']:,} "
          f"({table.metadata['n_products_usable']:,} usable for pricing)")
    print(f"  rejected wrong sign        : {summary['n_wrong_sign']:,}")
    print(f"  rejected imprecise (se)    : {summary['n_se_too_large']:,}")
    print(f"  rejected thin data         : {summary['n_insufficient']:,}")
    print(f"tau^2 between-product var    : {table.metadata['tau2_between_product_variance']:.4f}")
    print(f"mean shrinkage weight        : {table.metadata['mean_shrinkage_weight']:.3f}")
    print(f"final elasticity median      : {summary['final_elasticity_median']:.3f} "
          f"(p10 {summary['final_elasticity_p10']:.2f}, p90 {summary['final_elasticity_p90']:.2f})")
    print(f"\nwrote {out}  ({time.time() - t0:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
