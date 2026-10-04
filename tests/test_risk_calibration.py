"""Decision-time history statistics used by the risk-calibration audit."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_risk_calibration.py"


@pytest.fixture(scope="module")
def audit():
    spec = importlib.util.spec_from_file_location("audit_risk_calibration", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_history_stats_use_only_earlier_weeks(audit):
    panel = pd.DataFrame({
        "upc": [1, 1, 1, 1, 2],
        "store": [5, 5, 5, 5, 5],
        "week": [1, 2, 3, 4, 1],
        "effective_unit_price": [2.0, 3.0, 2.0, 4.0, 9.0],
    })
    h = audit.history_stats(panel).set_index(["upc", "week"])
    assert h.loc[(1, 1), "n_obs"] == 0 and np.isnan(h.loc[(1, 1), "price_min"])
    assert h.loc[(1, 3), "n_obs"] == 2
    assert h.loc[(1, 3), "n_distinct_prices"] == 2
    assert h.loc[(1, 4), "n_distinct_prices"] == 2           # 2.0 seen twice, 4.0 not yet
    assert (h.loc[(1, 4), "price_min"], h.loc[(1, 4), "price_max"]) == (2.0, 3.0)
    expected_cv = np.std([2.0, 3.0, 2.0], ddof=1) / np.mean([2.0, 3.0, 2.0])
    assert h.loc[(1, 4), "price_cv"] == pytest.approx(expected_cv)
    assert h.loc[(2, 1), "n_obs"] == 0                      # other series untouched


def test_wape(audit):
    assert audit.wape(np.array([10.0, 10.0]), np.array([8.0, 12.0])) == pytest.approx(0.2)
