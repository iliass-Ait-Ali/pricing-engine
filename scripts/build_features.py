"""Phase C - build the modelling feature table with decision-time guarantees.

    python scripts/build_features.py

Outputs
-------
    data/processed/dominicks_cereals_features.parquet
    artifacts/metrics/feature_build.json
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.data.loader import load_processed  # noqa: E402
from pricing_engine.features.build import (  # noqa: E402
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    build_feature_table,
    temporal_split,
    training_frame,
)
from pricing_engine.utils.io import dataframe_fingerprint, write_json  # noqa: E402

KEEP_EXTRA = [
    "upc",
    "store",
    "week",
    "week_start_date",
    "move",
    "price",
    "qty",
    "revenue",
    "gross_profit",
    "gross_margin_rate",
    "estimated_unit_aac",
    "recorded_promotion_type",
    "descrip",
    "size",
]


def main() -> int:
    cfg = load_config()
    t0 = time.time()
    df = load_processed(cfg=cfg)
    print(f"loaded {len(df):,} canonical rows")

    feats = build_feature_table(df, cfg=cfg)
    print(f"built features in {time.time() - t0:.1f}s -> {feats.shape}")

    keep = list(dict.fromkeys(list(FEATURE_COLUMNS) + KEEP_EXTRA))
    keep = [c for c in keep if c in feats.columns]
    out = feats[keep].copy()

    # Downcast the float feature block: 4.7 M rows x 30 float64 columns is
    # 1.1 GB, and float32 is far more precision than weekly unit sales need.
    for col in out.columns:
        if out[col].dtype == "float64" and col not in ("effective_unit_price", "price"):
            out[col] = out[col].astype("float32")

    usable = training_frame(out)
    train, valid, test, split_info = temporal_split(usable, cfg=cfg)

    path = cfg.path("features_table")
    out.to_parquet(path, index=False)
    print(f"wrote {path} ({path.stat().st_size / 1e6:.1f} MB)")

    missing = {
        c: float(out[c].isna().mean())
        for c in FEATURE_COLUMNS
        if c in out.columns and out[c].isna().any()
    }
    record = {
        "rows_total": int(len(out)),
        "rows_usable_for_training": int(len(usable)),
        "rows_dropped_insufficient_history": int(len(out) - len(usable)),
        "n_features": len(FEATURE_COLUMNS),
        "features": list(FEATURE_COLUMNS),
        "categorical_features": list(CATEGORICAL_FEATURES),
        "missing_share_by_feature": missing,
        "split": split_info,
        "fingerprint": dataframe_fingerprint(out.head(200_000)),
        "build_seconds": round(time.time() - t0, 1),
    }
    write_json(cfg.path("metrics_dir") / "feature_build.json", record)

    print(f"usable rows: {len(usable):,} (dropped {len(out) - len(usable):,} without history)")
    print(
        "split weeks: train {} valid {} test {}".format(
            split_info["train_weeks"], split_info["valid_weeks"], split_info["test_weeks"]
        )
    )
    print(f"train/valid/test rows: {len(train):,} / {len(valid):,} / {len(test):,}")
    if missing:
        print("features with missing values:", {k: round(v, 4) for k, v in missing.items()})
    print(f"total {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
