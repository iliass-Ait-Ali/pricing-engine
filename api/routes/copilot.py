"""Pricing Copilot endpoint: ask a question, get a guarded, tool-grounded answer."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(tags=["copilot"])


class CopilotRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000,
                          description="A question about the engine's recommendations")


class CopilotToolCall(BaseModel):
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]


class CopilotResponse(BaseModel):
    answer: str
    status: str = Field(..., description="passed | passed_after_regeneration | fallback_template")
    tool_calls: list[CopilotToolCall]
    unsupported_numbers: list[str]
    model: str
    data_mode: str
    latency_ms: int


class RateLimiter:
    """Sliding one-hour window per client, plus a global daily cap (public demo cost control)."""

    def __init__(self, per_client_per_hour: int, daily_limit: int) -> None:
        self.per_client = per_client_per_hour
        self.daily_limit = daily_limit
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._day = date.today()
        self._today = 0

    def allow(self, client: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        if date.today() != self._day:
            self._day, self._today = date.today(), 0
        hits = self._hits[client]
        while hits and now - hits[0] > 3600:
            hits.popleft()
        if len(hits) >= self.per_client or self._today >= self.daily_limit:
            return False
        hits.append(now)
        self._today += 1
        return True


def _client_id(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


@router.post("/copilot/ask", response_model=CopilotResponse)
def ask(payload: CopilotRequest, request: Request) -> CopilotResponse:
    copilot = getattr(request.app.state, "copilot", None)
    if copilot is None:
        raise HTTPException(
            status_code=503,
            detail="The copilot is disabled: set OPENAI_API_KEY and install the genai extra.",
        )
    limiter: RateLimiter | None = getattr(request.app.state, "copilot_limiter", None)
    if limiter is not None and not limiter.allow(_client_id(request)):
        raise HTTPException(status_code=429, detail="Copilot rate limit reached; try again later.")

    out = copilot.ask(payload.question)
    return CopilotResponse(
        answer=out.answer,
        status=out.status,
        tool_calls=[CopilotToolCall(name=c.name, arguments=c.arguments, result=c.result)
                    for c in out.tool_calls],
        unsupported_numbers=out.guard.unsupported_numbers,
        model=out.model,
        data_mode=out.data_mode,
        latency_ms=out.latency_ms,
    )
