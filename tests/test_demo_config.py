"""The public demo config must never touch the real artifacts.

`configs/demo.yaml` runs the unchanged pipeline on synthetic data. Two
properties make that safe and honest, and both are enforced here:

* every output path lives outside the real data and artifact folders, so a demo
  run can never overwrite a committed Dominick's result;
* everything that shapes a decision (policy profiles, risk bands, model and
  elasticity settings) is inherited unchanged, so the demo shows the same
  engine rather than one tuned to look good on generated data.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pricing_engine.config import ConfigError, deep_merge, load_config

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL = load_config(REPO_ROOT / "configs" / "config.yaml")
DEMO = load_config(REPO_ROOT / "configs" / "demo.yaml")

PROTECTED = [REAL.path(k) for k in ("raw_dir", "processed_dir", "artifacts_dir", "reports_dir")]


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def test_demo_is_labelled_synthetic():
    assert DEMO.is_synthetic and DEMO.data_mode == "synthetic"
    assert "SYNTHETIC" in DEMO.data_label
    assert not REAL.is_synthetic


@pytest.mark.parametrize("key", sorted(DEMO.require("paths")))
def test_every_demo_path_avoids_the_real_artifacts(key):
    demo_path = DEMO.path(key)
    for protected in PROTECTED:
        assert not _inside(demo_path, protected), f"paths.{key} = {demo_path} is inside {protected}"


def test_demo_elasticity_table_is_separate():
    real = REAL.get("pricing_response.elasticity_table")
    demo = DEMO.get("pricing_response.elasticity_table")
    assert demo != real and demo.startswith("artifacts_demo/")


@pytest.mark.parametrize(
    "section",
    ["policy_profiles", "risk", "optimization", "modeling", "eligibility", "elasticity"],
)
def test_decision_settings_are_inherited_unchanged(section):
    assert DEMO.get(section) == REAL.get(section)


def test_pricing_method_is_inherited():
    for key in ("method", "min_abs_elasticity", "max_abs_elasticity", "fallback"):
        assert DEMO.get(f"pricing_response.{key}") == REAL.get(f"pricing_response.{key}")


def test_deep_merge_semantics():
    base = {"a": {"x": 1, "y": [1, 2]}, "b": 1}
    merged = deep_merge(base, {"a": {"y": [3]}, "c": 2})
    assert merged == {"a": {"x": 1, "y": [3]}, "b": 1, "c": 2}
    assert base == {"a": {"x": 1, "y": [1, 2]}, "b": 1}  # inputs untouched


def test_extends_cycle_is_rejected(tmp_path):
    (tmp_path / "a.yaml").write_text("extends: b.yaml\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("extends: a.yaml\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Circular"):
        load_config(tmp_path / "a.yaml")
