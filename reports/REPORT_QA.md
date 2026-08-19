# Report QA record

**Subject** [`AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md`](AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md)
**Companion** [`AI_PRICING_REVENUE_OPTIMIZATION_REPORT_SOURCES.md`](AI_PRICING_REVENUE_OPTIMIZATION_REPORT_SOURCES.md)
**QA performed** 2026-08-19 (re-run at the v1.0 engineering freeze)
**Final status** ✅ **PASS — ready to submit**
**Editions** Markdown (source of truth) · **Word `.docx` (247 pages)** · PDF (generated from the DOCX)

---

## 0. Report vital statistics

| metric | value |
| --- | ---: |
| words | **92,317** |
| lines | 12,321 |
| characters | ~602,368 (≈ 588 KB) |
| top-level PARTs | **28** (I–XXVIII) |
| numbered sections | **119 / 119** present (§1–§119) |
| appendices | **12** (A–L) |
| markdown tables | **278** |
| figures with captions | **23** (numbered 1–23, no gaps, no duplicates) |
| Mermaid diagrams | **10** |
| embedded screenshots | **5** (1 API + 4 dashboard); **10 captured** in total |
| distinct repository paths cited | 132 |
| distinct `§N` cross-references | 119 |

---

## 0b. Word edition

The report is delivered in three formats, all generated from the same Markdown
source so they cannot drift apart.

| edition | file | built by |
| --- | --- | --- |
| **Markdown** (source of truth) | `AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md` | authored |
| **Word** | `AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.docx` | `scripts/build_report_docx.py` |
| **PDF** | `AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.pdf` | `scripts/export_report_pdf.py` (Word COM: repaginate → update TOC → export) |

### 0b.1 What the Word edition contains

