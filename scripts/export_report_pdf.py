"""Export the PDF edition of the full report from the DOCX, and count its pages.

    python scripts/build_report_docx.py     # Markdown -> DOCX
    python scripts/export_report_pdf.py     # DOCX -> PDF (+ page count)

The Markdown remains the single source of truth. This script never edits
content: it drives Microsoft Word through COM automation to

1. repaginate the document and update the table-of-contents field, so the TOC
   page numbers match the final layout;
2. export to PDF;
3. read back the final page count and write it to
   ``artifacts/metrics/report_build.json``.

Word is required (Windows). Without it the script reports NOT AVAILABLE and
exits non-zero rather than producing a stale or hand-made PDF.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from pricing_engine.utils.io import write_json  # noqa: E402

DOCX = REPO_ROOT / "reports" / "AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.docx"
PDF = REPO_ROOT / "reports" / "AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.pdf"
SRC = REPO_ROOT / "reports" / "AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md"
OUT = REPO_ROOT / "artifacts" / "metrics" / "report_build.json"

WD_EXPORT_FORMAT_PDF = 17
WD_STATISTIC_PAGES = 2
WD_STATISTIC_WORDS = 0
WD_DO_NOT_SAVE_CHANGES = 0


def export() -> dict:
    try:
        import win32com.client  # type: ignore
    except ImportError as exc:  # pragma: no cover - platform dependent
        raise SystemExit(
            "pywin32 is not installed, so the PDF cannot be generated from the "
            f"DOCX on this machine ({exc}). Install pywin32 on Windows with "
            "Microsoft Word, or export the DOCX to PDF manually and record that "
            "it was a manual step."
        ) from exc

    if not DOCX.exists():
        raise SystemExit(f"{DOCX} does not exist - run scripts/build_report_docx.py first.")

    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = False
    try:
        doc = word.Documents.Open(str(DOCX), ReadOnly=False)
        try:
            # Repaginate, then refresh every field (the TOC is a field) so the
            # printed page numbers match this build.
            doc.Repaginate()
            for toc in doc.TablesOfContents:
                toc.Update()
            doc.Fields.Update()
            doc.Repaginate()
            pages = int(doc.ComputeStatistics(WD_STATISTIC_PAGES))
            words = int(doc.ComputeStatistics(WD_STATISTIC_WORDS))
            headings = {
                level: sum(
                    1
                    for p in doc.Paragraphs
                    if p.Style.NameLocal in (f"Heading {level}", f"Titre {level}")
                )
                for level in (1, 2, 3)
            }
            doc.Save()
            doc.ExportAsFixedFormat(
                OutputFileName=str(PDF),
                ExportFormat=WD_EXPORT_FORMAT_PDF,
                OpenAfterExport=False,
                CreateBookmarks=1,  # wdExportCreateHeadingBookmarks
                DocStructureTags=True,
            )
        finally:
            doc.Close(WD_DO_NOT_SAVE_CHANGES)
    finally:
        word.Quit()

    return {
        "source_markdown": SRC.relative_to(REPO_ROOT).as_posix(),
        "docx": DOCX.relative_to(REPO_ROOT).as_posix(),
        "pdf": PDF.relative_to(REPO_ROOT).as_posix(),
        "pages": pages,
        "words_word_count": words,
        "headings": {f"heading_{k}": v for k, v in headings.items()},
        "docx_bytes": DOCX.stat().st_size,
        "pdf_bytes": PDF.stat().st_size,
        "markdown_lines": len(SRC.read_text(encoding="utf-8").splitlines()),
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "note": (
            "Generated from the Markdown source of truth: "
            "scripts/build_report_docx.py then scripts/export_report_pdf.py. "
            "The DOCX and PDF are never edited by hand."
        ),
    }


def main() -> int:
    summary = export()
    write_json(OUT, summary)
    print(f"pages: {summary['pages']}")
    print(f"words (Word's own count): {summary['words_word_count']:,}")
    print(f"headings: {summary['headings']}")
    print(f"wrote {PDF}")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
