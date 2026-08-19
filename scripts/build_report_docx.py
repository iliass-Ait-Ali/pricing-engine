"""Build the Microsoft Word edition of the full project report.

Converts ``reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md`` into a
styled ``.docx``:

* a cover page, a live Word table-of-contents field and page numbers;
* every heading, paragraph, list, block quote and fenced code block;
* all 278 markdown tables, with a shaded header row and column-count-aware
  font sizing;
* all 23 figures, scaled to fit the page;
* all 10 Mermaid diagrams, embedded as the PNGs rendered by
  ``scripts/render_mermaid.py``;
* LaTeX display equations transliterated to Unicode and centred.

    python scripts/render_mermaid.py      # once, to produce the diagram PNGs
    python scripts/build_report_docx.py

Output: ``reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.docx``
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _latex_unicode import latex_to_unicode  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "reports" / "AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md"
DST = ROOT / "reports" / "AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.docx"
MERMAID = ROOT / "artifacts" / "report_figures" / "mermaid"
METRICS = ROOT / "artifacts" / "metrics"

#: The edition of the report itself, kept in step with the project version.
REPORT_VERSION = "1.0"

BODY_FONT = "Calibri"
MONO_FONT = "Consolas"
MATH_FONT = "Cambria Math"

INK = RGBColor(0x1A, 0x20, 0x2C)
BLUE = RGBColor(0x1F, 0x4E, 0x79)
GREY = RGBColor(0x5A, 0x64, 0x70)
CODE_INK = RGBColor(0x2D, 0x37, 0x48)

HEADER_FILL = "1F4E79"
CODE_FILL = "F4F6F8"
QUOTE_FILL = "FBF7EC"

MAX_W_IN = 6.6          # usable width  on A4 with 0.8 in margins
MAX_H_IN = 9.2          # usable height on A4 with 0.8 in margins


# ---------------------------------------------------------------------------
# low-level docx helpers
# ---------------------------------------------------------------------------
def shade(element, fill: str) -> None:
    """Apply a solid background fill to a cell or paragraph."""
    node = OxmlElement("w:shd")
    node.set(qn("w:val"), "clear")
    node.set(qn("w:color"), "auto")
    node.set(qn("w:fill"), fill)
    element.append(node)


def cell_shade(cell, fill: str) -> None:
    shade(cell._tc.get_or_add_tcPr(), fill)


def para_shade(par, fill: str) -> None:
    shade(par._p.get_or_add_pPr(), fill)


def para_border(par, *, left: bool = False, top: bool = False) -> None:
    pPr = par._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    for side, on, sz, col in (("left", left, "18", "C89B3C"), ("top", top, "6", "D0D7DE")):
        if not on:
            continue
        b = OxmlElement(f"w:{side}")
        b.set(qn("w:val"), "single")
        b.set(qn("w:sz"), sz)
        b.set(qn("w:space"), "8")
        b.set(qn("w:color"), col)
        borders.append(b)
    if len(borders):
        pPr.append(borders)


def field(par, instr: str, placeholder: str = "") -> None:
    """Insert a Word field code (used for the TOC and page numbers)."""
    r1 = par.add_run()._r
    fc = OxmlElement("w:fldChar")
    fc.set(qn("w:fldCharType"), "begin")
    r1.append(fc)

    r2 = par.add_run()._r
    it = OxmlElement("w:instrText")
    it.set(qn("xml:space"), "preserve")
    it.text = instr
    r2.append(it)

    r3 = par.add_run()._r
    fs = OxmlElement("w:fldChar")
    fs.set(qn("w:fldCharType"), "separate")
    r3.append(fs)

    if placeholder:
        par.add_run(placeholder)

    r4 = par.add_run()._r
    fe = OxmlElement("w:fldChar")
    fe.set(qn("w:fldCharType"), "end")
    r4.append(fe)


def set_repeat_header(row) -> None:
    trPr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    trPr.append(el)


# ---------------------------------------------------------------------------
# inline markdown
# ---------------------------------------------------------------------------
INLINE = re.compile(
    r"(\*\*\*.+?\*\*\*|\*\*.+?\*\*|(?<![\w*])\*[^*\n]+?\*(?![\w*])|`[^`]+`|\[[^\]]+\]\([^)]+\))",
    re.S,
)


def add_inline(par, text: str, *, size: float = 10.5, colour=INK,
               bold=False, italic=False, _unescape=True):
    """Add text to a paragraph, honouring **bold**, *italic*, `code` and links.

    Emphasis spans are re-parsed rather than emitted verbatim, so a code span
    nested inside bold - ``**`COST_FLOOR`: ...**`` - keeps its monospace face
    instead of showing literal backticks.
    """
    if _unescape:
        text = text.replace(r"\|", "|").replace(r"\_", "_").replace(r"\*", "*")

    def recurse(inner: str, **flags):
        add_inline(par, inner, size=size, colour=colour, _unescape=False,
                   **{"bold": bold, "italic": italic, **flags})

    for token in INLINE.split(text):
        if not token:
            continue
        if token.startswith("***") and token.endswith("***") and len(token) > 6:
            recurse(token[3:-3], bold=True, italic=True)
            continue
        if token.startswith("**") and token.endswith("**") and len(token) > 4:
            recurse(token[2:-2], bold=True)
            continue
        if token.startswith("*") and token.endswith("*") and len(token) > 2 \
                and not token.startswith("**"):
            recurse(token[1:-1], italic=True)
            continue

        if token.startswith("`") and token.endswith("`") and len(token) > 2:
            run = par.add_run(token[1:-1])
            run.font.name = MONO_FONT
            run.font.size = Pt(size - 1.0)
            run.font.color.rgb = CODE_INK
            par_run_shade(run)
        elif token.startswith("[") and "](" in token:
            run = par.add_run(token[1:token.index("](")])
            run.font.color.rgb = BLUE
            run.underline = True
        else:
            run = par.add_run(token)

        if run.font.name is None:
            run.font.name = BODY_FONT
        if run.font.size is None:
            run.font.size = Pt(size)
        if run.font.color.rgb is None:
            run.font.color.rgb = colour
        if bold:
            run.bold = True
        if italic:
            run.italic = True
    return par


def par_run_shade(run) -> None:
    rPr = run._r.get_or_add_rPr()
    node = OxmlElement("w:shd")
    node.set(qn("w:val"), "clear")
    node.set(qn("w:fill"), "EEF1F4")
    rPr.append(node)


# ---------------------------------------------------------------------------
# document scaffolding
# ---------------------------------------------------------------------------
def setup(doc: Document) -> None:
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)      # A4
    for attr in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(sec, attr, Inches(0.8))

    normal = doc.styles["Normal"]
    normal.font.name = BODY_FONT
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = INK
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.12

    sizes = {"Heading 1": 20, "Heading 2": 15, "Heading 3": 12.5, "Heading 4": 11}
    for name, size in sizes.items():
        st = doc.styles[name]
        st.font.name = BODY_FONT
        st.font.size = Pt(size)
        st.font.color.rgb = BLUE
        st.font.bold = True
        st.paragraph_format.space_before = Pt(16 if name == "Heading 1" else 11)
        st.paragraph_format.space_after = Pt(6)
        st.paragraph_format.keep_with_next = True


def footer_page_numbers(doc: Document) -> None:
    for sec in doc.sections:
        p = sec.footer.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run("AI Pricing & Revenue Optimization Engine  ·  page ")
        r.font.size, r.font.color.rgb, r.font.name = Pt(8), GREY, BODY_FONT
        field(p, "PAGE", "1")
        r2 = p.add_run(" of ")
        r2.font.size, r2.font.color.rgb, r2.font.name = Pt(8), GREY, BODY_FONT
        field(p, "NUMPAGES", "1")
        for run in p.runs:
            run.font.size, run.font.color.rgb = Pt(8), GREY


def cover(doc: Document, meta: list[tuple[str, str]], n_level3: int = 0) -> None:
    for _ in range(2):
        doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("AI Pricing & Revenue\nOptimization Engine")
    r.font.size, r.font.bold, r.font.color.rgb, r.font.name = Pt(34), True, BLUE, BODY_FONT

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("Full Technical Report")
    r.font.size, r.font.color.rgb, r.font.name = Pt(19), GREY, BODY_FONT

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(
        "Dominick's Finer Foods Cereals scanner panel\n"
        "Kilts Center for Marketing, University of Chicago Booth"
    )
    r.font.size, r.font.italic, r.font.color.rgb, r.font.name = Pt(11.5), True, GREY, BODY_FONT
    doc.add_paragraph()

    tbl = doc.add_table(rows=0, cols=2)
    tbl.style = "Table Grid"
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    for k, v in meta:
        cells = tbl.add_row().cells
        for cell, txt, bold in ((cells[0], k, True), (cells[1], v, False)):
            cell.text = ""
            add_inline(cell.paragraphs[0], txt, size=9, bold=bold)
            cell.paragraphs[0].paragraph_format.space_after = Pt(2)
        cell_shade(cells[0], "F4F6F8")
    tbl.columns[0].width, tbl.columns[1].width = Inches(2.1), Inches(4.5)

    doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(
        "Nothing in this report is a causal claim. Every counterfactual figure is a\n"
        "MODEL-INTERNAL estimate: the same fitted price-response model both proposes\n"
        "and scores the candidate prices."
    )
    r.font.size, r.font.bold, r.font.color.rgb, r.font.name = Pt(9.5), True, RGBColor(0xB0, 0x3A, 0x2B), BODY_FONT
    para_shade(p, "FDF3F2")

    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    h = doc.add_paragraph()
    r = h.add_run("Contents")
    r.font.size, r.font.bold, r.font.color.rgb, r.font.name = Pt(20), True, BLUE, BODY_FONT
    note = doc.add_paragraph()
    r = note.add_run(
        f"Parts and numbered sections are listed below; the {n_level3:,} third-level "
        "headings are omitted to keep this to a few pages. Entries are clickable, and "
        "the contents can be rebuilt at any time by right-clicking it and choosing "
        "Update Field."
    )
    r.font.size, r.font.italic, r.font.color.rgb, r.font.name = Pt(9), True, GREY, BODY_FONT
    toc = doc.add_paragraph()
    field(toc, r'TOC \o "1-2" \h \z \u', "  (right-click and choose Update Field)")
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


# ---------------------------------------------------------------------------
# block builders
# ---------------------------------------------------------------------------
def add_image(doc: Document, path: Path, *, own_page: bool = False) -> bool:
    if not path.exists():
        return False
    with Image.open(path) as im:
        px_w, px_h = im.size
    dpi = 96.0
    # report figures are matplotlib at 130 dpi; screenshots/mermaid are 2x or 4x scaled
    if "report_figures" in str(path) and path.name.startswith("fig_"):
        dpi = 130.0
    elif path.parent.name == "mermaid":
        dpi = 384.0                      # device-scale 2 x CDP clip scale 2 x 96
    elif path.name.startswith(("dashboard_", "api_")):
        dpi = 96.0
    w_in, h_in = px_w / dpi, px_h / dpi
    scale = min(MAX_W_IN / w_in, MAX_H_IN / h_in, 1.6)
    w_in, h_in = w_in * scale, h_in * scale

    if own_page or h_in > 6.4:
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(2)
    p.add_run().add_picture(str(path), width=Inches(w_in))
    return True


def add_code(doc: Document, lines: list[str], lang: str) -> None:
    body = "\n".join(lines).rstrip()
    if not body:
        return
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Inches(0.12)
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(8)
    p.paragraph_format.line_spacing = 1.0
    para_shade(p, CODE_FILL)
    para_border(p, left=True)
    size = 8.0 if max((len(x) for x in body.split("\n")), default=0) > 92 else 8.8
    r = p.add_run(body)
    r.font.name, r.font.size, r.font.color.rgb = MONO_FONT, Pt(size), CODE_INK
    if lang:
        cap = doc.add_paragraph()
        cap.paragraph_format.space_after = Pt(8)
        rc = cap.add_run(f"{lang}")
        rc.font.size, rc.font.italic, rc.font.color.rgb, rc.font.name = Pt(7.5), True, GREY, BODY_FONT


def add_math(doc: Document, latex: str) -> None:
    for line in latex_to_unicode(latex).split("\n"):
        if not line.strip():
            continue
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(4)
        p.paragraph_format.space_after = Pt(6)
        r = p.add_run(line.strip())
        r.font.name, r.font.size, r.font.color.rgb = MATH_FONT, Pt(11.5), INK


def split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    parts, buf, esc = [], "", False
    for ch in line:
        if esc:
            buf += "\\" + ch if ch != "|" else "|"
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == "|":
            parts.append(buf.strip())
            buf = ""
        else:
            buf += ch
    parts.append(buf.strip())
    return parts


def add_table(doc: Document, rows: list[list[str]], aligns: list[str]) -> None:
    ncol = max(len(r) for r in rows)
    size = 9.0 if ncol <= 4 else 8.2 if ncol <= 6 else 7.4 if ncol <= 9 else 6.6
    tbl = doc.add_table(rows=0, cols=ncol)
    tbl.style = "Table Grid"
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl.autofit = True

    for ri, row in enumerate(rows):
        cells = tbl.add_row().cells
        for ci in range(ncol):
            txt = row[ci] if ci < len(row) else ""
            cell = cells[ci]
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(1.5)
            p.paragraph_format.space_after = Pt(1.5)
            p.paragraph_format.line_spacing = 1.0
            if ci < len(aligns):
                if aligns[ci] == "right":
                    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                elif aligns[ci] == "center":
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            add_inline(p, txt, size=size)
            if ri == 0:
                cell_shade(cell, HEADER_FILL)
                for run in p.runs:
                    run.bold = True
                    run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                    # a code span's pale background is unreadable on the dark
                    # header fill, so drop it and keep only the monospace face
                    rPr = run._r.find(qn("w:rPr"))
                    if rPr is not None:
                        for shd in rPr.findall(qn("w:shd")):
                            rPr.remove(shd)
            elif ri % 2 == 0:
                cell_shade(cell, "F7F9FB")
        if ri == 0:
            set_repeat_header(tbl.rows[0])

    doc.add_paragraph().paragraph_format.space_after = Pt(4)


def add_quote(doc: Document, lines: list[str]) -> None:
    text = "\n".join(lines).strip()
    if not text:
        return
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Inches(0.22)
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(8)
    para_shade(p, QUOTE_FILL)
    para_border(p, left=True)
    add_inline(p, text, size=10.5, italic=True)


# ---------------------------------------------------------------------------
# main conversion
# ---------------------------------------------------------------------------
def _artifact(name: str) -> dict:
    return json.loads((METRICS / name).read_text(encoding="utf-8"))


def cover_metadata() -> list[tuple[str, str]]:
    """Cover-page facts, read from the artifacts that own them.

    Hand-typing these is how a cover page ends up claiming a test count or a
    WAPE the pipeline no longer produces.
    """
    fingerprint = _artifact("dataset_fingerprint.json")
    models = _artifact("model_metrics.json")
    evaluation = _artifact("evaluation.json")
    elasticity = _artifact("elasticity_estimation.json")
    attribution = _artifact("constraint_attribution.json")
    tests = _artifact("test_suite.json")
    claims = _artifact("claim_audit.json")
    unsupported = claims.get("counts", {}).get("UNSUPPORTED", claims.get("n_unsupported", 0))
    return [
        ("Report version", REPORT_VERSION),
        ("Report generated", datetime.now(UTC).date().isoformat()),
        ("Dataset", f"Dominick's Cereals — {fingerprint['rows']:,} UPC × store × week rows"),
        (
            "Selected model",
            f"{models['selected']} — test WAPE {evaluation['test_metrics']['wape']:.4f}",
        ),
        (
            "Pricing elasticity",
            f"pooled {elasticity['pooled_elasticity']:.3f}".replace("-", "\u2212")
            + ", two-way clustered 95% CI ["
            + f"{elasticity['pooled_ci_low_two_way']:.2f}".replace("-", "\u2212")
            + ", "
            + f"{elasticity['pooled_ci_high_two_way']:.2f}".replace("-", "\u2212")
            + "]",
        ),
        (
            "Headline audit finding",
            f"{100 * attribution['share_determined_by_learned_signal']:.1f}% of recommendations "
            "come from an interior model optimum",
        ),
        (
            "Quality gates",
            f"{tests['collected_tests']} tests passed · ruff clean · "
            f"{unsupported} unsupported claims",
        ),
        ("Companion files", "…REPORT_SOURCES.md (evidence trail) · REPORT_QA.md (QA record)"),
    ]


def convert() -> int:
    if not SRC.exists():
        print(f"missing {SRC}", file=sys.stderr)
        return 1
    md = SRC.read_text(encoding="utf-8")
    lines = md.split("\n")

    doc = Document()
    setup(doc)

    n_level3 = sum(1 for ln in lines if ln.startswith('### '))
    cover(doc, cover_metadata(), n_level3)

    stats = {"h": 0, "p": 0, "tbl": 0, "img": 0, "mermaid": 0, "code": 0, "math": 0, "quote": 0}
    mermaid_idx = 0
    pending_caption_image = False
    i = 0
    in_toc = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # ---- skip the markdown TOC (Word generates its own) ------------------
        if stripped.startswith("## Table of contents"):
            in_toc = True
            i += 1
            continue
        if in_toc:
            if re.match(r"^#{1,6}\s", stripped) or stripped == "---":
                in_toc = False
            else:
                i += 1
                continue

        # ---- fenced blocks ---------------------------------------------------
        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            body, i = [], i + 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            if lang == "mermaid":
                mermaid_idx += 1
                png = MERMAID / f"mermaid_{mermaid_idx:02d}.png"
                if add_image(doc, png):
                    stats["mermaid"] += 1
                    cap = doc.add_paragraph()
                    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    cap.paragraph_format.space_after = Pt(10)
                    rc = cap.add_run(f"Diagram {mermaid_idx}")
                    rc.font.size, rc.font.italic, rc.font.color.rgb, rc.font.name = (
                        Pt(8.5), True, GREY, BODY_FONT)
                else:
                    add_code(doc, body, "mermaid source (diagram image unavailable)")
            else:
                add_code(doc, body, lang)
                stats["code"] += 1
            continue

        # ---- display maths ---------------------------------------------------
        if stripped.startswith("$$"):
            block = stripped
            if block.count("$$") < 2:
                i += 1
                while i < len(lines) and "$$" not in lines[i]:
                    block += "\n" + lines[i]
                    i += 1
                if i < len(lines):
                    block += "\n" + lines[i]
            add_math(doc, block.replace("$$", " "))
            stats["math"] += 1
            i += 1
            continue

        # ---- images ----------------------------------------------------------
        m = re.match(r"!\[(.*?)\]\((.+?)\)\s*$", stripped)
        if m:
            path = (SRC.parent / m.group(2)).resolve()
            if add_image(doc, path):
                stats["img"] += 1
                pending_caption_image = True
            i += 1
            continue

        # ---- headings --------------------------------------------------------
        # A real heading is 1-6 hashes FOLLOWED BY A SPACE. Soft-wrapped prose can
        # begin with "#7, ..." (a leakage-channel reference) and must not be
        # promoted to a heading.
        mh = re.match(r"^(#{1,6})\s+(\S.*)$", stripped)
        if mh:
            level = len(mh.group(1))
            text = mh.group(2).strip()
            if level == 1 and text.startswith("PART"):
                doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
            if level == 1 and text.startswith("AI Pricing"):
                i += 1
                continue                      # already on the cover
            h = doc.add_heading(level=min(level, 4))
            add_inline(h, text, size={1: 20, 2: 15, 3: 12.5, 4: 11}[min(level, 4)],
                       colour=BLUE, bold=True)
            for run in h.runs:
                run.font.color.rgb = BLUE
                run.font.bold = True
            stats["h"] += 1
            i += 1
            continue

        # ---- horizontal rule -------------------------------------------------
        if stripped in {"---", "***", "___"}:
            i += 1
            continue

        # ---- tables ----------------------------------------------------------
        if stripped.startswith("|") and i + 1 < len(lines) and re.match(
            r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]
        ):
            header = split_row(lines[i])
            spec = split_row(lines[i + 1])
            aligns = [
                "right" if s.strip().endswith(":") and not s.strip().startswith(":")
                else "center" if s.strip().startswith(":") and s.strip().endswith(":")
                else "left"
                for s in spec
            ]
            rows = [header]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i]))
                i += 1
            add_table(doc, rows, aligns)
            stats["tbl"] += 1
            continue

        # ---- block quote -----------------------------------------------------
        if stripped.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            add_quote(doc, buf)
            stats["quote"] += 1
            continue

        # ---- lists -----------------------------------------------------------
        m = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", line)
        if m:
            indent = len(m.group(1)) // 2
            ordered = bool(re.match(r"\d", m.group(2)))
            style = "List Number" if ordered else "List Bullet"
            body = m.group(3)
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(
                r"^(\s*)([-*+]|\d+[.)])\s+|^\s*\||^\s*#{1,6}\s|^\s*```|^\s*>|^\s*\$\$", lines[i]
            ):
                body += " " + lines[i].strip()
                i += 1
            try:
                p = doc.add_paragraph(style=style)
            except KeyError:
                p = doc.add_paragraph()
                body = ("• " if not ordered else "") + body
            p.paragraph_format.left_indent = Inches(0.25 + 0.25 * indent)
            p.paragraph_format.space_after = Pt(3)
            add_inline(p, body)
            stats["p"] += 1
            continue

        # ---- blank -----------------------------------------------------------
        if not stripped:
            i += 1
            continue

        # ---- paragraph -------------------------------------------------------
        buf = [stripped]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
            r"^\s*(#{1,6}\s|\||```|>|\$\$|!\[|---$|[-*+]\s|\d+[.)]\s)", lines[i]
        ):
            buf.append(lines[i].strip())
            i += 1
        text = " ".join(buf)
        p = doc.add_paragraph()
        is_caption = pending_caption_image and text.startswith("*") and text.endswith("*")
        if is_caption:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_after = Pt(12)
            add_inline(p, text.strip("*"), size=8.5, colour=GREY, italic=True)
            for run in p.runs:
                run.font.size, run.font.italic, run.font.color.rgb = Pt(8.5), True, GREY
        else:
            p.paragraph_format.space_after = Pt(7)
            add_inline(p, text)
        pending_caption_image = False
        stats["p"] += 1

    footer_page_numbers(doc)
    DST.parent.mkdir(parents=True, exist_ok=True)
    doc.save(DST)

    print(f"wrote {DST}")
    print(f"  {DST.stat().st_size/1_048_576:.2f} MB")
    print("  " + " · ".join(f"{k}={v}" for k, v in stats.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(convert())