| element | count | note |
| --- | ---: | --- |
| pages | **247** | A4, 0.8 in margins; counted by Word, recorded in `artifacts/metrics/report_build.json` |
| words (Word's own count) | 77,448 | lower than the Markdown count, which includes table pipes and syntax |
| Heading 1 / 2 / 3 | 29 / 132 / 690 | 28 Parts + Appendices; 119 sections + 12 appendices + "How to read" |
| tables | **279** | 278 content tables + the cover metadata table |
| images | **33** | 23 figures + 10 Mermaid diagrams |
| code blocks | 80 | shaded, monospace, with a coloured left rule |
| display equations | 100 | LaTeX transliterated to Unicode, centred in Cambria Math |
| block quotes | 27 | cream fill with a gold left rule |
| table of contents | live Word field | **pre-built**, 2 levels, clickable, 5 pages |
| footer | every page | title + "page N of M" as live fields |

### 0b.2 Mermaid diagrams

Word cannot render Mermaid, so all **10** diagrams were rendered to PNG by
`scripts/render_mermaid.py` — headless Chrome driving a locally cached
`mermaid.min.js`, captured through CDP with `captureBeyondViewport` at 4×
scale. Two rendering defects were found and fixed during this:

1. Mermaid caps the SVG at its container width, so wide left-to-right
   flowcharts were being captured at 300 px and were unreadable. The SVG is now
   released from `max-width` and pinned to its intrinsic `viewBox` before
   capture.
2. `element.screenshot()` clipped tall diagrams to the viewport. Replaced with
   a CDP clip capture, and every output is checked against the expected height.

### 0b.3 Equation conversion

`scripts/_latex_unicode.py` converts the 100 display equations, with a
self-test of 10 fixtures (`python scripts/_latex_unicode.py` → **10/10**).
Six real defects were found and fixed while building it:

| defect | example | fix |
| --- | --- | --- |
| `rac` collapsed | `(a+bc)/(2b)` came out as `a2b` | proper balanced-argument fraction parser |
| ambiguous denominator | `(a + bc)/2b` reads as `((a+bc)/2)·b` | denominators bracket unless they are a single quantity |
| macro fused with its neighbour | `\cdot\hat{Q}` became `\cdotQ` and the `Q` was then deleted | symbols resolved *before* macro expansion, while the backslash is still present |
| thin space merged names | `\pi\,w` became `\piw`, losing π and w | thin spaces become an invisible macro-name boundary |
| `\left` ate `\leftarrow` | `τ² ← …` came out as `τ² arrow …` | word-boundary guard on the sizing macros |
| identifiers read as subscripts | `effective\_unit\_price` became `effectiveᵤnitₚrice` | literal `\_` protected by a sentinel during script conversion |

Verified: all **100** equations convert with **0** leftover LaTeX artefacts.

### 0b.4 Source-markdown defects found while building the Word edition

Building the DOCX surfaced problems in the Markdown that were then fixed **in
the source**, improving both editions:

| # | defect | count | fix |
| ---: | --- | ---: | --- |
| 1 | inline `$…$` LaTeX rendered as literal backslashes (GitHub does not render it either) | 22 | converted to Unicode |
| 2 | LaTeX macros inside code spans, e.g. `` `\mathcal{F}` `` | 8 | converted to `𝓕`, `Q̂(p₀)` etc. |
| 3 | transposed approximation sign, `16.9 ≈` instead of `≈16.9` | 1 | corrected |
| 4 | sentence continuations beginning `#7, …` promoted to Heading 1 by the converter | 3 | heading rule now requires a space after the hashes |
| 5 | code spans in dark table header rows were unreadable | all | header-row code shading removed |
| 6 | code spans nested inside bold showed literal backticks | all | inline parser made recursive |

### 0b.5 Word-edition verification

| check | result |
| --- | --- |
| opens in Word without repair prompts | ✅ (Word 16.0, COM automation) |
| table of contents populated | ✅ 5 pages, 2 levels, page numbers correct |
| all 33 images present and unclipped | ✅ verified by round-trip and PDF inspection |
| headings map to Word styles | ✅ 29 / 132 / 690, no spurious headings |
| pages spot-checked in the PDF export | cover, TOC (2–3), §1.13 diagram (31), §2 equations (13–14), §22 code+tables (42–43), §54 wide table (98–99), Appendix L glossary (246–247) |
| no text overflow or column collapse in the 7-column tables | ✅ |

---

## 1. Sections checked

### 1.1 Structural completeness

| check | method | result |
| --- | --- | --- |
| all 119 numbered sections present | regex over `^##+\s+(\d+)\. ` | ✅ **0 missing** |
| all 28 PART headings present and in order | regex over `^# PART ` | ✅ I → XXVIII, no gaps |
| all 12 appendices present | regex over `^## Appendix [A-L]` | ✅ A–L complete |
| section numbering matches the prompt's required table of contents | manual comparison | ✅ every required heading exists; several expanded |
| table of contents at the head matches the body | manual comparison | ✅ |

### 1.2 Required-content spot checks

| prompt requirement | where satisfied |
| --- | --- |
| "concise system diagram" in the executive summary | §1.13 (Mermaid) |
| `Q(p,X)`, `R(p)`, `GP(p)` defined with a numerical example | §2.1–§2.3 |
| arc + log-log + fixed effects + robust inference | §25, §26, §28, §29 |
| empirical-Bayes derivation with τ², weights, fallback, audit | §31 (10 subsections) |
| "how much comes from the model versus policy rules?" answered explicitly | §54.9 |
| guardrail ablation across five layers | §55.3 |
| circularity given a full section | §64 |
| strongly-verifiable vs not-verifiable split | §65.1 / §65.2 |
| one detailed subsection per dashboard page, no invented pages | §74.1–§74.9 (exactly 9) |
| every constraint with rationale / form / implementation / example | §53.1–§53.11 |
| 30 interview questions with deep + short + trap + evidence | §108 (Q1–Q30 plus 5 rapid-fire) |
| five-minute / one-minute / thirty-second explanations | §109 / §110 / §111 |
| claims: supported / qualified / forbidden | §112 / §113 / §114 |
| project evolution as scientific iteration | §107 |
| all 12 appendices A–L | Appendices |

---

## 2. Figures checked

| check | result |
| --- | --- |
| every `![...](path)` resolves relative to `reports/` | ✅ **23 references, 22 unique, 0 broken** |
| figure numbering continuous | ✅ 1–23, no gaps (a gap at 3 was found and fixed by renumbering) |
| duplicate figure numbers | ✅ none |
| non-caption "Figure N" references in prose | ✅ none (so renumbering could not break a cross-reference) |
| every figure has a caption stating its source artifact | ✅ 23 / 23 |
| every figure generated from a real artifact or a live run | ✅ see the sources file §11 |
| no mocked, drawn or edited images | ✅ confirmed — all 24 files in `artifacts/report_figures/` were produced on 2026-08-19 |

### 2.1 Figure inventory

| # | file | provenance |
| ---: | --- | --- |
| 1 | `fig_01_temporal_split.png` | `feature_build.json` |
| 2, 23 | `fig_08_decision_context_curves.png` | **live pipeline call** (model + elasticity table + simulator) |
| 3 | `artifacts/figures/eda_price_cv.png` | `run_eda.py` |
| 4 | `fig_02_elasticity_ladder.png` | `elasticity.json` + `elasticity_estimation.json` + `price_response.json` |
| 5 | `fig_03_inference_standard_errors.png` | `elasticity_inference.json` |
| 6 | `fig_05_elasticity_funnel.png` | `elasticity_funnel.json` |
| 7 | `fig_04_shrinkage.png` | `shrinkage_audit.json` |
| 8 | `fig_12_elasticity_stability.png` | `elasticity_stability.json` |
| 9 | `fig_10_model_comparison.png` | `model_metrics.json` |
| 10 | `fig_11_backtest_wape.png` | `backtest.json` |
| 11 | `artifacts/figures/compare_change_distribution.png` | `compare_price_response.py` |
| 12 | `fig_09_out_of_time_price_response.png` | `out_of_time_price_response.json` |
| 13 | `fig_06_constraint_attribution.png` | `constraint_attribution.json` |
| 14 | `fig_07_guardrail_ablation.png` | `guardrail_ablation.json` |
| 15 | `fig_14_model_value_ablation.png` | `model_value_ablation.json` |
| 16 | `fig_13_decision_states.png` | `decision_state_audit.json` |
| 17 | `artifacts/figures/backtest_policy_profit.png` | `backtest.py` |
| 18 | `api_openapi_docs.png` | **live FastAPI + headless Chrome** |
| 19–22 | `dashboard_{1,5,6,8}_*.png` | **live Streamlit + Selenium** |

Captured but not embedded (referenced in §75.5): `dashboard_2`, `dashboard_3`,
`dashboard_4`, `dashboard_7`, `dashboard_9`.

### 2.2 Screenshot verification

Each embedded screenshot was **visually inspected** to confirm it shows real
rendered content rather than an error state or a missing-artifact warning:

| screenshot | verified content |
| --- | --- |
| API `/docs` | 5 endpoints listed, scientific note visible, 9 schemas expanded |
| Executive overview | $262.0M / $40.1M / 489×93 / 366 / WAPE 0.4565 / 19,707 of 36,443 / +8.30% / reason-code bar chart / "0 of 1,039" caption |
| Price simulator | BERRY BERRY KIX store 2, $3.05, cost $2.24, support $1.70–$3.43, ε −3.25, three live curves |
| Recommendation engine | $3.05 → $3.24 (+6.23%), RECOMMEND_CHANGE, LOW risk, ε −3.248, reason codes, feasible range 2.75–3.35, shaded band |
| Data quality | exclusion ledger with all 7 rules, metadata-join JSON, `ok` counts, sale-code counts including `G` 11,075 and `L` 1 |

---

## 3. Metrics consistency checked

### 3.1 Method

A single authoritative metrics dictionary was built **before** writing
(published as **Appendix J**, 24 sub-tables). Every repeated metric in the
report was then scanned for stale or variant forms.

### 3.2 Automated consistency scan

| metric | correct-form occurrences | suspicious variants found |
| --- | ---: | --- |
| test WAPE (0.4565 / 0.45649) | 28 | none |
| validation WAPE (0.4135 / 0.41346) | 11 | none |
| canonical rows (4,707,776) | 20 | 6 × "4.7M" — **benign rounding**, all in prose about the in-memory panel or CV bullets |
| test count (519) | consistent everywhere | the count is now generated: `artifacts/metrics/test_suite.json` is the source of truth and `tests/test_readme_metrics.py` compares it with pytest's live collection |
| pooled elasticity (−2.029 family) | 45 | none |
| mean shrinkage weight (0.780 / 0.7797) | 22 | 4 × "0.78" — **benign rounding** in prose |
| interior-optimum share (2.9%) | 31 | none |
| R4 match (82.5% family) | 19 | none |
| portfolio uplift (+8.30%) | 29 | none |
| usable products (239) | 28 | 7 × "240" — **false positives**: all are `−0.240` (p90 elasticity) or `−2.240` (window W3) |
| full-week contexts (13,964) | 63 | none |

### 3.3 Report-versus-artifact cross-check (programmatic)

Nine headline values were re-read from the artifacts and compared with the
report:

| metric | artifact | report | verdict |
| --- | ---: | ---: | :--: |
| test WAPE | 0.4565 | 0.4565 | ✅ |
| validation WAPE | 0.4135 | 0.4135 | ✅ |
| pooled elasticity | −2.0289 | −2.0289 | ✅ |
| mean shrinkage weight | 0.7797 | 0.7797 | ✅ |
| τ² | 0.7661 | 0.7661 | ✅ |
| usable products | 239 | 239 | ✅ |
| share determined by the learned signal | 2.9% | 2.9% | ✅ |
| portfolio model-internal uplift | 8.30% | 8.30% | ✅ |
| R4 within one grid step | 82.5% | 82.5% | ✅ |

**0 mismatches.**

### 3.4 Stale-metric scan

| pattern | hits | verdict |
| --- | ---: | --- |
| `121 tests` | 1 | ✅ **intentional** — the superseded value in the resolved drift register (Appendix J.24) |
| `149` | 5 | ✅ **false positives** — `$149.94`, `0.149`, `18.149` in data tables |
| `0.954` | 13 | ✅ **intentional** — all inside the Phase L → Phase M correction narrative (§29.7, §31.7, §84.1, §87.2, §107.5, Q14) |
| `87.3%` / `89.0%` | 3 / 3 | ✅ **intentional** — all inside the resolved drift-disclosure passages (§44.3, §92.8, J.24); the live values 86.67% / 91.67% are what every current statement uses |
| `+6.51%` | 2 | ✅ **intentional** — the superseded value, kept in the resolved drift register (Appendix J.24); README now states +7.23% |
| `1.101` | 1 | ✅ **intentional** — drift disclosure only |
| `−3.016` | 0 | ✅ never used as the applied elasticity |

**Conclusion:** no stale metric is presented as current anywhere. Every
appearance of a superseded value is explicitly labelled as superseded.

### 3.5 Deliberate distinctions preserved

Three pairs of near-identical numbers describe genuinely different quantities
and are labelled every time they appear:

| pair | distinction |
| --- | --- |
| 58.9% / 30.8% / 10.4% **vs** 59.2% / 31.1% / 9.7% actionable-keep-review | the **3,000-context sample** vs the **full 13,964-context week** (§106.3 explains) |
| −3.0178 **vs** −2.9401 for UPC 3000006560 | the **raw** per-UPC estimate vs the **shrunk applied** value (§31.8) |
| −1.909 **vs** −2.029 pooled | the **full-panel Phase D** specification vs the **training-weeks-only pricing** estimate (§28.5) |

---

## 4. Unsupported-claim scan

### 4.1 Watched phrases

| pattern | hits | verdict |
| --- | ---: | --- |
| `production[- ]ready` (not preceded by "not") | 3 | ✅ all in the **forbidden-claims table** (§114), the scanner-description (§78.7) and the CV "what to avoid" list (§115.2) |
| `actual uplift` | 2 | ✅ both quoting what `audit_claims.py` blocks |
| `proven` | 8 | ✅ all narrowly scoped — "temporal availability is proven, economic correctness is not" (§17.3, §20.4, §23.4, §65.2, §113, §118.2) and the scanner description |
| `guarantee(s/d)` | 17 | ✅ all technical — "Poisson loss guarantees non-negative predictions", "monotone by construction", "any chronological split guarantees a large PSI", plus quotations of the claim-audit's watched words |
| `causal (effect\|impact\|uplift)` | 8 | ✅ **every one is a negation or a definition** — "not a causal effect", "never realised or causal impact", the glossary entry, the forbidden-claims table |
| `increases profit by` | 0 | ✅ |
| `optimal price` | 1 | ✅ in the **forbidden-claims table**, as the phrase not to use |
| `TODO` / `TBD` / `FIXME` / `Lorem` / `[placeholder` / `<insert` | **0** | ✅ |

### 4.2 Category-discipline audit

Every counterfactual economic figure in the report was checked for the required
qualifier:

| figure | qualifier present? |
| --- | :--: |
| +8.30% portfolio uplift (29 occurrences) | ✅ labelled "model-internal estimate" or appearing in a table headed as such |
| +10.45% backtest ML policy | ✅ table header says "**All 'vs historical' figures are model-internal estimates (§64)**" |
| +13.68% elasticity-baseline policy | ✅ same table |
| +7.23% worked-example uplift | ✅ §2.3, §69, §72, §105 all state model-internal |
| +2.30% / +1.48% simple-margin policy | ✅ same table |

**No counterfactual figure appears anywhere in the report without its
qualifier.**

### 4.2b The repository's own claim scanner, run on this report

`scripts/audit_claims.py --strict` was executed **with this report included in
the scan**.

| verdict | before this report (43 files) | with this report (49 files) |
| --- | ---: | ---: |
| **UNSUPPORTED** | 0 | **0** ✅ |
| NEEDS QUALIFICATION | 11 | 38 |
| SAFE | 151 | 325 |
| total occurrences | 162 | 363 |

**First pass flagged 1 UNSUPPORTED**: the phrase *"realised margin"* (§20.3),
used to mean the accounting margin a week actually produced, matched the watched
pattern `(increased|improved|achieved|delivered|realised|realized)
(profit|revenue|margin)` for a claimed business outcome. The sentence was
reworded to *"how the week's accounting margin turned out"*. **Second pass:
0 UNSUPPORTED, exit code 0.** The scanner behaved correctly on a genuinely
ambiguous phrase, and the report's numbers in §78.7, §84.1, §112–§114, §118.1
and Appendix J.22 were updated to the post-inclusion counts.

Of the 38 needs-qualification hits, 27 are in this report — all uses of
*validated*, *guarantees* or *profit uplift* carrying the qualifier §113
requires.

### 4.3 Structural safeguards verified in the text

| safeguard | present |
| --- | :--: |
| §64 is a standalone chapter on circularity | ✅ |
| §65 separates strongly-verifiable from not-verifiable | ✅ |
| §113 lists every claim requiring qualification, with the exact qualifier | ✅ |
| §114 lists forbidden claims with a correct replacement for each | ✅ |
| §33 states `P(Q\|price) ≠ P(Q\|do(price))` formally | ✅ |
| §86 names the weakest component without hedging | ✅ |
| the "How to read this report" preamble fixes the four number categories | ✅ |

---

## 5. Broken-reference scan

| check | method | result |
| --- | --- | --- |
| image paths | filesystem existence relative to `reports/` | ✅ **0 broken** (22 unique) |
| repository paths in backticks | filesystem existence | ✅ **0 genuinely missing** — see note below |
| bare artifact filenames | searched across 22 plausible roots | ✅ 71 of 73 resolve; 2 are `upccer.csv` / `wcer.csv`, which live in the **git-ignored** `data/raw/` tree (present locally, correctly not committed) |
| `§N` cross-references | every referenced section number exists | ✅ **119 distinct refs, 0 unresolved** |
| `§N.M` sub-references | parent section exists | ✅ **0 unresolved** |
| companion-file links | `AI_PRICING_REVENUE_OPTIMIZATION_REPORT_SOURCES.md`, `REPORT_QA.md` | ✅ both created |

**Note on `reports/NN` shorthand:** the report uses the repository's own
shorthand (`reports/11`, `reports/16`, …) for the numbered audit reports. The
automated scan flags 12 of these as "missing" because the literal path lacks
the `_NAME.md` suffix. All twelve resolve to real files
(`reports/11_CONSTRAINT_ATTRIBUTION.md` etc.), and the full filename is given
at least once for each. **Not a defect.**

---

## 6. Equation rendering checked

| check | result |
| --- | --- |
| display equations use `$$ … $$` | ✅ consistently |
| inline maths uses backticks or `$ … $` consistently within a passage | ✅ |
| every equation in the body also appears in **Appendix B** | ✅ 8 groups: data derivations, economics, elasticity, shrinkage, price response, optimization, metrics, experiment design |
| Greek and mathematical symbols render as Unicode (ε, τ, β, ρ, σ, μ, θ, Σ, ∂, ≈, ≥, ≤, ⟹) | ✅ |
| pipe characters inside table cells escaped (`\|`) | ✅ checked in `P(Q\|price)` and `mean(\|y−ŷ\|)` cells |
| Mermaid blocks are syntactically closed and free of characters Mermaid rejects (parentheses in node labels replaced, arrows quoted) | ✅ 10 / 10 |

---

## 7. Table numbering and captions

The report uses **278 markdown tables**. A decision was taken **not** to number
them, because:

* every table sits immediately under the heading that names it;
* the prompt asks for numbered *figures*, not numbered tables;
* 278 numbered captions would add ~278 lines of overhead with no navigational
  benefit in a document with a 119-section table of contents.

**Figures are numbered and captioned** (23/23), and every figure caption names
its source artifact. ✅ **Accepted as a deliberate choice, not an omission.**

---

## 8. Command verification

Every command quoted in Appendix I was checked against the Makefile and the
scripts directory.

| check | result |
| --- | --- |
| every `make` target quoted exists in the Makefile | ✅ (`data`, `validate`, `features`, `eda`, `elasticity`, `estimate-elasticity`, `train`, `evaluate`, `price-response`, `compare-response`, `optimize`, `backtest`, `monitor`, `audit-zero-price`, `audit-cost`, `audit`, `demo`, `test`, `lint`, `api`, `dashboard`, `smoke`, `all`) |
| every `python scripts/*.py` quoted exists | ✅ 30 / 30 |
| every CLI flag quoted exists | ✅ `--batch`, `--profile`, `--objective`, `--method`, `--upc`, `--store`, `--week`, `--n-contexts`, `--weeks`, `--contexts-per-week`, `--n-series`, `--strict`, `--force` |
| the `curl` example matches the live API contract | ✅ executed 2026-08-19 |
| the uvicorn / streamlit invocations match the Makefile | ✅ |

---

## 9. Live verification performed for this report

| # | action | outcome |
| ---: | --- | --- |
| 1 | `python -m pytest` | **519 passed, 0 failed, 0 skipped**, 15.9 s |
| 2 | `python -m pytest --collect-only -q` | per-file counts sum to 519 ✅ |
| 3 | `ruff check .` | **All checks passed!** (including the newly added `scripts/make_report_figures.py`) |
| 4 | `python scripts/run_demo.py` | real recommendation reproduced; **found a documentation drift** vs the README (§12 of the sources file) |
| 5 | FastAPI live: `/health`, `/model/info`, `/recommend-price` | all responded; the §72 payload is verbatim |
| 6 | Streamlit live: all 9 pages captured | all rendered with real content |
| 7 | `python scripts/make_report_figures.py` | 14 figures generated from artifacts and a live pipeline call |
| 8 | `git log` | **no commits** — recorded as limitation L15 |

---

## 10. Findings raised by this QA pass

| # | finding | action taken |
| ---: | --- | --- |
| 1 | Figure numbering had a gap at 3 (a figure was moved out of §10 during drafting) | ✅ renumbered 4–24 → 3–23; verified 0 non-caption references existed, so no cross-reference broke |
| 2 | `scripts/make_report_figures.py` failed `ruff` on 12 rules (`UP031`, `B007`, 10 × `B905`) | ✅ fixed; the repository-wide lint is clean again |
| 3 | Six documentation-drift instances between the README/limitations prose and the current artifacts | ✅ all six documented in Appendix J.24 and sources §12; the report uses the artifact values throughout |
| 4 | The repository has **no git commits**, so model artifacts have no code lineage | ✅ recorded as limitation **L15** (§81.4, §92.3, Appendix K) and listed as the first thing to fix (§118.6) |
| 5 | The dashboard's Reports tab omits the ten Phase M audits | ✅ recorded as limitation **L14** (§74.9, Appendix K) |
| 6 | The CI matrix targets Python 3.11/3.12 while the build ran on 3.13.0 | ✅ recorded as limitation **L17** (§83.4, Appendix K) |
| 7 | The ±10% cap is per-decision with no cumulative tracking (four weeks compounds to +46%) | ✅ recorded as limitation **O9** (§94.5, Appendix K) |
| 8 | No absolute price ceiling is configured (`MAX_PRICE` present in 0 contexts) | ✅ recorded as limitation **O7** (§53.2, §91.7, §94.5) |

**Findings 4–8 are new observations produced by writing this report**, not
copies of the repository's existing limitation list. They have been added to
the consolidated register in Appendix K.

---

## 11. Conclusions checked against evidence

| conclusion | supporting evidence | verdict |
| --- | --- | :--: |
| "rule-bounded pricing with a learned direction, not ML pricing" | 2.9% interior optima; 57.1% guardrail corners; R4 matches 82.5% | ✅ supported |
| "the model contributes direction, eligibility, refusal and economics" | 8.0% of actionable recommendations are cuts; 40.1% of contexts are refusals; p90 elasticity sensitivity 14.5% | ✅ supported |
| "the weakest component is counterfactual policy evaluation" | §64's argument plus the absence of any offline estimator | ✅ supported, and argued against four alternatives |
| "the strongest component is the audit suite" | §87's eight-step inference→shrinkage→policy chain | ✅ supported, with the runner-ups named |
| "PORTFOLIO READY WITH CLEAR LIMITATIONS" | eight readiness criteria met; ten limitations ranked; matches `STATUS.md` | ✅ supported |
| "no causal claim anywhere" | scanned: every `causal effect/impact/uplift` occurrence is a negation or a definition | ✅ verified programmatically |
| "no counterfactual figure without its qualifier" | §4.2 above | ✅ verified |

---

## 12. Final status

| dimension | status |
| --- | :--: |
| all required headings present | ✅ |
| all current metrics verified against artifacts | ✅ 9/9 exact |
| placeholders / TODO / TBD | ✅ none |
| stale metrics presented as current | ✅ none |
| unsupported "causal" wording | ✅ none |
| "actual uplift" / "production ready" as claims | ✅ none |
| image and file references | ✅ 0 broken |
| section cross-references | ✅ 0 unresolved |
| equations render and are collected in Appendix B | ✅ |
| repository paths exist | ✅ |
| commands correct | ✅ |
| conclusions match evidence | ✅ |
| screenshots real, not fabricated | ✅ 10 captured from live applications |
| companion sources file complete | ✅ 15 sections, every claim mapped |

### ✅ **PASS — the report is ready to submit and present.**

**Known caveats a reader should be told up front** (all disclosed inside the
report itself):

1. Nothing in the project is causal; all elasticities are observational.
2. Every uplift figure is a **model-internal estimate** and circular by
   construction.
3. **2.9%** of recommendations are set by an interior model optimum — the
   system is rule-bounded.
4. Docker was never built; CI never ran; the repository has no commits.
5. Six documentation-drift instances exist between the repository's prose and
   its current artifacts; the report uses the artifacts and lists all six.
