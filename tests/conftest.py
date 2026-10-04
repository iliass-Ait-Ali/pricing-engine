"""Shared pytest fixtures.

Every fixture here is deterministic and synthetic. Synthetic data is used ONLY
for unit tests (formulas, optimizer mathematics, API contracts); the portfolio
analysis itself always runs on the real Dominick's files.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.config import load_config  # noqa: E402


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def cfg():
    return load_config(REPO_ROOT / "configs" / "config.yaml")


@pytest.fixture
def raw_movement() -> pd.DataFrame:
    """A tiny hand-checked movement fixture covering the documented edge cases."""
    return pd.DataFrame(
        {
            "store": [2, 2, 2, 2, 2, 5, 5, 5],
            "upc": [111, 111, 111, 111, 222, 111, 222, 333],
            "week": [10, 11, 12, 13, 10, 10, 11, 12],
            # week 13 for upc 111 is a suspect row (ok = 0)
            "move": [10.0, 20.0, 0.0, 99.0, 5.0, 8.0, 4.0, 3.0],
            "qty": [1.0, 1.0, 1.0, 1.0, 2.0, 1.0, 1.0, 0.0],
            "price": [3.00, 2.50, 3.00, 3.00, 5.00, 3.20, 5.00, 4.00],
            "sale": [None, "B", None, "S", "C", None, "G", None],
            "profit": [25.0, 10.0, 25.0, 25.0, 40.0, 30.0, 40.0, 20.0],
            "ok": [1, 1, 1, 0, 1, 1, 1, 1],
        }
    )


@pytest.fixture
def raw_upc_meta() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "com_code": [311, 311, 311, 311],
            "upc": [111, 222, 333, 999],
            "descrip": ["TEST FLAKES", "TEST OATS", "TEST PUFFS", "NEVER SOLD"],
            "size": ["12 OZ", "18 OZ", "10 OZ", "1 OZ"],
            "case": [12, 12, 12, 1],
            "nitem": [1, 2, 3, 4],
        }
    )


@pytest.fixture
def panel() -> pd.DataFrame:
    """A deterministic multi-week panel used by feature / model / policy tests."""
    rng = np.random.default_rng(0)
    rows = []
    for upc in (111, 222):
        for store in (2, 5):
            base_price = 3.0 if upc == 111 else 5.0
            for week in range(1, 61):
                price = base_price * (1.0 + 0.1 * np.sin(week / 3.0))
                units = max(1.0, 60.0 - 8.0 * price + rng.normal(0, 1.0))
                rows.append(
                    {
                        "upc": upc,
                        "store": store,
                        "week": week,
                        "week_start_date": pd.Timestamp("1990-01-04") + pd.Timedelta(weeks=week - 1),
                        "move": float(round(units)),
                        "qty": 1.0,
                        "price": round(price, 2),
                        "effective_unit_price": round(price, 2),
                        "profit": 25.0,
                        "gross_margin_rate": 0.25,
                        "estimated_unit_aac": round(price * 0.75, 4),
                        "revenue": round(price, 2) * float(round(units)),
                        "gross_profit": round(price, 2) * float(round(units)) * 0.25,
                        "sale": pd.NA,
                        "recorded_promotion_flag": 0,
                        "recorded_promotion_type": "NONE_RECORDED",
                        "ok": 1,
                        "descrip": "TEST FLAKES" if upc == 111 else "TEST OATS",
                        "year": 1990,
                        "month": 1,
                        "quarter": 1,
                        "week_of_year": 1,
                    }
                )
    return pd.DataFrame(rows)


@pytest.fixture(scope="session")
def demo_config(tmp_path_factory) -> Path:
    """Build the synthetic public demo (fast mode) once per session.

    Runs ``scripts/make_demo.py --fast --out-root <tmp>``: the unchanged
    pipeline on generated data, with every output under a temporary folder.
    Returns the path of the rooted config that points at those outputs.
    """
    import subprocess

    out_root = tmp_path_factory.mktemp("demo")
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "make_demo.py"), "--fast",
         "--out-root", str(out_root)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, (
        f"demo build failed:\n{result.stdout[-4000:]}\n{result.stderr[-4000:]}"
    )
    return out_root / "demo_rooted.yaml"
