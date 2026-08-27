"""Phase O - batch throughput/memory sweep across replicated context counts.

    python scripts/benchmark_scale.py --sizes 500,3000,10000,30000,100000

`scripts/benchmark_batch.py` measures `optimize_price_batch` at one fixed
size (3,000 real contexts) and proves it equivalent to the per-context loop.
This script does not repeat that equivalence check - it only measures
`optimize_price_batch`'s own throughput and peak memory as the number of
contexts scored in one call grows.

Contexts above the size of the real sampled base pool are that SAME pool
REPLICATED (tiled), not independent new contexts or catalogue growth: this
measures throughput at larger row counts on data already proven correct, not
a claim about a larger real catalogue or about any other hardware. See the
`caveat` field in the JSON output and reports/22_POST_FREEZE_ENGINEERING.md.

Output: artifacts/metrics/batch_scale_benchmark.json
"""

from __future__ import annotations

import argparse
import math
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.config import Config, load_config  # noqa: E402
from pricing_engine.features.build import training_frame  # noqa: E402
from pricing_engine.models.demand_model import load_model  # noqa: E402
from pricing_engine.models.hybrid import load_pricing_model  # noqa: E402
from pricing_engine.optimization.optimizer import optimize_price_batch  # noqa: E402
from pricing_engine.utils.io import peak_process_memory_mb, write_json  # noqa: E402

STAT_COLUMNS = ["n_obs", "n_distinct_prices", "price_cv", "price_min", "price_max"]


def _tile(base: pd.DataFrame, series_stats: list[dict], n: int) -> tuple[pd.DataFrame, list[dict]]:
    """Replicate the base pool up to exactly `n` rows, contexts and stats in step."""
    reps = math.ceil(n / len(base))
    contexts = pd.concat([base] * reps, ignore_index=True).iloc[:n].reset_index(drop=True)
    stats = (series_stats * reps)[:n]
    return contexts, stats


def run_scale_sweep(
    model,
    base_contexts: pd.DataFrame,
    base_series_stats: list[dict],
    sizes: list[int],
    *,
    cfg: Config,
    policy_profile: str = "standard",
) -> list[dict]:
    """Run `optimize_price_batch` once per requested size; return one result dict per size."""
    results = []
    for n in sizes:
        contexts, stats = _tile(base_contexts, base_series_stats, n)
        assert len(contexts) == n, f"tiling bug: requested {n}, built {len(contexts)}"

        t0 = time.perf_counter()
        recs = optimize_price_batch(
            model, contexts, cfg=cfg, policy_profile=policy_profile, series_stats=stats
        )
        seconds = time.perf_counter() - t0

        reps = math.ceil(n / len(base_contexts))
        results.append(
            {
                "n_contexts": n,
                "n_recommendations": len(recs),
                "seconds": round(seconds, 4),
                "contexts_per_second": round(n / seconds, 1) if seconds > 0 else None,
                "peak_process_memory_mb": peak_process_memory_mb(),
                "replication_factor": reps,
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sizes", default="500,3000,10000,30000,100000",
                         help="Comma-separated context counts to sweep.")
    parser.add_argument("--base-contexts", type=int, default=3000,
                         help="Size of the real sampled pool that larger sizes replicate.")
    parser.add_argument("--profile", default="standard",
                        choices=["conservative", "standard", "aggressive"])
    parser.add_argument("--week", type=int, default=None)
    args = parser.parse_args()
    sizes = sorted({int(s) for s in args.sizes.split(",") if s.strip()})

    cfg = load_config()
    base_model = load_model(cfg.path("models_dir") / "demand_model.joblib", cfg=cfg)
    model = load_pricing_model(base_model, cfg=cfg)

    feats = pd.read_parquet(cfg.path("features_table"))
    usable = training_frame(feats)
    var = pd.read_csv(cfg.path("metrics_dir") / "price_variation_upc_store.csv")
    stats_table = var[["upc", "store", *STAT_COLUMNS, "eligible"]]

    week = args.week or int(usable["week"].max())
    pool = usable[usable["week"] == week].merge(stats_table, on=["upc", "store"], how="left")
    base = pool.sample(min(args.base_contexts, len(pool)), random_state=cfg.seed).reset_index(drop=True)
    base_series_stats = base[STAT_COLUMNS].to_dict(orient="records")
    print(f"decision week {week}: base pool {len(base):,} real contexts, sweeping sizes {sizes}")

    results = run_scale_sweep(model, base, base_series_stats, sizes, cfg=cfg, policy_profile=args.profile)
    for r in results:
        print(
            f"  n={r['n_contexts']:>7,} (x{r['replication_factor']:>3} replication) "
            f"| {r['seconds']:>7.2f}s | {r['contexts_per_second']:>9,.1f} ctx/s "
            f"| peak {r['peak_process_memory_mb']} MB"
        )

    summary = {
        "decision_week": week,
        "policy_profile": args.profile,
        "price_response_method": model.method.value,
        "base_sample_size": len(base),
        "sizes": sizes,
        "results": results,
        "environment": {
            "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
        "caveat": (
            "Single machine, single process. Contexts above the base sample size "
            "are the same real sampled contexts REPLICATED (tiled), not "
            "independent new contexts or catalogue growth - this measures "
            "optimize_price_batch's throughput at larger row counts, not "
            "scaling to a larger real catalogue or to other hardware."
        ),
    }
    out = cfg.path("metrics_dir") / "batch_scale_benchmark.json"
    write_json(out, summary)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
