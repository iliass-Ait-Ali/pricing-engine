"""Generate the synthetic Dominick's-shaped raw files for the public demo.

    PRICING_ENGINE_CONFIG=configs/demo.yaml python scripts/make_synthetic.py
    python scripts/make_synthetic.py --config configs/demo.yaml --seed 11

Writes the raw movement and UPC files (Dominick's schema) to ``paths.raw_dir``
and the ground truth (true elasticities, true costs) to ``paths.truth_dir``,
which no pipeline step reads. Generator settings come from the ``synthetic:``
block of the config; command-line flags override them.

Refuses to run against a config whose ``project.data_mode`` is not
``synthetic``, so generated files can never land in the licensed-data folders.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import fields
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.data.synthetic import SyntheticSpec, generate_panel, write_panel  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None, help="Config path (default: PRICING_ENGINE_CONFIG).")
    parser.add_argument("--n-upcs", type=int, default=None)
    parser.add_argument("--n-stores", type=int, default=None)
    parser.add_argument("--n-weeks", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    if args.config:
        os.environ["PRICING_ENGINE_CONFIG"] = args.config
    cfg = load_config(args.config)
    if not cfg.is_synthetic:
        raise SystemExit(
            "refusing to write synthetic files: the active config has data_mode "
            f"'{cfg.data_mode}'. Use configs/demo.yaml."
        )

    known = {f.name for f in fields(SyntheticSpec)}
    overrides = {k: v for k, v in (cfg.get("synthetic", {}) or {}).items() if k in known}
    for name in ("n_upcs", "n_stores", "n_weeks", "seed"):
        value = getattr(args, name)
        if value is not None:
            overrides[name] = value
    for key in ("elasticity_bounds", "markup_range", "promo_depth", "price_test_range", "sizes_oz"):
        if key in overrides:
            overrides[key] = tuple(overrides[key])
    spec = SyntheticSpec(**overrides)

    panel = generate_panel(spec)
    write_panel(
        panel,
        cfg.path("raw_dir"),
        cfg.path("truth_dir"),
        movement_file=str(cfg.get("data.movement_file", "wcer.csv")),
        upc_file=str(cfg.get("data.upc_file", "upccer.csv")),
    )
    print(
        f"synthetic panel: {spec.n_upcs} UPCs x {spec.n_stores} stores x {spec.n_weeks} weeks "
        f"(seed {spec.seed}) -> {len(panel.movement):,} raw rows"
    )
    print(f"wrote raw files to {cfg.path('raw_dir')}")
    print(f"wrote ground truth to {cfg.path('truth_dir')} (never read by the pipeline)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
