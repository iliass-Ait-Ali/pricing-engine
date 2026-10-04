"""Application state for the API.

The implementation lives in :mod:`pricing_engine.serving` so the API, the
dashboard and the pricing copilot share one loader and one set of pricing
operations. This module keeps the original import path working.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.serving import (  # noqa: E402,F401
    SERVING_WEEKS,
    STAT_COLUMNS,
    AppState,
    ContextNotFound,
    ServingError,
    build_state,
)
