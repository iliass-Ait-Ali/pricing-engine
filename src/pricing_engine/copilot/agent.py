"""The copilot loop: model -> tools -> model -> guard -> answer."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from pricing_engine.copilot.client import LLMClient, LLMTurn
from pricing_engine.copilot.guard import GuardReport, check_answer
from pricing_engine.copilot.prompts import system_prompt
from pricing_engine.copilot.tools import TOOLS, run_tool
from pricing_engine.serving import AppState

Status = Literal["passed", "passed_after_regeneration", "fallback_template"]

SYNTHETIC_FOOTER = (
    "\n\n_Synthetic demo data: these figures describe a generated panel, not Dominick's._"
)


@dataclass
class ExecutedCall:
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]


@dataclass
class CopilotAnswer:
    question: str
    answer: str
    status: Status
    tool_calls: list[ExecutedCall]
    guard: GuardReport
    model: str
    data_mode: str
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class PricingCopilot:
    def __init__(self, client: LLMClient, state: AppState, *, max_tool_rounds: int = 5,
                 max_regenerations: int = 1, max_output_tokens: int = 600) -> None:
        self.client = client
        self.state = state
        self.max_tool_rounds = max_tool_rounds
        self.max_regenerations = max_regenerations
        self.max_output_tokens = max_output_tokens
        self.tool_schemas = [t.schema() for t in TOOLS.values()]

    # -- one model call, executing any tool calls it makes -----------------------
    def _converse(self, messages: list[dict], calls: list[ExecutedCall],
                  usage: dict[str, int]) -> str | None:
        for _ in range(self.max_tool_rounds):
            turn: LLMTurn = self.client.complete(messages, self.tool_schemas,
                                                 max_tokens=self.max_output_tokens)
            for k, v in turn.usage.items():
                usage[k] = usage.get(k, 0) + int(v)
            if not turn.tool_calls:
                messages.append({"role": "assistant", "content": turn.text or ""})
                return turn.text or ""
            messages.append({
                "role": "assistant",
                "content": turn.text,
                "tool_calls": [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                    for c in turn.tool_calls
                ],
            })
            for c in turn.tool_calls:
                result = run_tool(self.state, c.name, c.arguments)
                calls.append(ExecutedCall(c.name, dict(c.arguments), result))
                messages.append({"role": "tool", "tool_call_id": c.id,
                                 "content": json.dumps(result, default=str)})
        return None  # ran out of tool rounds without a final answer

    def ask(self, question: str) -> CopilotAnswer:
        started = time.perf_counter()
        messages: list[dict] = [
            {"role": "system", "content": system_prompt(self.state.data_label)},
            {"role": "user", "content": question},
        ]
        calls: list[ExecutedCall] = []
        usage: dict[str, int] = {}

        answer = self._converse(messages, calls, usage)
        report = self._check(answer, question, calls)
        status: Status = "passed"
        regenerations = 0
        while not report.passed and answer is not None and regenerations < self.max_regenerations:
            regenerations += 1
            messages.append({"role": "user", "content": (
                "Your answer failed the automatic checks. " + report.feedback()
                + " Rewrite the answer, following every rule.")})
            answer = self._converse(messages, calls, usage)
            report = self._check(answer, question, calls)
            status = "passed_after_regeneration"

        if answer is None or not report.passed:
            answer = fallback_answer(calls)
            report = self._check(answer, question, calls)
            status = "fallback_template"

        if self.state.cfg.is_synthetic:
            answer += SYNTHETIC_FOOTER
        return CopilotAnswer(
            question=question, answer=answer, status=status, tool_calls=calls, guard=report,
            model=getattr(self.client, "model", "unknown"), data_mode=self.state.data_mode,
            usage=usage, latency_ms=int(1000 * (time.perf_counter() - started)),
        )

    @staticmethod
    def _check(answer: str | None, question: str, calls: list[ExecutedCall]) -> GuardReport:
        if answer is None:
            return GuardReport(passed=False, missing_required=["a final answer"])
        return check_answer(answer, question, [c.result for c in calls], [c.arguments for c in calls])


def fallback_answer(calls: list[ExecutedCall]) -> str:
    """A deterministic answer built only from tool results (used when the guard fails)."""
    recs = [c.result for c in calls if c.name == "recommend_price" and "decision" in c.result]
    if recs:
        r = recs[-1]
        lines = [
            f"{r.get('product') or 'This product'} (UPC {r['upc']}, store {r['store']}, week {r['week']}): "
            f"decision {r['decision']}. {r['decision_meaning']}",
            f"Current price ${r['current_price']}, recommended ${r['recommended_price']} "
            f"({r['price_change_percent']}%), risk {r['risk_level']}.",
            "Reason codes: " + ", ".join(r["reason_codes"]) + ".",
        ]
        if r.get("model_internal_estimated_profit_uplift_percent") is not None:
            lines.append("Model-internal estimated profit uplift: "
                         f"{r['model_internal_estimated_profit_uplift_percent']}% "
                         "(scored by the same model that chose the price).")
        return " ".join(lines)
    errors = [c.result["error"] for c in calls if "error" in c.result]
    if errors:
        return f"The engine could not answer: {errors[-1]}"
    return ("I can only answer questions about this pricing engine's recommendations, using "
            "figures the engine returns. Try asking about a product and store, for example "
            "\"What price do you recommend for Cheerios in store 2?\"")


def build_copilot(state: AppState | None) -> PricingCopilot | None:
    """A live copilot when the OpenAI SDK and an API key are available, else None."""
    import os

    if state is None or not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        from pricing_engine.copilot.client import OpenAIChatClient

        cfg = state.cfg
        client = OpenAIChatClient(model=os.environ.get("OPENAI_MODEL") or cfg.get("copilot.model"))
    except ImportError:
        return None
    return PricingCopilot(
        client, state,
        max_tool_rounds=int(cfg.get("copilot.max_tool_rounds", 5)),
        max_regenerations=int(cfg.get("copilot.max_regenerations", 1)),
        max_output_tokens=int(cfg.get("copilot.max_output_tokens", 600)),
    )
