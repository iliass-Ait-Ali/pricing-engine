"""Re-evaluate the saved demand model on the held-out test window.

    python scripts/evaluate.py

Loads the serialised artifact (not the in-memory object from training), so it
also proves the model reloads and scores identically.

Outputs
-------
    artifacts/metrics/evaluation.json
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.features.build import temporal_split, training_frame  # noqa: E402
from pricing_engine.models.demand_model import load_model  # noqa: E402
from pricing_engine.models.metrics import evaluate, evaluate_by  # noqa: E402
from pricing_engine.utils.io import write_json  # noqa: E402


def main() -> int:
    cfg = load_config()
    model = load_model(cfg.path("models_dir") / "demand_model.joblib", cfg=cfg)
    meta = model.metadata
    print(f"loaded model {meta.name} ({meta.version})")

    feats = pd.read_parquet(cfg.path("features_table"))
    usable = training_frame(feats)
    _, _, test, split = temporal_split(usable, cfg=cfg)

    pred = model.predict(test)
    metrics = evaluate(test["move"], pred)
    print(f"test rows {metrics['n']:,}  MAE {metrics['mae']:.3f}  RMSE {metrics['rmse']:.3f}  "
          f"WAPE {metrics['wape']:.4f}  bias {metrics['bias']:+.3f}")

    reported = (meta.metrics or {}).get("test", {})
    drift = abs(metrics["wape"] - reported.get("wape", metrics["wape"]))
    print(f"WAPE reported at training time: {reported.get('wape')}, reload difference {drift:.2e}")

    seg = test.copy()
    seg["pred"] = pred
    seg["promo_state"] = np.where(seg["recorded_promotion_flag"] == 1, "recorded promo", "no recorded promo")
    by_promo = evaluate_by(seg, "move", "pred", "promo_state")

    payload = {
        "model_version": meta.version,
        "split": split,
        "test_metrics": metrics,
        "reported_at_training": reported,
        "reload_wape_difference": float(drift),
        "by_promotion_state": by_promo.to_dict("records"),
        "implied_elasticity": model.implied_elasticity(test),
    }
    write_json(cfg.path("metrics_dir") / "evaluation.json", payload)
    print("wrote artifacts/metrics/evaluation.json")
    return 0 if drift < 1e-9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
