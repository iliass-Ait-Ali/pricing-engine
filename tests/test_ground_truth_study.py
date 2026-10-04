"""The ground-truth recovery study runs and points the right way (quick mode)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def study(tmp_path_factory):
    out = tmp_path_factory.mktemp("gts")
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "ground_truth_study.py"), "--quick",
         "--out-dir", str(out)],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    payload = json.loads((out / "artifacts" / "metrics" / "ground_truth_study.json").read_text())
    return out, {r["scenario"]: r for r in payload["summary"]}


def test_clean_panel_is_recovered(study):
    _, s = study
    assert abs(s["clean"]["pooled_bias"]) < 0.3
    assert s["clean"]["direction_agreement_when_moved"] > 0.9


def test_unrecorded_promotions_bias_the_estimate_steeper(study):
    _, s = study
    assert s["promos unrecorded"]["pooled_bias"] < s["clean"]["pooled_bias"] - 0.5


def test_outputs_are_written(study):
    out, _ = study
    assert (out / "reports" / "25_GROUND_TRUTH_STUDY.md").exists()
    assert (out / "artifacts" / "report_figures" / "fig_15_ground_truth_recovery.png").exists()
