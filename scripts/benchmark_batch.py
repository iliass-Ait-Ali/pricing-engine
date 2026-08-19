"""Benchmark and equivalence-check the batch recommendation path on real data.

    python scripts/benchmark_batch.py --contexts 3000

Runs the same decision-week context sample twice:

  * the legacy path - one ``optimize_price`` call per context;
  * the vectorised path - ``optimize_price_batch``, which scores contexts in
    groups through ``simulate_many``.

Every recommendation field is then compared. Discrete fields (decision state,
actionable, reason codes, elasticity source, risk level) must match exactly;
the floating-point economics must match to a tight tolerance. The measurement
is a single-machine, single-process wall-clock benchmark on this dataset - it
says nothing about scaling to other hardware or catalogue sizes.

Output: artifacts/metrics/batch_performance.json
"""

from __future__ import annotations

import argparse
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.features.build import training_frame  # noqa: E402
from pricing_engine.models.demand_model import load_model  # noqa: E402
from pricing_engine.models.hybrid import load_pricing_model  # noqa: E402
from pricing_engine.optimization.optimizer import (  # noqa: E402
    optimize_price,
    optimize_price_batch,
)
from pricing_engine.utils.io import write_json  # noqa: E402

STAT_COLUMNS = ["n_obs", "n_distinct_prices", "price_cv", "price_min", "price_max"]

DISCRETE_FIELDS = [
    "upc",
    "store",
    "decision_week",
    "decision_week_start_date",
    "objective",
    "policy_profile",
    "model_version",
    "price_response_method",
    "elasticity_source",
    "decision",
    "actionable",
    "risk_level",
    "reason_codes",
    "risk_notes",
]

FLOAT_FIELDS = [
    "elasticity_used",
    "current_price",
    "proposed_candidate_price",
    "final_recommended_price",
    "proposed_price_change_pct",
    "price_change_pct",
    "predicted_units_current",
    "predicted_units_recommended",
    "expected_revenue_current",
    "expected_revenue_recommended",
    "expected_gross_profit_current",
    "expected_gross_profit_recommended",
    "model_internal_estimated_profit_uplift_pct",
    "unit_cost_used",
]

TOLERANCE = 1e-9


