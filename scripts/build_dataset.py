"""Build the canonical Dominick's Cereals table and the data-audit report.

    python scripts/build_dataset.py
    python scripts/build_dataset.py --sample-stores 10   # faster local iteration

Outputs
-------
    data/processed/dominicks_cereals.parquet
    artifacts/metrics/raw_audit.json
    artifacts/metrics/build_audit.json
    artifacts/metrics/dataset_fingerprint.json
    reports/01_DATA_AUDIT.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.config import load_config  # noqa: E402
from pricing_engine.data.cleaning import build_canonical  # noqa: E402
from pricing_engine.data.loader import (  # noqa: E402
    load_movement,
    load_source_metadata,
    load_upc_metadata,
)
from pricing_engine.data.validator import raw_audit, validate_processed  # noqa: E402
from pricing_engine.utils.io import (  # noqa: E402
    dataframe_fingerprint,
    ensure_dir,
    environment_record,
    sha256_file,
    write_json,
)


def _md_table(rows: list[dict], columns: list[str]) -> str:
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(str(r.get(c, "")) for c in columns) + " |"
        for r in rows
    ]
    return "\n".join([header, sep, *body])


def _profile_table(profile: dict) -> str:
    cols = ["metric", "count", "missing", "mean", "std", "min", "p01", "median", "p99", "max",
            "n_zero", "n_negative"]
    rows = []
    for name, stats in profile.items():
        row = {"metric": name}
        for c in cols[1:]:
            v = stats.get(c)
            row[c] = f"{v:,.4f}" if isinstance(v, float) else (f"{v:,}" if isinstance(v, int) else "")
        rows.append(row)
    return _md_table(rows, cols)


def build_report(
    raw: dict,
    audit: dict,
    checks: list,
    source: dict,
    fingerprint: dict,
    df: pd.DataFrame,
) -> str:
    mv = raw["movement"]
    meta = raw["upc_metadata"]
    join = audit["metadata_join"]

    excl_rows = [
        {
            "rule": e["rule"],
            "rows_removed": f"{e['rows_removed']:,}",
            "pct_of_raw": f"{e['pct_of_raw']:.4f}%",
            "reason": e["reason"],
        }
        for e in audit["exclusions"]
    ]

    check_rows = [
        {"check": c.name, "result": "PASS" if c.passed else "FAIL", "detail": c.detail}
        for c in checks
    ]

    promo_counts = df["recorded_promotion_type"].value_counts(dropna=False)
    promo_rows = [
        {"recorded_promotion_type": str(k), "rows": f"{int(v):,}", "share": f"{100.0*v/len(df):.2f}%"}
        for k, v in promo_counts.items()
    ]

    flag_rows = [
        {
            "flag": name,
            "rows": f"{int(df[name].sum()):,}",
            "share": f"{100.0 * float(df[name].mean()):.3f}%",
        }
        for name in (
            "margin_implausible_flag",
            "aac_nonpositive_flag",
            "zero_move_flag",
            "bundle_flag",
            "has_metadata_flag",
        )
        if name in df.columns
    ]

    from pricing_engine.data.validator import _numeric_profile

    processed_profile = {
        col: _numeric_profile(df[col])
        for col in (
            "effective_unit_price",
            "move",
            "qty",
            "gross_margin_rate",
            "estimated_unit_aac",
            "revenue",
            "gross_profit",
        )
    }

    files = source.get("files", {})
    file_rows = [
        {
            "file": name,
            "bytes": f"{info.get('bytes', 0):,}",
            "sha256": str(info.get("sha256", ""))[:16] + "...",
            "url_or_member": info.get("url") or info.get("archive_member", ""),
        }
        for name, info in files.items()
    ]

    all_pass = all(c.passed for c in checks)

    return f"""# 01 - Data Audit (Dominick's Cereals)

**Generated:** {fingerprint['environment']['timestamp_utc']}
**Status:** {"VALIDATED" if all_pass else "FAILED CHECKS - see below"}

All numbers below were produced by `python scripts/build_dataset.py` on the
official Kilts Center files. Nothing here is hardcoded.

## 1. Source files

{_md_table(file_rows, ["file", "bytes", "sha256", "url_or_member"]) if file_rows else "_SOURCE.json not found - provenance unavailable._"}

Provider: Kilts Center for Marketing, University of Chicago Booth School of Business.
Academic-use data; not redistributed in this repository (see `docs/DATA_SOURCE.md`).

## 2. Raw movement file (before any cleaning)

| property | value |
| --- | --- |
| rows | {mv['rows']:,} |
| columns | {', '.join(mv['columns'])} |
| distinct UPCs | {mv['n_upc']:,} |
| distinct stores | {mv['n_store']:,} |
| distinct weeks | {mv['n_week']:,} |
| week index range | {mv['week_min']} .. {mv['week_max']} |
| exact duplicate rows | {mv['exact_duplicate_rows']:,} |
| duplicate (upc, store, week) rows | {mv['duplicate_grain_rows']:,} |

### Dtypes and missingness (raw)

{_md_table([{ 'column': c, 'dtype': mv['dtypes'][c], 'nulls': f"{mv['null_counts'][c]:,}" } for c in mv['columns']], ['column', 'dtype', 'nulls'])}

### ok flag (manual: 1 = valid, 0 = suspect / trash)

{_md_table([{ 'ok': k, 'rows': f"{v:,}" } for k, v in mv['ok_value_counts'].items()], ['ok', 'rows'])}

### Recorded sale codes (B = Bonus Buy, C = Coupon, S = simple price reduction)

{_md_table([{ 'sale': k, 'rows': f"{v:,}" } for k, v in mv['sale_code_counts'].items()], ['sale', 'rows'])}

The manual states promotion coding is incomplete: a present code indicates a
promotion, but a missing code does **not** prove that no promotion occurred.

