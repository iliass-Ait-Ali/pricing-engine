"""FastAPI service for the AI Pricing & Revenue Optimization Engine.

    uvicorn api.main:app --reload
    make api

The demand model and the serving context slice are loaded once during startup
(lifespan), not per request.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from api.routes.copilot import RateLimiter  # noqa: E402
from api.routes.copilot import router as copilot_router  # noqa: E402
from api.routes.pricing import router as pricing_router  # noqa: E402
from api.schemas import HealthResponse, ModelInfoResponse  # noqa: E402
from api.state import build_state  # noqa: E402
from pricing_engine import __version__  # noqa: E402
from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.copilot.agent import build_copilot  # noqa: E402

SYNTHETIC_NOTE = """
**SYNTHETIC DEMO DATA.** This instance serves a generated panel with known
elasticities, not Dominick's data (whose licence forbids redistribution). The
engine, guardrails and policy are the same code as the real run; every number
it returns describes the synthetic panel only.
"""

DESCRIPTION = """
Price recommendation service built on the Dominick's Finer Foods Cereals
scanner panel (Kilts Center, University of Chicago Booth).

**Scientific note.** All counterfactual economics returned by this API are
*model-internal estimates*: demand at prices that were never charged was never
observed, and the same fitted price-response model both proposes and scores the
candidate prices. Nothing here is a causal or realised uplift.

**Decision states.** RECOMMEND_CHANGE is actionable; KEEP_CURRENT leaves the
price alone; REVIEW_REQUIRED means a proposal exists but a human must approve
it - HIGH-risk contexts never auto-change a price under the default policy.
"""


def _description(cfg) -> str:
    return (SYNTHETIC_NOTE if cfg.is_synthetic else "") + DESCRIPTION


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.cfg = load_config()
    # The docs page states which data this instance serves.
    app.description = _description(app.state.cfg)
    app.openapi_schema = None
    try:
        app.state.engine = build_state(app.state.cfg)
    except FileNotFoundError as exc:  # keep /health informative instead of crashing
        app.state.engine = None
        app.state.startup_error = str(exc)
    # Optional: only when OPENAI_API_KEY is set and the genai extra is installed.
    app.state.copilot = build_copilot(app.state.engine)
    app.state.copilot_limiter = RateLimiter(
        int(app.state.cfg.get("copilot.rate_limit_per_client_per_hour", 20)),
        int(app.state.cfg.get("copilot.daily_limit", 300)),
    )
    yield
    app.state.engine = None
    app.state.copilot = None


CFG = load_config()

app = FastAPI(
    title="AI Pricing & Revenue Optimization Engine",
    # One source of truth for the project version: src/pricing_engine/__init__.py
    version=__version__,
    description=_description(CFG),
    lifespan=lifespan,
)
app.include_router(pricing_router)
app.include_router(copilot_router)


def _cfg(request: Request):
    """The config the running app was started with (falls back to import time)."""
    return getattr(request.app.state, "cfg", CFG)


@app.middleware("http")
async def label_data_mode(request: Request, call_next):
    """Every response says which data it describes (licensed or synthetic)."""
    response = await call_next(request)
    response.headers["X-Data-Mode"] = _cfg(request).data_mode
    return response


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health(request: Request) -> HealthResponse:
    state = getattr(request.app.state, "engine", None)
    return HealthResponse(
        model_loaded=state is not None,
        contexts_loaded=int(len(state.contexts)) if state else 0,
        decision_weeks=state.weeks if state else [],
        data_mode=_cfg(request).data_mode,
        data_label=_cfg(request).data_label,
    )


@app.get("/model/info", response_model=ModelInfoResponse, tags=["meta"])
def model_info(request: Request) -> ModelInfoResponse:
    state = getattr(request.app.state, "engine", None)
    meta = getattr(state.model, "metadata", None) if state else None
    table = getattr(state.model, "elasticity_table", None) if state else None
    elas_meta = dict(getattr(table, "metadata", {}) or {})
    training_weeks = [
        int(elas_meta[k])
        for k in ("training_weeks_low", "training_weeks_high")
        if elas_meta.get(k) is not None
    ]
    return ModelInfoResponse(
        data_mode=_cfg(request).data_mode,
        data_label=_cfg(request).data_label,
        price_response_method=(
            getattr(getattr(state.model, "method", None), "value", None) if state else None
        ),
        pooled_elasticity=float(table.pooled) if table is not None else None,
        elasticity_training_weeks=training_weeks,
        name=getattr(meta, "name", None),
        kind=getattr(meta, "kind", None),
        version=getattr(meta, "version", None),
        trained_at_utc=getattr(meta, "trained_at_utc", None),
        target=getattr(meta, "target", None),
        n_features=len(getattr(meta, "feature_columns", []) or []),
        feature_columns=list(getattr(meta, "feature_columns", []) or []),
        train_weeks=list(getattr(meta, "train_weeks", []) or []),
        valid_weeks=list(getattr(meta, "valid_weeks", []) or []),
        test_weeks=list(getattr(meta, "test_weeks", []) or []),
        metrics=dict(getattr(meta, "metrics", {}) or {}),
        data_fingerprint=getattr(meta, "data_fingerprint", None),
    )
