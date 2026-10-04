"""Central configuration loading.

Everything tunable (paths, thresholds, policy profiles, model
hyper-parameters) lives in ``configs/config.yaml``. Code should never hardcode
a magic number; it should read it from here.

A config file may start with ``extends: <other.yaml>`` (resolved relative to
the file itself). The parent is loaded first and the child is deep-merged on
top: nested mappings merge key by key, anything else (lists, scalars) is
replaced. ``configs/demo.yaml`` uses this to run the unchanged pipeline on
synthetic data with its own paths, so a demo run can never overwrite the real
artifacts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


class ConfigError(RuntimeError):
    """Raised when the project configuration is missing or inconsistent."""


def project_root() -> Path:
    """Return the repository root (the directory containing ``configs/``)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "configs" / "config.yaml").exists():
            return parent
    # Fall back to three levels up: src/pricing_engine/config.py -> root
    return here.parents[2]


def _default_config_path() -> Path:
    env = os.environ.get("PRICING_ENGINE_CONFIG")
    if env:
        return Path(env)
    return project_root() / "configs" / "config.yaml"


@dataclass(frozen=True)
class Config:
    """Typed-ish wrapper around the YAML configuration tree."""

    raw: dict[str, Any]
    root: Path

    # -- generic access ----------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        """Fetch a nested key, e.g. ``cfg.get("modeling.target")``."""
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, dotted: str) -> Any:
        """Like :meth:`get`, but raises a domain error when the key is absent."""
        sentinel = object()
        value = self.get(dotted, sentinel)
        if value is sentinel:
            raise ConfigError(
                f"Missing required configuration key '{dotted}' in configs/config.yaml."
            )
        return value

    # -- paths -------------------------------------------------------------
    def path(self, dotted: str) -> Path:
        """Resolve a configured relative path against the repository root."""
        value = self.require(f"paths.{dotted}")
        p = Path(value)
        return p if p.is_absolute() else (self.root / p)

    @property
    def seed(self) -> int:
        return int(self.get("project.random_seed", 42))

    # -- data provenance -----------------------------------------------------
    @property
    def data_mode(self) -> str:
        """``licensed`` (the real Dominick's panel) or ``synthetic`` (demo data)."""
        return str(self.get("project.data_mode", "licensed"))

    @property
    def is_synthetic(self) -> bool:
        return self.data_mode == "synthetic"

    @property
    def data_label(self) -> str:
        """Human-readable provenance label shown by the API and the dashboard."""
        default = (
            "SYNTHETIC DEMO DATA - generated, not Dominick's"
            if self.is_synthetic
            else "Dominick's Finer Foods (Kilts Center, Chicago Booth)"
        )
        return str(self.get("project.data_label", default))

    # -- policy ------------------------------------------------------------
    def policy(self, profile: str | None = None) -> dict[str, Any]:
        """Return a DEMO policy profile (conservative / standard / aggressive)."""
        profile = profile or os.environ.get("PRICING_ENGINE_POLICY_PROFILE", "standard")
        profiles = self.require("policy_profiles")
        if profile not in profiles:
            raise ConfigError(
                f"Unknown policy profile '{profile}'. Available: {sorted(profiles)}."
            )
        return dict(profiles[profile])


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """``override`` on top of ``base``: mappings merge, everything else replaces."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_tree(path: Path, seen: tuple[Path, ...] = ()) -> dict[str, Any]:
    path = path.resolve()
    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")
    if path in seen:
        raise ConfigError(f"Circular 'extends' chain: {' -> '.join(map(str, (*seen, path)))}")
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    parent = raw.pop("extends", None)
    if parent is None:
        return raw
    return deep_merge(_read_tree(path.parent / parent, (*seen, path)), raw)


@lru_cache(maxsize=8)
def _load_cached(path_str: str) -> Config:
    return Config(raw=_read_tree(Path(path_str)), root=project_root())


def load_config(path: str | Path | None = None) -> Config:
    """Load (and cache) the project configuration."""
    return _load_cached(str(Path(path) if path else _default_config_path()))


#: Tests that switch ``PRICING_ENGINE_CONFIG`` call this to drop cached configs.
load_config.cache_clear = _load_cached.cache_clear  # type: ignore[attr-defined]
