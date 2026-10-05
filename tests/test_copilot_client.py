"""Which model endpoint the copilot talks to, and how keys are found."""

from __future__ import annotations

import os

import pytest

from pricing_engine.copilot.client import PROVIDERS, load_env_file, resolve_llm_settings

KEY_VARS = ("COPILOT_API_KEY", "COPILOT_BASE_URL", "COPILOT_MODEL", "GROQ_API_KEY", "OPENAI_API_KEY")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in KEY_VARS:
        monkeypatch.delenv(name, raising=False)


def test_no_key_means_no_copilot():
    assert resolve_llm_settings() is None


def test_groq_free_tier_is_the_default_provider(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    s = resolve_llm_settings()
    assert (s.provider, s.api_key) == ("groq", "g")
    assert s.base_url == PROVIDERS["groq"]["base_url"] and s.model == PROVIDERS["groq"]["model"]


def test_openai_is_used_when_it_is_the_only_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    s = resolve_llm_settings()
    assert s.provider == "openai" and s.base_url is None


def test_explicit_endpoint_and_model_win(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setenv("COPILOT_API_KEY", "c")
    monkeypatch.setenv("COPILOT_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("COPILOT_MODEL", "local-model")
    s = resolve_llm_settings(default_model="from-config")
    assert (s.provider, s.api_key, s.base_url, s.model) == (
        "custom", "c", "http://localhost:11434/v1", "local-model")


def test_config_model_overrides_the_provider_default(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "g")
    assert resolve_llm_settings(default_model="llama-3.3-70b-versatile").model == "llama-3.3-70b-versatile"


def test_env_file_supplies_keys_without_overriding_the_environment(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nGROQ_API_KEY='from-file'\nOPENAI_API_KEY=file-openai\nBROKEN LINE\n",
                   encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "from-environment")
    loaded = load_env_file(env)
    try:
        assert loaded == ["GROQ_API_KEY"]                       # names only, never values
        assert os.environ["GROQ_API_KEY"] == "from-file"
        assert os.environ["OPENAI_API_KEY"] == "from-environment"
        assert resolve_llm_settings().provider == "groq"
    finally:
        os.environ.pop("GROQ_API_KEY", None)


def test_missing_env_file_is_fine(tmp_path):
    assert load_env_file(tmp_path / "nope.env") == []