### Raw numeric distributions

{_profile_table(mv['profile'])}

## 3. Raw UPC metadata file

| property | value |
| --- | --- |
| rows | {meta['rows']:,} |
| distinct UPCs | {meta['n_upc']:,} |
| duplicate UPC rows | {meta['duplicate_upc']:,} |
| columns | {', '.join(meta['columns'])} |

## 4. Exclusions applied (every rule counted)

Raw rows: **{audit['raw_rows']:,}** -> canonical rows: **{audit['processed_rows']:,}**
(removed {audit['rows_removed']:,}, {100.0*audit['rows_removed']/audit['raw_rows']:.3f}% of raw).

{_md_table(excl_rows, ["rule", "rows_removed", "pct_of_raw", "reason"])}

## 5. Metadata join quality

| property | value |
| --- | --- |
| movement UPCs | {join['movement_upcs']:,} |
| metadata UPCs | {join['metadata_upcs']:,} |
| movement UPCs without metadata | {join['movement_upcs_without_metadata']:,} |
| metadata UPCs without movement | {join['metadata_upcs_without_movement']:,} |
| rows without metadata (kept, flagged) | {join['rows_without_metadata']:,} ({join['pct_rows_without_metadata']:.3f}%) |
| duplicate metadata UPC rows | {join['duplicate_metadata_upcs']:,} |

The join is executed with `validate="many_to_one"`, so it cannot multiply rows;
the row count is asserted before/after.

## 6. Canonical table

| property | value |
| --- | --- |
| grain | one valid UPC x store x week observation |
| rows | {audit['processed_rows']:,} |
| UPCs | {audit['upcs']:,} |
| stores | {audit['stores']:,} |
| weeks | {audit['weeks']:,} |
| week index range | {audit['week_min']} .. {audit['week_max']} |
| calendar range | {audit['date_min']} .. {audit['date_max']} |
| total units sold (historical observed) | {audit['total_units']:,.0f} |
| total revenue (historical observed) | ${audit['total_revenue']:,.2f} |
| total gross profit (historical observed) | ${audit['total_gross_profit']:,.2f} |
| parquet sha256 | `{fingerprint['parquet_sha256']}` |
| content fingerprint | `{fingerprint['dataframe_fingerprint']}` |

### Processed numeric distributions

{_profile_table(processed_profile)}

### Recorded promotion coding

{_md_table(promo_rows, ["recorded_promotion_type", "rows", "share"])}

### Quality flags (kept, not dropped)

{_md_table(flag_rows, ["flag", "rows", "share"])}

## 7. Formula and structural checks

{_md_table(check_rows, ["check", "result", "detail"])}

## 8. Reproducibility

| property | value |
| --- | --- |
| python | {fingerprint['environment']['python']} |
| pandas | {fingerprint['environment']['pandas']} |
| numpy | {fingerprint['environment']['numpy']} |
| platform | {fingerprint['environment']['platform']} |
| git commit | {fingerprint['environment']['git_commit']} |

Command: `python scripts/build_dataset.py`
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-stores", type=int, default=None,
                        help="Keep only the N lowest store ids (development shortcut).")
    parser.add_argument("--out", default=None, help="Override output parquet path.")
    args = parser.parse_args()

    cfg = load_config()
    print("[1/6] loading raw movement file ...")
    movement = load_movement(cfg=cfg)
    print(f"      {len(movement):,} raw rows, columns={list(movement.columns)}")

    print("[2/6] loading raw UPC metadata ...")
    upc_meta = load_upc_metadata(cfg=cfg)
    print(f"      {len(upc_meta):,} metadata rows")

    if args.sample_stores:
        keep = sorted(movement["store"].unique())[: args.sample_stores]
        movement = movement[movement["store"].isin(keep)]
        print(f"      development sample: {len(keep)} stores, {len(movement):,} rows")

    print("[3/6] auditing raw data ...")
    raw = raw_audit(movement, upc_meta)
    write_json(cfg.path("metrics_dir") / "raw_audit.json", raw)

    print("[4/6] building canonical table ...")
    df, audit = build_canonical(movement, upc_meta, cfg=cfg)
    print(f"      {len(df):,} canonical rows ({audit['rows_removed']:,} removed)")

    out_path = Path(args.out) if args.out else cfg.path("processed_table")
    ensure_dir(out_path.parent)
    df.to_parquet(out_path, index=False)
    print(f"      wrote {out_path}")

    print("[5/6] validating canonical table ...")
    checks = validate_processed(df)
    for c in checks:
        print(f"      [{'PASS' if c.passed else 'FAIL'}] {c.name}: {c.detail}")

    fingerprint = {
        "parquet_path": str(out_path.relative_to(REPO_ROOT)),
        "parquet_sha256": sha256_file(out_path),
        "dataframe_fingerprint": dataframe_fingerprint(df),
        "rows": int(len(df)),
        "columns": list(df.columns),
        "environment": environment_record(),
    }
    write_json(cfg.path("metrics_dir") / "dataset_fingerprint.json", fingerprint)
    audit["checks"] = [c.as_dict() for c in checks]
    write_json(cfg.path("metrics_dir") / "build_audit.json", audit)

    print("[6/6] writing reports/01_DATA_AUDIT.md ...")
    source = load_source_metadata(cfg)
    report = build_report(raw, audit, checks, source, fingerprint, df)
    report_path = cfg.path("reports_dir") / "01_DATA_AUDIT.md"
    ensure_dir(report_path.parent)
    report_path.write_text(report, encoding="utf-8")
    print(f"      wrote {report_path}")

    failed = [c.name for c in checks if not c.passed]
    if failed:
        print(f"\nFAILED CHECKS: {failed}")
        return 1
    print("\nAll data checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