def _peak_memory_mb() -> float | None:
    """Peak resident memory of this process, when the platform exposes it."""
    try:  # Windows
        import ctypes
        import ctypes.wintypes as wt

        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wt.DWORD),
                ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _Counters()
        counters.cb = ctypes.sizeof(_Counters)
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = wt.HANDLE
        get_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_info.argtypes = [wt.HANDLE, ctypes.POINTER(_Counters), wt.DWORD]
        if get_info(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return round(counters.PeakWorkingSetSize / 1024**2, 1)
    except Exception:  # noqa: BLE001 - a benchmark must not fail on diagnostics
        pass
    try:  # POSIX
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(peak / (1024**2 if sys.platform == "darwin" else 1024), 1)
    except Exception:  # noqa: BLE001
        return None


def compare(old: list, new: list) -> dict:
    """Field-by-field comparison of two recommendation lists."""
    if len(old) != len(new):
        return {"equivalent": False, "reason": f"{len(old)} vs {len(new)} recommendations"}

    a = pd.DataFrame([r.as_dict() for r in old])
    b = pd.DataFrame([r.as_dict() for r in new])
    discrete_mismatches: dict[str, int] = {}
    for field in DISCRETE_FIELDS:
        va = a[field].map(lambda v: tuple(v) if isinstance(v, list) else v)
        vb = b[field].map(lambda v: tuple(v) if isinstance(v, list) else v)
        n_diff = int((va != vb).sum())
        if n_diff:
            discrete_mismatches[field] = n_diff

    float_max_abs_diff: dict[str, float] = {}
    for field in FLOAT_FIELDS:
        va = pd.to_numeric(a[field], errors="coerce").to_numpy(dtype="float64")
        vb = pd.to_numeric(b[field], errors="coerce").to_numpy(dtype="float64")
        both_nan = np.isnan(va) & np.isnan(vb)
        diff = np.abs(np.where(both_nan, 0.0, va - vb))
        if np.isnan(diff).any():
            float_max_abs_diff[field] = float("nan")
        else:
            float_max_abs_diff[field] = float(diff.max()) if diff.size else 0.0

    worst = max((v for v in float_max_abs_diff.values() if not np.isnan(v)), default=0.0)
    return {
        "equivalent": (not discrete_mismatches)
        and worst <= TOLERANCE
        and not any(np.isnan(v) for v in float_max_abs_diff.values()),
        "n_recommendations": len(old),
        "discrete_fields_compared": DISCRETE_FIELDS,
        "discrete_mismatches": discrete_mismatches,
        "float_fields_compared": FLOAT_FIELDS,
        "float_max_abs_diff": float_max_abs_diff,
        "worst_float_abs_diff": worst,
        "tolerance": TOLERANCE,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contexts", type=int, default=3000)
    parser.add_argument("--profile", default="standard",
                        choices=["conservative", "standard", "aggressive"])
    parser.add_argument("--week", type=int, default=None)
    args = parser.parse_args()

    cfg = load_config()
    base_model = load_model(cfg.path("models_dir") / "demand_model.joblib", cfg=cfg)
    model = load_pricing_model(base_model, cfg=cfg)

    feats = pd.read_parquet(cfg.path("features_table"))
    usable = training_frame(feats)
    var = pd.read_csv(cfg.path("metrics_dir") / "price_variation_upc_store.csv")
    stats = var[["upc", "store", *STAT_COLUMNS, "eligible"]]

    week = args.week or int(usable["week"].max())
    pool = usable[usable["week"] == week].merge(stats, on=["upc", "store"], how="left")
    take = pool.sample(min(args.contexts, len(pool)), random_state=cfg.seed).reset_index(drop=True)
    series_stats = take[STAT_COLUMNS].to_dict(orient="records")
    print(f"decision week {week}: benchmarking {len(take):,} contexts")

    t0 = time.perf_counter()
    old = [
        optimize_price(
            model,
            take.iloc[[i]],
            cfg=cfg,
            policy_profile=args.profile,
            series_stats=take.iloc[i][STAT_COLUMNS].to_dict(),
        )
        for i in range(len(take))
    ]
    old_seconds = time.perf_counter() - t0
    print(f"  per-context loop : {old_seconds:8.2f}s")

    t0 = time.perf_counter()
    new = optimize_price_batch(
        model, take, cfg=cfg, policy_profile=args.profile, series_stats=series_stats
    )
    new_seconds = time.perf_counter() - t0
    print(f"  vectorised batch : {new_seconds:8.2f}s")

    equivalence = compare(old, new)
    summary = {
        "decision_week": week,
        "policy_profile": args.profile,
        "price_response_method": model.method.value,
        "model_version": getattr(getattr(model, "metadata", None), "version", None),
        "n_contexts": int(len(take)),
        "per_context_loop_seconds": round(old_seconds, 2),
        "vectorised_batch_seconds": round(new_seconds, 2),
        "speedup": round(old_seconds / new_seconds, 2) if new_seconds > 0 else None,
        "contexts_per_second_loop": round(len(take) / old_seconds, 1) if old_seconds else None,
        "contexts_per_second_batch": round(len(take) / new_seconds, 1) if new_seconds else None,
        "peak_process_memory_mb": _peak_memory_mb(),
        "equivalence": equivalence,
        "environment": {
            "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
        "caveat": (
            "Single-machine, single-process wall-clock measurement on this dataset. "
            "It does not demonstrate scaling to other hardware or catalogue sizes."
        ),
    }
    out = cfg.path("metrics_dir") / "batch_performance.json"
    write_json(out, summary)
    print(f"\nspeedup: {summary['speedup']}x | equivalent: {equivalence['equivalent']}")
    if not equivalence["equivalent"]:
        print("MISMATCH:", equivalence["discrete_mismatches"], equivalence["float_max_abs_diff"])
    print(f"wrote {out}")
    return 0 if equivalence["equivalent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
