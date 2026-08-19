"""Re-validate the canonical dataset that is currently on disk.

    python scripts/validate_data.py

Exits non-zero when any documented invariant fails, so it can gate the pipeline
(make validate) without re-reading the 458 MB raw CSV.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.data.loader import load_processed  # noqa: E402
from pricing_engine.data.validator import validate_processed  # noqa: E402
from pricing_engine.utils.io import write_json  # noqa: E402


def main() -> int:
    cfg = load_config()
    path = cfg.path("processed_table")
    print(f"validating {path}")
    df = load_processed(cfg=cfg)
    checks = validate_processed(df)

    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"  [{'PASS' if c.passed else 'FAIL'}] {c.name.ljust(width)}  {c.detail}")

    write_json(
        cfg.path("metrics_dir") / "data_validation.json",
        {"rows": int(len(df)), "checks": [c.as_dict() for c in checks]},
    )

    failed = [c.name for c in checks if not c.passed]
    print()
    if failed:
        print(f"{len(failed)} check(s) FAILED: {failed}")
        return 1
    print(f"All {len(checks)} checks passed on {len(df):,} rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
