"""Small I/O, hashing and reproducibility helpers."""

from __future__ import annotations

import hashlib
import json
import platform
import random
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def utc_now() -> str:
    """ISO-8601 UTC timestamp."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_json(path: str | Path, payload: Any) -> Path:
    """Write JSON, creating parent directories, with numpy-safe conversion."""
    p = Path(path)
    ensure_dir(p.parent)
    p.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")
    return p


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.ndarray,)):
        return obj.tolist()
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serialisable")


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def dataframe_fingerprint(df: pd.DataFrame) -> str:
    """Content fingerprint of a DataFrame (shape + column names + value hash)."""
    hasher = hashlib.sha256()
    hasher.update(str(df.shape).encode())
    hasher.update(",".join(map(str, df.columns)).encode())
    hashed = pd.util.hash_pandas_object(df, index=False).to_numpy()
    hasher.update(hashed.tobytes())
    return hasher.hexdigest()


def git_commit() -> str | None:
    """Return the current git commit hash when available."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001 - provenance is best-effort
        return None


def environment_record() -> dict[str, Any]:
    """Capture the reproducibility context of the current run."""
    import sklearn

    return {
        "timestamp_utc": utc_now(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scikit_learn": sklearn.__version__,
        "git_commit": git_commit(),
    }


def set_seed(seed: int) -> None:
    """Seed the RNGs this project uses."""
    random.seed(seed)
    np.random.seed(seed)


def fmt_int(x: float | int) -> str:
    return f"{int(x):,}"


def fmt_money(x: float) -> str:
    return f"${x:,.2f}"


def fmt_pct(x: float, digits: int = 2) -> str:
    return f"{100.0 * x:.{digits}f}%"
