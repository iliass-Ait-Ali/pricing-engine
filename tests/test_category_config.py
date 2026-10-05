"""A second-category run must be like for like and must not touch the cereal outputs."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from pricing_engine.config import load_config

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE = load_config(REPO_ROOT / "configs" / "config.yaml")
CRACKERS = load_config(REPO_ROOT / "configs" / "crackers.yaml")


@pytest.mark.parametrize(
    "section", ["policy_profiles", "risk", "optimization", "modeling", "eligibility", "elasticity"]
)
def test_nothing_is_retuned_for_the_second_category(section):
    assert CRACKERS.get(section) == BASE.get(section)


@pytest.mark.parametrize("key", ["processed_table", "features_table", "artifacts_dir", "models_dir",
                                 "metrics_dir", "reports_dir", "recommendation_log"])
def test_second_category_outputs_do_not_overwrite_cereal(key):
    assert CRACKERS.path(key) != BASE.path(key)


def test_second_category_is_real_data_not_synthetic():
    assert not CRACKERS.is_synthetic
    assert CRACKERS.get("data.movement_file") == "wcra.csv"


def test_row_level_outputs_of_the_second_category_are_git_ignored():
    for path in ("artifacts_crackers/metrics/x.json", "data/processed/dominicks_crackers.parquet",
                 "data/raw/dominicks/crackers/wcra.csv"):
        result = subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO_ROOT)
        assert result.returncode == 0, f"{path} is not git-ignored"


def test_runner_refuses_a_config_that_would_overwrite_cereal():
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "run_category.py"), "--config",
         str(REPO_ROOT / "configs" / "config.yaml")],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode != 0 and "would overwrite the cereal outputs" in result.stderr


def test_downloader_knows_the_second_category():
    spec = importlib.util.spec_from_file_location(
        "download_dominicks_cat", REPO_ROOT / "scripts" / "download_dominicks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.CATEGORIES["crackers"] == "cra"
    assert module.movement_url("cra").startswith("https://www.chicagobooth.edu/")
    assert module.movement_url("cra").endswith("/wcra.zip")
    assert module.UPC_URL.endswith("/upccer.csv")   # cereal stays the default
