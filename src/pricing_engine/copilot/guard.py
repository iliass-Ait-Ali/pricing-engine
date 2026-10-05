"""Deterministic checks on every copilot answer before anyone sees it.

1. **Numbers.** Every number in the answer must match a value from a tool
   result, a tool argument or the question itself, at the precision the answer
   states it ("$3.49" matches 3.4912; "8%" matches 8.4 but "9%" does not). The
   model therefore cannot invent, compute or misround a figure.
2. **Wording.** The project's claim rules (`scripts/audit_claims.py`) apply to
   the copilot too: no causal or guaranteed-outcome language unless it is
   negated, and any mention of a gain must carry "model-internal". A gain is
   recognised by its wording *or by its value*: quoting a percentage that a
   tool returned as a model-internal estimated uplift needs the label however
   the sentence is phrased (the first live run wrote "increase the gross
   profit by 43.7%", which no word list would have caught).
3. **Decision.** If a recommendation was retrieved, its decision state must be
   quoted verbatim, so "REVIEW_REQUIRED" can never be paraphrased into "raise
   the price".

Known limit: a number that does appear in the tool results but is attached to
the wrong thing in the prose ("the cost is $3.49" when $3.49 is the price)
passes check 1. The answer view therefore also shows the tool results as a
code-rendered facts table.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

#: Plain counts and ordinals ("3 policies", "step 2") carry no pricing claim.
SMALL_INTEGER_LIMIT = 12

_NUMBER = re.compile(
    r"(?<![A-Za-z_])(?P<sign>[-−+])?\$?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?P<pct>\s?%)?"
)
_LIST_MARKER = re.compile(r"^\s*(\d+)[.)]\s", re.MULTILINE)

#: Sentences that assert any of these fail unless the sentence negates them.
FORBIDDEN = {
    "causal claim": re.compile(r"\bcaus(?:e|es|ed|al|ally|ing)\b", re.I),
    "guarantee": re.compile(r"\bguarantee", re.I),
    "certain outcome": re.compile(r"\bwill\s+(?:increase|raise|boost|grow|improve|deliver|add)\b", re.I),
    "proof claim": re.compile(r"\bprov(?:e|es|en|ed)\b", re.I),
    "realised result": re.compile(r"\breali[sz]ed\s+(?:uplift|profit|gain|revenue)", re.I),
    "'optimal' price": re.compile(r"\boptimal\s+price", re.I),
}
NEGATION = re.compile(
    r"\b(?:not|never|no|nothing|none|neither|cannot|can't|isn't|aren't|doesn't|don't|without|nor|unproven|"
    r"rather than|instead of)\b", re.I
)
GAIN = re.compile(r"\b(?:uplift|gain|more profit|extra profit|profit increase)\b", re.I)
#: Tool-result keys whose values are model-internal estimated gains.
UPLIFT_KEY = "model_internal_estimated"
_PERCENT = re.compile(r"(?<![A-Za-z_\d.])(\d+(?:\.\d+)?)\s?%")


@dataclass
class GuardReport:
    passed: bool
    unsupported_numbers: list[str] = field(default_factory=list)
    forbidden_phrases: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)

    def feedback(self) -> str:
        parts = []
        if self.unsupported_numbers:
            parts.append("These numbers do not appear in any tool result: "
                         + ", ".join(self.unsupported_numbers)
                         + ". Use only numbers exactly as the tools returned them.")
        if self.forbidden_phrases:
            parts.append("Remove these claims: " + "; ".join(self.forbidden_phrases) + ".")
        if self.missing_required:
            parts.append("Missing: " + "; ".join(self.missing_required) + ".")
        return " ".join(parts)


def _walk(value: Any) -> Iterable[float]:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, int | float):
        yield float(value)
    elif isinstance(value, str):
        for m in _NUMBER.finditer(value):
            yield float(m.group("num").replace(",", ""))
    elif isinstance(value, dict):
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _walk(v)


def allowed_numbers(question: str, tool_payloads: Iterable[Any]) -> list[float]:
    values = list(_walk(question))
    for payload in tool_payloads:
        values.extend(_walk(payload))
    return values


def _supported(text: str, allowed: list[float]) -> bool:
    value = abs(float(text.replace(",", "")))
    decimals = len(text.split(".")[1]) if "." in text else 0
    tol = 0.5 * 10 ** (-decimals) + 1e-9
    return any(abs(value - abs(a)) <= tol for a in allowed)


def unsupported_numbers(answer: str, allowed: list[float]) -> list[str]:
    list_markers = {m.start(1) for m in _LIST_MARKER.finditer(answer)}
    bad = []
    for m in _NUMBER.finditer(answer):
        raw = m.group("num")
        if m.start("num") in list_markers:
            continue
        if not m.group("pct") and "." not in raw and "," not in raw and "$" not in m.group(0) \
                and int(raw) <= SMALL_INTEGER_LIMIT:
            continue
        if not _supported(raw, allowed):
            bad.append(m.group(0).strip())
    return bad


def uplift_values(payload: Any) -> Iterable[float]:
    """Non-zero numbers stored under a model-internal estimated uplift key."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if UPLIFT_KEY in str(key) and isinstance(value, int | float) and not isinstance(value, bool):
                if value:
                    yield float(value)
            else:
                yield from uplift_values(value)
    elif isinstance(payload, list | tuple):
        for item in payload:
            yield from uplift_values(item)


def quotes_an_uplift(answer: str, tool_results: Iterable[Any]) -> bool:
    """True when the answer states, as a percentage, a value a tool returned as an uplift."""
    uplifts = [v for r in tool_results for v in uplift_values(r)]
    return bool(uplifts) and any(_supported(m.group(1), uplifts) for m in _PERCENT.finditer(answer))


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def check_language(answer: str, quotes_uplift: bool = False) -> tuple[list[str], list[str]]:
    answer = answer.replace("\u2019", "'")   # a typographic apostrophe still negates
    forbidden = []
    for sentence in _sentences(answer):
        if NEGATION.search(sentence):
            continue
        for label, rx in FORBIDDEN.items():
            if rx.search(sentence):
                forbidden.append(f"{label}: \"{sentence.strip()[:80]}\"")
    missing = []
    if (GAIN.search(answer) or quotes_uplift) and "model-internal" not in answer.lower():
        missing.append('the words "model-internal estimated" next to any gain or uplift')
    return forbidden, missing


def check_answer(answer: str, question: str, tool_results: list[dict[str, Any]],
                 tool_args: list[dict[str, Any]] | None = None) -> GuardReport:
    allowed = allowed_numbers(question, [*tool_results, *(tool_args or [])])
    numbers = unsupported_numbers(answer, allowed)
    forbidden, missing = check_language(answer, quotes_an_uplift(answer, tool_results))
    decisions = {r.get("decision") for r in tool_results if isinstance(r, dict) and r.get("decision")}
    for decision in sorted(decisions):
        if decision not in answer:
            missing.append(f"the decision state {decision} quoted exactly")
    return GuardReport(passed=not (numbers or forbidden or missing), unsupported_numbers=numbers,
                       forbidden_phrases=forbidden, missing_required=missing)
