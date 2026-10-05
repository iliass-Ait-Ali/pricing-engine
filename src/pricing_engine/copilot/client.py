"""LLM clients behind one small interface.

* :class:`OpenAIChatClient` - the live client: chat completions with tool
  calling on any OpenAI-compatible endpoint (Groq's free tier by default, or
  OpenAI, or a custom base URL). The only code that imports the ``openai`` SDK.
* :class:`ScriptedClient` - returns a fixed list of turns; unit tests.
* :class:`RecordingClient` / :class:`ReplayClient` - record live turns to JSON
  and replay them, so the evaluation set runs in CI without an API key.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMTurn:
    """One model response: either final text, or tool calls to execute."""

    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LLMTurn:
        return cls(
            text=d.get("text"),
            tool_calls=[ToolCall(**c) for c in d.get("tool_calls", [])],
            usage=dict(d.get("usage", {})),
        )


class LLMClient(Protocol):
    model: str

    def complete(self, messages: list[dict], tools: list[dict], *, max_tokens: int) -> LLMTurn:
        ...


#: Providers with an OpenAI-compatible chat-completions API and tool calling,
#: tried in this order. Groq comes first because its free tier needs no card.
PROVIDERS: dict[str, dict[str, str | None]] = {
    "groq": {"key_env": "GROQ_API_KEY", "base_url": "https://api.groq.com/openai/v1",
             "model": "openai/gpt-oss-120b"},
    "openai": {"key_env": "OPENAI_API_KEY", "base_url": None, "model": "gpt-4o-mini"},
}


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    api_key: str
    base_url: str | None
    model: str


def load_env_file(path: str | Path) -> list[str]:
    """Read ``KEY=VALUE`` lines from a git-ignored ``.env`` into the environment.

    Variables that are already set win, so a real environment variable always
    overrides the file. Returns the names it set (never the values).
    """
    path = Path(path)
    if not path.exists():
        return []
    loaded = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("'\"")
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def resolve_llm_settings(default_model: str | None = None,
                         env_file: str | Path | None = None) -> LLMSettings | None:
    """Which model endpoint to use, or None when no key is configured.

    Order: an explicit ``COPILOT_API_KEY`` (with ``COPILOT_BASE_URL``), then the
    first provider in :data:`PROVIDERS` whose key is set. ``COPILOT_MODEL``
    overrides the model for any provider.
    """
    if env_file is not None:
        load_env_file(env_file)
    model_override = os.environ.get("COPILOT_MODEL") or default_model
    explicit = os.environ.get("COPILOT_API_KEY")
    if explicit:
        base_url = os.environ.get("COPILOT_BASE_URL") or None
        return LLMSettings("custom", explicit, base_url, model_override or "gpt-4o-mini")
    for name, spec in PROVIDERS.items():
        key = os.environ.get(str(spec["key_env"]))
        if key:
            return LLMSettings(name, key, spec["base_url"], model_override or str(spec["model"]))
    return None


class OpenAIChatClient:
    """Chat completions with tool calling on any OpenAI-compatible endpoint, temperature 0."""

    def __init__(self, settings: LLMSettings) -> None:
        from openai import OpenAI  # optional dependency: pip install -e ".[genai]"

        self.model = settings.model
        self.provider = settings.provider
        # Free tiers rate-limit per minute; the SDK backs off and retries on 429.
        self._client = OpenAI(api_key=settings.api_key, base_url=settings.base_url,
                              max_retries=6, timeout=90)

    def complete(self, messages: list[dict], tools: list[dict], *, max_tokens: int) -> LLMTurn:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools or None,
            temperature=0,
            max_completion_tokens=max_tokens,
        )
        message = response.choices[0].message
        calls = [
            ToolCall(id=c.id, name=c.function.name, arguments=_parse_args(c.function.arguments))
            for c in (message.tool_calls or [])
        ]
        usage = {}
        if response.usage is not None:
            usage = {"prompt_tokens": response.usage.prompt_tokens,
                     "completion_tokens": response.usage.completion_tokens}
        return LLMTurn(text=message.content, tool_calls=calls, usage=usage)


def _parse_args(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"_unparseable_arguments": raw}
    return value if isinstance(value, dict) else {"_arguments": value}


class ScriptedClient:
    """Returns the given turns in order; records every request it received."""

    def __init__(self, turns: Sequence[LLMTurn], model: str = "scripted") -> None:
        self.model = model
        self._turns = list(turns)
        self.requests: list[list[dict]] = []

    def complete(self, messages: list[dict], tools: list[dict], *, max_tokens: int) -> LLMTurn:
        self.requests.append([dict(m) for m in messages])
        if not self._turns:
            raise RuntimeError("ScriptedClient ran out of turns")
        return self._turns.pop(0)


class RecordingClient:
    """Wraps a live client and keeps every turn it returns."""

    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.model = inner.model
        self.turns: list[LLMTurn] = []

    def complete(self, messages: list[dict], tools: list[dict], *, max_tokens: int) -> LLMTurn:
        turn = self.inner.complete(messages, tools, max_tokens=max_tokens)
        self.turns.append(turn)
        return turn

    def save(self, path: str | Path, **meta: Any) -> None:
        payload = {"model": self.model, **meta, "turns": [t.as_dict() for t in self.turns]}
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")


class ReplayClient(ScriptedClient):
    """Replays turns saved by :class:`RecordingClient`."""

    @classmethod
    def load(cls, path: str | Path) -> ReplayClient:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls([LLMTurn.from_dict(t) for t in payload["turns"]], model=payload.get("model", "replay"))
