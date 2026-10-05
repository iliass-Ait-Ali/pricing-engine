"""Run the unchanged pipeline end to end under another category's config.

    python scripts/download_dominicks.py --category crackers
    python scripts/run_category.py --config configs/crackers.yaml

Every step is the same script the cereal run uses, executed with
``PRICING_ENGINE_CONFIG`` pointing at the category config, so nothing can be
tuned per category by accident. The config must route its outputs away from
the cereal artifacts (``configs/crackers.yaml`` does); this script refuses to
run otherwise.

Outputs
-------
    <metrics_dir>/category_run.json     steps, durations, exit codes
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.utils.io import utc_now, write_json  # noqa: E402

#: The Makefile ``all`` order, restricted to what the cross-category comparison reads.
STEPS: list[list[str]] = [
    ["build_dataset.py"],
    ["validate_data.py"],
    ["build_features.py"],
    ["run_eda.py"],
    ["run_elasticity.py"],
    ["train.py"],
    ["evaluate.py"],
    ["price_response.py"],
    ["estimate_elasticity.py"],
    ["optimize.py", "--batch", "3000"],
    ["audit_constraints.py"],
    ["audit_model_value.py"],
    ["size_value.py"],
    ["segment_roles.py"],
]
PROTECTED = ("artifacts_dir", "metrics_dir", "models_dir", "reports_dir", "processed_table",
             "features_table")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--from-step", default=None, help="Resume from this script name.")
    args = parser.parse_args()

    config = Path(args.config).resolve()
    cfg = load_config(config)
    base = load_config(REPO_ROOT / "configs" / "config.yaml")
    clashes = [k for k in PROTECTED if cfg.path(k) == base.path(k)]
    if clashes:
        raise SystemExit(f"{config.name} would overwrite the cereal outputs: {clashes}")

    env = {**os.environ, "PRICING_ENGINE_CONFIG": str(config), "PYTHONIOENCODING": "utf-8",
           "MPLBACKEND": "Agg",
           "LOKY_MAX_CPU_COUNT": os.environ.get("LOKY_MAX_CPU_COUNT", str(os.cpu_count() or 1))}
    steps = STEPS
    if args.from_step:
        names = [s[0] for s in STEPS]
        steps = STEPS[names.index(args.from_step):]

    record, started = [], time.perf_counter()
    for step in steps:
        print(f"\n=== {' '.join(step)} ===", flush=True)
        t0 = time.perf_counter()
        code = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / step[0]), *step[1:]],
                              env=env, cwd=REPO_ROOT).returncode
        record.append({"step": " ".join(step), "seconds": round(time.perf_counter() - t0, 1),
                       "returncode": code})
        if code != 0:
            print(f"\nCATEGORY RUN FAILED at {step[0]} (exit {code})")
            break

    ok = all(r["returncode"] == 0 for r in record)
    write_json(cfg.path("metrics_dir") / "category_run.json", {
        "finished_at_utc": utc_now(), "config": config.name, "category": cfg.get("project.category"),
        "completed": ok, "total_seconds": round(time.perf_counter() - started, 1), "steps": record,
    })
    print(f"\ncategory run {'complete' if ok else 'FAILED'} in {time.perf_counter() - started:.0f}s")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
