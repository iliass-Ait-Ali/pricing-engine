"""Build the complete public demo: synthetic data -> the unchanged pipeline.

    python scripts/make_demo.py              # full demo build (configs/demo.yaml)
    python scripts/make_demo.py --fast       # smaller model, for CI
    python scripts/make_demo.py --out-root /tmp/demo   # everything under one folder

Every step is the same script the real Dominick's run uses, executed with
``PRICING_ENGINE_CONFIG`` pointing at a synthetic config. Nothing here touches
``artifacts/``, ``reports/`` or ``data/processed/``: the demo config routes all
outputs to ``data/demo/`` and ``artifacts_demo/`` (or under ``--out-root``).

Outputs
-------
    <metrics_dir>/demo_build.json   steps, durations and row counts of this build
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.utils.io import utc_now, write_json  # noqa: E402

DEFAULT_CONFIG = REPO_ROOT / "configs" / "demo.yaml"


def steps(fast: bool) -> list[list[str]]:
    """The pipeline, in dependency order (the Makefile ``all`` target, minus audits).

    ``fast`` (CI and tests) skips the two slowest steps that only feed
    dashboard comparison panels: the method comparison and the backtest.
    """
    n_ctx = "150" if fast else "300"
    return [
        ["make_synthetic.py"],
        ["build_dataset.py"],
        ["validate_data.py"],
        ["build_features.py"],
        ["run_eda.py"],
        ["run_elasticity.py"],
        ["train.py", *(["--max-iter", "60"] if fast else [])],
        ["evaluate.py"],
        ["price_response.py", "--n-contexts", n_ctx],
        ["estimate_elasticity.py"],
        *([] if fast else [["compare_price_response.py", "--n-contexts", n_ctx]]),
        ["optimize.py", "--batch", n_ctx],
        ["size_value.py", "--batch", n_ctx],
        ["segment_roles.py", "--batch", n_ctx],
        *([] if fast else [["backtest.py", "--weeks", "6", "--contexts-per-week", "150"]]),
    ]


def rooted_config(base: Path, out_root: Path) -> Path:
    """A config that extends ``base`` with every path moved under ``out_root``."""
    cfg = load_config(base)
    paths = {k: str(out_root / Path(v)) for k, v in cfg.require("paths").items()}
    table = str(out_root / Path(cfg.require("pricing_response.elasticity_table")))
    out_root.mkdir(parents=True, exist_ok=True)
    target = out_root / "demo_rooted.yaml"
    target.write_text(
        yaml.safe_dump(
            {
                "extends": str(base.resolve()),
                "paths": paths,
                "pricing_response": {"elasticity_table": table},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--fast", action="store_true", help="Smaller model and samples (CI).")
    parser.add_argument("--out-root", default=None, help="Put every demo output under this folder.")
    args = parser.parse_args()

    config = Path(args.config).resolve()
    if args.out_root:
        config = rooted_config(config, Path(args.out_root).resolve())
    cfg = load_config(config)
    if not cfg.is_synthetic:
        raise SystemExit(f"{config} is not a synthetic config (data_mode={cfg.data_mode!r})")

    env = {
        **os.environ,
        "PRICING_ENGINE_CONFIG": str(config),
        "PYTHONIOENCODING": "utf-8",
        "MPLBACKEND": "Agg",
        # joblib probes physical cores with a tool missing on some Windows builds.
        "LOKY_MAX_CPU_COUNT": os.environ.get("LOKY_MAX_CPU_COUNT", str(os.cpu_count() or 1)),
    }
    record: list[dict] = []
    started = time.perf_counter()
    for step in steps(args.fast):
        print(f"\n=== {' '.join(step)} ===", flush=True)
        t0 = time.perf_counter()
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / step[0]), *step[1:]],
            env=env,
            cwd=REPO_ROOT,
        )
        seconds = round(time.perf_counter() - t0, 2)
        record.append({"step": " ".join(step), "seconds": seconds, "returncode": result.returncode})
        if result.returncode != 0:
            print(f"\nDEMO BUILD FAILED at {step[0]} (exit {result.returncode})")
            return result.returncode

    summary = {
        "built_at_utc": utc_now(),
        "config": str(config),
        "data_mode": cfg.data_mode,
        "data_label": cfg.data_label,
        "fast": args.fast,
        "total_seconds": round(time.perf_counter() - started, 2),
        "steps": record,
    }
    out = write_json(cfg.path("metrics_dir") / "demo_build.json", summary)
    print(f"\ndemo build complete in {summary['total_seconds']:.0f}s -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
