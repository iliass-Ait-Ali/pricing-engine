"""Readers for the raw Dominick's Cereals files.

These functions do *no* cleaning: they read the official CSVs, normalise column
names to lower case, apply memory-efficient dtypes and verify that the
documented columns are present. Everything else happens in
:mod:`pricing_engine.data.cleaning`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from pricing_engine.config import Config, load_config
from pricing_engine.data.schema import (
    MOVEMENT_REQUIRED_COLUMNS,
    UPC_REQUIRED_COLUMNS,
    SchemaError,
    assert_required_columns,
)

#: The official Dominick's CSVs are Latin-1 encoded (product descriptions
#: contain non-UTF-8 bytes), so the encoding is pinned explicitly.
ENCODING = "latin-1"

MOVEMENT_DTYPES: dict[str, str] = {
    "store": "int32",
    "upc": "int64",
    "week": "int32",
    "move": "float64",
    "qty": "float64",
    "price": "float64",
    "sale": "string",
    "profit": "float64",
    "ok": "int8",
}


class RawDataMissingError(FileNotFoundError):
    """Raised when the official raw files have not been acquired yet."""


def _raw_dir(cfg: Config | None = None) -> Path:
    cfg = cfg or load_config()
    return cfg.path("raw_dir")


def _raw_name(key: str, default: str, cfg: Config | None) -> str:
    """Raw file name from ``data.<key>``; defaults are the Cereals file names."""
    return str((cfg or load_config()).get(f"data.{key}", default))


def _require(path: Path) -> Path:
    if not path.exists():
        raise RawDataMissingError(
            f"Official Dominick's file not found: {path}. "
            "Run `python scripts/download_dominicks.py` first "
            "(or follow the manual download instructions it prints)."
        )
    return path


def load_movement(path: str | Path | None = None, *, cfg: Config | None = None) -> pd.DataFrame:
    """Load the raw Cereals weekly movement table (``wcer.csv``)."""
    csv_path = Path(path) if path else _raw_dir(cfg) / _raw_name("movement_file", "wcer.csv", cfg)
    _require(csv_path)

    header = pd.read_csv(csv_path, nrows=0, encoding=ENCODING)
    raw_names = list(header.columns)
    columns = [c.strip().lower() for c in raw_names]
    assert_required_columns(columns, MOVEMENT_REQUIRED_COLUMNS, csv_path.name)

    # The official files also carry PRICE_HEX / PROFIT_HEX (raw IEEE encodings
    # of price and profit). They are redundant for modelling and expensive in
    # memory, so they are not read.
    keep = [raw for raw, low in zip(raw_names, columns, strict=True) if low in MOVEMENT_DTYPES]
    dtypes = {
        raw: MOVEMENT_DTYPES[low]
        for raw, low in zip(raw_names, columns, strict=True)
        if low in MOVEMENT_DTYPES
    }
    df = pd.read_csv(
        csv_path,
        usecols=keep,
        dtype=dtypes,
        na_values=["", ".", "NA", "N/A"],
        keep_default_na=True,
        low_memory=False,
        encoding=ENCODING,
    )
    df.columns = [c.strip().lower() for c in df.columns]
    return df


def load_upc_metadata(path: str | Path | None = None, *, cfg: Config | None = None) -> pd.DataFrame:
    """Load the raw Cereals UPC metadata table (``upccer.csv``)."""
    csv_path = Path(path) if path else _raw_dir(cfg) / _raw_name("upc_file", "upccer.csv", cfg)
    _require(csv_path)

    df = pd.read_csv(
        csv_path,
        dtype={"UPC": "int64"},
        low_memory=False,
        encoding=ENCODING,
    )
    df.columns = [c.strip().lower() for c in df.columns]
    assert_required_columns(list(df.columns), UPC_REQUIRED_COLUMNS, csv_path.name)
    for col in ("descrip", "size"):
        df[col] = df[col].astype("string").str.strip()
    return df


def load_source_metadata(cfg: Config | None = None) -> dict:
    """Return the provenance record written by the downloader, if present."""
    path = _raw_dir(cfg) / "SOURCE.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_processed(path: str | Path | None = None, *, cfg: Config | None = None) -> pd.DataFrame:
    """Load the canonical processed Cereals table."""
    cfg = cfg or load_config()
    parquet_path = Path(path) if path else cfg.path("processed_table")
    if not parquet_path.exists():
        raise RawDataMissingError(
            f"Processed table not found: {parquet_path}. Run `python scripts/build_dataset.py`."
        )
    df = pd.read_parquet(parquet_path)
    if df.empty:
        raise SchemaError(f"Processed table {parquet_path} is empty.")
    return df
