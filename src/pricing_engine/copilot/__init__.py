"""Pricing Copilot: an LLM that answers pricing questions by calling the engine.

The language model never prices anything and never supplies a number. It
chooses which engine tools to call (recommend, simulate, compare policies, ...),
and writes the answer from their results. A deterministic guard then checks
that every number in the answer appears in a tool result and that the wording
follows the project's claim rules; an answer that fails twice is replaced by a
template built from the tool results alone.

The package imports without the ``openai`` SDK; only
:class:`pricing_engine.copilot.client.OpenAIChatClient` needs it.
"""

from pricing_engine.copilot.agent import CopilotAnswer, PricingCopilot
from pricing_engine.copilot.client import LLMTurn, ReplayClient, ScriptedClient, ToolCall
from pricing_engine.copilot.guard import GuardReport, check_answer

__all__ = [
    "CopilotAnswer",
    "GuardReport",
    "LLMTurn",
    "PricingCopilot",
    "ReplayClient",
    "ScriptedClient",
    "ToolCall",
    "check_answer",
]
