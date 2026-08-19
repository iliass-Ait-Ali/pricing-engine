# 21 — Final engineering closure (v1.0 freeze)

**Date:** 2026-08-19
**Scope:** engineering only. No modelling method, estimator, feature,
guardrail, decision rule or scientific claim was changed.
**Outcome:** version **1.0.0**, classification unchanged —
**PORTFOLIO READY WITH CLEAR LIMITATIONS**.

This is the evidence log for the closure phase. Every result below is the
output of the command shown, run in this repository on the real Dominick's
Cereals panel.

---

## 1. Protected starting state

Recorded before anything was modified.

| item | state at the start of the phase |
| --- | --- |
| `git status` | **no commits at all**; 224 untracked non-ignored files; `data/` ignored |
| tests | `python -m pytest` → **493 passed**, 0 failed, 15.5 s |
| lint | `python -m ruff check .` → All checks passed |
| claim audit | `python scripts/audit_claims.py --strict` → SAFE 328 / NEEDS_QUALIFICATION 41 / **UNSUPPORTED 0** |
| dataset fingerprint | 4,707,776 rows; parquet SHA-256 `51f9148bd3955129…` |
| selected model | `ridge_loglog-20260818-132847`, validation WAPE 0.4135, test WAPE 0.4565 |
| pooled elasticity | −2.0289, two-way clustered 95% CI [−2.2363, −1.8214]; 239/372 products usable; REML τ² 0.7661; mean weight 0.7797 |
| recommendations (week 399, standard, 3,000 contexts) | 58.87% actionable / 10.37% review / 30.77% keep; HIGH-risk actionable **0** of 1,039; runtime **105.3 s** |
| constraint attribution (13,964 contexts) | **2.86%** of final recommendations set by an interior learned optimum; 57.1% guardrail corners |
| newest generated artifacts | `claim_audit.json` 2026-08-19 06:45; `decision_state_audit.json`, `elasticity_funnel.json` 2026-08-19 00:05 |
| raw data in Git | excluded — `.gitignore` covers `data/raw/`, `data/interim/`, `data/processed/`, `*.zip`; `data/` contains no tracked file |

Raw and processed Dominick's data were never staged, never committed and never
placed in a Docker image at any point in this phase.

---

## 2. Executive-summary margin wording (defect: ambiguous units)

**The defect.** §1.2 of the full report read:

> A 1% improvement in realised gross margin on the Cereals category of this
> panel would have been worth roughly $400,000 of gross profit over the eight
> years observed.

"A 1% improvement in realised gross **margin**" reads as a percentage-point
change in the margin *rate*, but the $400,000 was computed as 1% **of the
gross-profit total**. On this panel the two differ by a factor of **6.5**:

| reading | arithmetic | result |
| --- | --- | --- |
| 1% *relative* increase in the gross-profit total | `0.01 × $40,091,143` | **$400,911** |
| +1 *percentage point* on the gross-margin rate, applied to observed revenue | `0.01 × $262,008,582` | **$2,620,086** |

**The fix.** Both illustrations are now stated explicitly, in their own units,
with the arithmetic shown inline, and both are labelled as illustrations of
category scale — **not** estimated uplift, **not** realised gain, and not
something this system has been shown to deliver. The paragraph now also points
at the causal disclaimers (§13, §54, Appendix G).

Corrected at source in three places, then propagated by regeneration:

* `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md` §1.2 (source of truth)
* `reports/AI_PRICING_REVENUE_OPTIMIZATION_REPORT_SOURCES.md` §13 (derived-claims table, now two rows)
* `docs/BUSINESS_CASE.md`

DOCX and PDF were rebuilt from the Markdown; the corrected wording was verified
present in the exported PDF.

---

## 3. Batch recommendation vectorisation

### 3.1 What the audit had found

A vectorised path (`simulation.counterfactual.simulate_many`) existed but the
batch workflow still called `optimize_price` once per context — roughly 3,000
contexts in ~105 s.

### 3.2 Diagnosis

Profiling 300 real contexts (43.9 ms each) showed the cost was **per-call
overhead, not arithmetic**: 526 separate `model.predict` calls for 300
contexts, each dragging a pandas frame construction and a full scikit-learn
`ColumnTransformer` pass. The candidate grids are small (median **11**
candidates, max 19), so nothing was gained by keeping them separate.

### 3.3 The refactor

`optimize_price` was split into two stages that **both** entry points share:

```
_prepare_context()    eligibility screen → preliminary risk → risk-adjusted
                      policy → constraints → candidate grid
        ↓ (the only difference: how the grid is scored)
_finalise_context()   objective → materiality → final risk gate → decision
                      state → reason codes → Recommendation
```

* `optimize_price` scores one context with `simulate_price_grid` (unchanged).
* `optimize_price_batch` groups contexts by candidate-grid width and scores each
  group with the existing `simulate_many`, then runs the identical
  `_finalise_context`.

Because the pricing logic, guardrails, elasticity selection and risk policy
live in the shared stages, the two paths cannot diverge by construction. Only
`scripts/optimize.py --batch` was switched over; the Phase M audit scripts keep
the per-context path so the frozen scientific artifacts were not perturbed.

### 3.4 Equivalence evidence

**Real data, 3,000 contexts** (`python scripts/benchmark_batch.py --contexts 3000`,
result in `artifacts/metrics/batch_performance.json`):

| compared field | discrete/float | result |
| --- | --- | --- |
| upc, store, decision week, decision date | discrete | identical |
| objective, policy profile, model version, price-response method | discrete | identical |
| **decision state**, **actionable** | discrete | identical |
| **elasticity source**, **risk level**, **reason codes**, risk notes | discrete | identical |
| elasticity used | float | max abs diff **0.0** |
| **proposed candidate price**, **final recommended price** | float | max abs diff **0.0** |
| proposed / final price change % | float | max abs diff **0.0** |
| **predicted units** (current, recommended) | float | max abs diff **0.0** |
| **expected revenue** (current, recommended) | float | max abs diff **0.0** |
| **expected gross profit** (current, recommended) | float | max abs diff **0.0** |
| model-internal estimated profit uplift %, unit cost used | float | max abs diff **0.0** |

0 discrete mismatches; worst floating-point difference **0.0** across all 3,000
recommendations. The output is bit-identical, not merely within tolerance.

**Synthetic contract tests** (`tests/test_batch_equivalence.py`, 13 tests)
compare the two paths field by field — exact for discrete fields, 1e-12 for
floats — across the `conservative` / `standard` / `aggressive` profiles, the
`gross_profit` and `revenue` objectives, the native ML response, contexts with
no eligibility statistics, a non-default input index, and a forced one-row
chunking. Fixtures deliberately cover contexts that fail the history screen,
fail the price-variation screen, have no usable cost, and use pooled versus
per-product elasticity, so the agreement is not vacuous.

**Whole-artifact check.** Re-running the production command
`python scripts/optimize.py --batch 3000 --profile standard` reproduced
`artifacts/metrics/recommendations.json` **byte-for-byte identical except
`runtime_seconds`** (105.3 → 2.3).

### 3.5 Benchmark

| measurement | value |
| --- | --- |
| contexts | 3,000 (decision week 399, `standard` profile, `shrunk` response) |
| per-context loop | **122.01 s** (24.6 contexts/s) |
| vectorised batch | **2.44 s** (1,229.0 contexts/s) |
| speed-up | **50.0×** |
| peak process memory | 4,593.7 MB for the whole benchmark process - dominated by the 4.7 M-row feature table held in memory, not by the batch scoring itself |

> Vectorized batch scoring reduced local runtime from 122.0 s to 2.4 s on 3,000
> contexts.

This is a single-machine, single-process wall-clock measurement on this
dataset. It is **not** evidence of scalability to millions of SKUs, and no such
claim is made anywhere in the repository. The caveat is stored inside the
benchmark artifact itself.

---

## 4. Docker — built, run and verified

Docker Desktop was not running at the start of the phase; it was started, and
the daemon came up. No project code was changed to accommodate the
environment.

```
$ docker build -t pricing-engine:local .
...
#15 naming to docker.io/library/pricing-engine:local done
#15 DONE 34.4s

$ docker images pricing-engine:local
pricing-engine:local 1.66GB

$ docker run -d --name pricing-engine-check -p 8010:8000 \
    -v "$PWD/artifacts:/app/artifacts:ro" \
    -v "$PWD/data/processed:/app/data/processed:ro" \
    pricing-engine:local
bb5558df0f30…

$ curl -s http://127.0.0.1:8010/health
{"status":"ok","model_loaded":true,"contexts_loaded":112763,
 "decision_weeks":[392,393,394,395,396,397,398,399]}

$ curl -s http://127.0.0.1:8010/model/info
{"name":"M1 ridge log-log","kind":"ridge_loglog",
 "version":"ridge_loglog-20260818-132847","price_response_method":"shrunk",
 "pooled_elasticity":-2.028859937952365,"elasticity_training_weeks":[2,257],
 "trained_at_utc":"2026-08-18T09:28:47+00:00","target":"move","n_features":26,…}

$ curl -s -X POST http://127.0.0.1:8010/recommend-price \
    -H "content-type: application/json" \
    -d '{"upc":3000006560,"store":86,"objective":"gross_profit",
         "policy_profile":"standard"}'
{… "current_price":3.35, "final_recommended_price":3.66,
   "decision":"RECOMMEND_CHANGE", "actionable":true,
   "elasticity_used":-2.9400591555147004, "elasticity_source":"shrunk_product",
   "predicted_units_current":25.564729608414478,
   "predicted_units_recommended":19.707714927805956,
   "expected_gross_profit_current":20.271424609064187,
   "expected_gross_profit_recommended":21.73652571021359,
   "model_internal_estimated_profit_uplift_pct":0.0722742051633755,
   "risk_level":"LOW",
   "reason_codes":["PRICE_CHANGE_LIMIT","PROFIT_UPLIFT_POSITIVE"]}

$ docker exec pricing-engine-check id
uid=1000(appuser) gid=1000(appuser) groups=1000(appuser)

$ docker stop pricing-engine-check && docker rm pricing-engine-check
pricing-engine-check
pricing-engine-check
```

| check | result |
| --- | --- |
| clean build | **PASS** (34.4 s; `python:3.11-slim`) |
| `/health` | **PASS** — model loaded, 112,763 serving contexts |
| `/model/info` | **PASS** — same model version and pooled elasticity as the local run |
| real recommendation | **PASS** — **identical** to `python scripts/optimize.py --upc 3000006560 --store 86` down to the last decimal |
| non-root execution | **PASS** — `appuser`, uid 1000, as the Dockerfile intends |
| clean stop | **PASS** |

**Mounts are required, and that is deliberate.** The image contains code only:
`find /app -name '*.parquet' -o -name '*.csv'` returns nothing and `/app/data`
does not exist. The trained model (`./artifacts`) and the processed panel
(`./data/processed`) are mounted **read-only** at run time, because the
Dominick's data are licensed for academic research and must not be
redistributed inside a publicly distributable image.

**Environment note.** The image resolves its own dependencies, landing on
scikit-learn 1.9.0 while `demand_model.joblib` was fitted under 1.8.0. Loading
emits `InconsistentVersionWarning`. Predictions were checked and are identical,
but the unpinned pickle is recorded as limitation 45 rather than silenced.

---

## 5. CI — reproduced locally, not executed on GitHub

`.github/workflows/ci.yml` parses as valid YAML (`yaml.safe_load`), defines one
`test` job on `ubuntu-latest` across Python 3.11 and 3.12, and runs: checkout →
setup-python → install → lint → smoke import → **README metrics check (added in
this phase)** → tests → Docker build (3.11 only).

Every step reproduced locally:

| CI step | local command | result |
| --- | --- | --- |
| Install | `pip install -e ".[api,dashboard,dev]"` (fresh venv) | **PASS** — `pricing-engine-1.0.0` |
| Lint | `python -m ruff check .` | **PASS** — All checks passed |
| Smoke import | `import pricing_engine` → `1.0.0`; `from pricing_engine.optimization.optimizer import optimize_price`; `import api.main` | **PASS** |
| README metrics | `python scripts/update_readme_metrics.py --check` | **PASS** — block matches the artifacts |
| Tests | `python -m pytest -q` | **PASS** — 519 passed |
| Docker build | `docker build -t pricing-engine:local .` | **PASS** — §4 |

```
CI WORKFLOW IMPLEMENTED
CI COMMANDS REPRODUCED LOCALLY
GITHUB-HOSTED RUN NOT YET EXECUTED
```

No Git remote exists in this repository (`git remote -v` is empty). None was
created: publishing to a public remote is a decision for the repository owner,
not for this phase.

---

## 6. Generated README metrics (defect class: documentation drift)

**The mechanism.** `scripts/update_readme_metrics.py` writes everything between

```
<!-- BEGIN GENERATED METRICS -->
<!-- END GENERATED METRICS -->
```

in `README.md`, reading each number from the single artifact that owns it. The
source-of-truth file is printed above every table in the block, so a reader can
go straight to it. Prose outside the markers is never touched.

| metric family | source of truth |
| --- | --- |
| raw → canonical rows, exclusions | `build_audit.json` |
| row count, dataset SHA-256 | `dataset_fingerprint.json` |
| coverage, revenue, gross profit | `eda_summary.json` |
| formula/structural checks | `data_validation.json` |
| model comparison, selected model, WAPEs | `model_metrics.json` + `evaluation.json` |
| native price-response validation | `price_response.json` |
| elasticity ladder | `elasticity.json` |
| pooled elasticity, CI, τ², shrinkage weight, usable products | `elasticity_estimation.json` |
| funnel entry point | `elasticity_funnel.json` |
| out-of-time episodes and WAPEs | `out_of_time_price_response.json` |
| decision-state shares, risk gating, elasticity provenance | `recommendations.json` |
| constraint attribution | `constraint_attribution.json` |
| policy backtest | `backtest.json` |
| test count | `test_suite.json` |
| batch performance and equivalence | `batch_performance.json` |

**Deliberately excluded from the headline block:** the model-internal estimated
profit uplift. It is a self-scored offline simulation, not business impact. It
still appears — labelled — in `reports/07` and in the full report, and a test
asserts that any counterfactual-profit line inside the block carries the
"model-internal" qualifier.

**Enforcement** (`tests/test_readme_metrics.py`, 9 tests): the block must match
a fresh regeneration; `--check` must agree with the test; the **published test
count must equal the number pytest collects in that very run**; no hand-copied
test count may survive outside the block; every metric family must name its
source of truth.

`make readme-metrics` / `make check-readme` were added, and the check is now a
CI step.

---

## 7. Report regeneration

Markdown remains the only source of truth. Nothing was edited in the DOCX or
the PDF.

```
python scripts/render_mermaid.py      # 10 of 10 diagrams re-rendered
python scripts/build_report_docx.py   # Markdown -> DOCX
python scripts/export_report_pdf.py   # DOCX -> PDF, TOC updated, pages read back
```

`scripts/export_report_pdf.py` is new: it drives Word through COM to
repaginate, update the table-of-contents field, export to PDF, and record the
result in `artifacts/metrics/report_build.json`. Before this phase the PDF was
a manual export — itself a reproducibility gap.

The DOCX cover page previously **asserted** its facts (report version, dataset
size, selected model, WAPE, elasticity, headline finding, test count). Those
are now read from the artifacts by `cover_metadata()`, and the "N third-level
headings" note counts the real headings in the Markdown.

| edition | path | size |
| --- | --- | --- |
| Markdown (source of truth) | `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md` | 12,321 lines, 92,317 words, 602,368 chars |
| Word | `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.docx` | 5.61 MB |
| PDF | `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.pdf` | 9.58 MB |

| element | count |
| --- | --- |
| **final page count** | **247** |
| words (Word's own count) | 77,448 |
| Heading 1 / 2 / 3 | 29 / 132 / 690 |
| tables | 278 |
| figures | 23 |
| Mermaid diagrams | 10 (all rendered to PNG) |
| code blocks | 80 |
| display equations | 100 |
| block quotes | 27 |
| PDF bookmarks | 59 |

Verified after export: cover metadata renders with the live values; the TOC
field resolves to real page numbers; footers read "page N of 247"; the
corrected §1.2 wording is present in the PDF text layer; the final page is the
glossary, so nothing was truncated.

Pagination was allowed to change. It happens to have landed on 247 pages
again — the additions (Appendix J.24b, the closure rows) and the deletions
(the de-duplicated README-style tables were README-only) roughly balanced. The
count was **read back from Word**, not assumed.

`reports/AI_PRICING_REVENUE_OPTIMIZATION_REPORT_SOURCES.md` and
`reports/REPORT_QA.md` were updated for the rebuild (page/word/heading counts,
the two-row derived-claims entry for §1.2, the resolved drift rows, live test
counts).

---

## 8. Numeric consistency audit

`scripts/audit_metric_consistency.py` is new. It reads each headline metric
from the artifact that owns it and checks every sentence in the
**hand-authored** documents that states it — anchored on the metric's name, so
it compares like with like. `reports/01`–`reports/20` are excluded because
their own scripts rewrite them from the artifacts on every run; drift registers
and the claim audit are excluded because their subject matter *is* other
documents' wrong numbers.

Lines that record a **correction** ("mean weight 0.954 → 0.780", "was 0.95
before the audit") are recognised and kept. Deleting superseded values would
hide the corrections this project made on purpose.

**Result: 25 metrics, 214 statements verified, 0 mismatches.**

Metrics covered: canonical rows · observed revenue · observed gross profit ·
validation WAPE · test WAPE · pooled elasticity · two-way clustered CI (both
bounds) · usable product elasticities · products without a usable estimate ·
products entering per-UPC estimation · UPCs entering the funnel · empirical-Bayes
τ² · mean shrinkage weight · out-of-time episodes · actionable share · contexts
scored · HIGH-risk actionable count · attribution contexts · share decided by the
learned signal · learned-signal contexts · three decision-agreement pairs · test
count.

### 8.1 Drift found and fixed at source

| document | stated | artifact | resolution |
| --- | --- | --- | --- |
| `README.md` §3 demo block | elasticity −3.016, units 19.58, GP $21.59, uplift +6.51% | −2.940 (raw −3.018, EB weight 0.921), 19.71, $21.74, **+7.23%** | regenerated from a live `python scripts/run_demo.py` |
| `docs/INTERVIEW_RED_TEAM.md` Q2 | 2.1% interior optima, 56.3% corners, 6.6% materiality, 81.2% rule match, 89.1% decision agreement | **2.9% / 57.1% / 5.1% / 82.5% / 90.0%** | corrected |
| `docs/INTERVIEW_GUIDE.md`, `KNOWN_LIMITATIONS.md` #28 | ml vs shrunk 87.3% | **86.7%** | corrected |
| `README.md` §8 | "121 tests" | generated | replaced by a pointer to the generated block |
| `STATUS.md`, `ROADMAP.md`, `docs/TECHNICAL_DESIGN.md`, `reports/VALIDATION_SUMMARY.md`, the full report (19 statements) | 493 / 121 tests | **519** | corrected; phase-scoped historical statements ("149 at Phase K") kept as history |
| full report §44.3, §92.8, Appendix J.24 | drift registers quoting the old values as current | — | rewritten as **resolved** records, with the superseded values kept and marked |

Every fix was made at the source document. None was made by adjusting an
artifact to match prose.

### 8.2 False positives, and what they taught

The first run raised 73 hits. Investigating each one showed that most were the
audit's fault, not the documents':

* tables of **estimator variants** (`reports/14`, `reports/15`) legitimately
  state different τ², weights and confidence intervals per variant;
* **complement statements** ("133 of 372 products have *no* usable own
  elasticity") are the arithmetic complement of 239;
* **per-file test tallies** ("`tests/test_decision_states.py` (246 tests)") are
  not the suite total;
* a **labelled sampling-variation discussion** ("58.9% vs 59.2% — the 3,000 is a
  sample of the 13,964") states both figures on purpose;
* `13,964` contains the substring `3,964`.

The patterns were tightened rather than the documents edited. That distinction
is the point of §8: an audit that "fixes" documents to silence itself is worse
than no audit.

---

## 9. Complete test suite

| command | result |
| --- | --- |
| `python -m pytest` | **519 passed, 0 failed, 0 skipped, 0 xfailed**, 15.9 s |
| `python -m ruff check .` | **All checks passed** |
| `python scripts/audit_claims.py --strict` | SAFE 351 / NEEDS_QUALIFICATION 39 / **UNSUPPORTED 0** |
| `python scripts/audit_metric_consistency.py --strict` | 25 metrics, 214 statements, **0 mismatches** |
| `python scripts/update_readme_metrics.py --check` | README metrics match the artifacts |
| `python scripts/render_mermaid.py` | 10 of 10 diagrams rendered |
| `python scripts/build_report_docx.py` | DOCX rebuilt; 851 headings, 278 tables, 23 images, 10 diagrams, 80 code blocks, 100 equations |
| `python scripts/export_report_pdf.py` | PDF exported, 247 pages, TOC updated |
| `python scripts/smoke_dashboard.py` | all 9 pages render |

Test count: 493 → **519** (+26): 13 batch-equivalence, 9 README-metrics, 4
numeric-consistency. No existing test was weakened or deleted.

**Zero failing tests at the freeze.**

---

## 10. Clean-environment reproduction

A fresh virtual environment was created and populated with the **documented**
install command.

```
python -m venv .venv-clean
.venv-clean/Scripts/python -m pip install -e ".[api,dashboard,dev]"
```

It resolved to newer libraries than the development environment — pandas
**3.0.5** (a major version ahead), numpy **2.5.2**, scikit-learn **1.9.0**,
statsmodels 0.14.6, pytest 9.1.1, ruff 0.16.3 — and still:

| check | result |
| --- | --- |
| `import pricing_engine` | **1.0.0** |
| `python -m ruff check .` | All checks passed |
| `python -m pytest` | **519 passed**, 0 failed, 45 warnings, 33.8 s |

The warnings are the `InconsistentVersionWarning` from loading a model pickled
under scikit-learn 1.8.0 (limitation 45) and a numpy 2.5 deprecation inside
joblib. Neither changed a prediction.

### 10.1 What was re-run and what was reused

| stage | this phase | why |
| --- | --- | --- |
| install | **re-run** (twice: dev env + clean venv) | it is the documented entry point |
| data download | **reused** | the official files are already present with recorded SHA-256; re-downloading 458 MB proves nothing new. The downloader's logic *was* verified: `tests/test_repo_and_downloader.py` (10 tests) pins official-URL-only acquisition, zip-slip and absolute-path rejection, archive member selection, and that licensed data stays git-ignored |
| data validation | **reused** artifact (`data_validation.json`, 15/15) | inputs unchanged, fingerprint unchanged |
| feature build | **reused** | inputs unchanged; the parquet SHA-256 matches the fingerprint |
| training / evaluation | **reused** | freezing v1.0 means not retraining; the model artifact and its metrics are the frozen ones |
| elasticity estimation | **reused** | frozen |
| price-response validation | **reused** | frozen |
| **optimization (batch)** | **re-run** | the vectorisation had to be proven on the real artifact — output identical except runtime |
| **demo** | **re-run** | to regenerate the README example from live output |
| **claim audit** | **re-run** | documents changed |
| **numeric consistency audit** | **re-run** (new) | documents changed |
| **tests, lint** | **re-run** | in both environments |
| **report DOCX + PDF** | **re-run** | the Markdown changed |
| Phase M audits (`reports/11`–`19`) | **reused** | frozen scientific artifacts; nothing they depend on changed |

Dataset integrity re-verified: parquet SHA-256
`51f9148bd3955129a2d7e86953d487a4707296e7ec1b306d1ced5089799761a9`, 4,707,776
rows — unchanged from the recorded fingerprint.

---

## 11. Version freeze

| item | value |
| --- | --- |
| `pyproject.toml` | `version = "1.0.0"` |
| `src/pricing_engine/__init__.py` | `__version__ = "1.0.0"` |
| FastAPI app | now **imports** `__version__` instead of repeating it |
| report edition | `REPORT_VERSION = "1.0"`, printed on the cover from the artifacts |

---

## 12. What did **not** change

Stated explicitly, because an engineering phase is exactly where scientific
findings get quietly softened:

* **No new modelling method.** No RL, no bandits, no causal forest, no neural
  network, no extra forecasting model, no cross-price optimisation, no new
  features.
* **No guardrail was loosened.** The ±10% change cap, the extrapolation
  guardrail, the margin and cost floors, the materiality threshold and the risk
  gate are byte-for-byte the same policy. The batch refactor is pinned to
  bit-identical output precisely so this is checkable.
* **The headline finding stands.** **2.9%** of final recommendations come from
  an interior optimum of the estimated price response; 57.1% sit on a guardrail
  corner; a rule that never looks at demand matches the engine's price within
  one 5-cent grid step 82.5% of the time. This remains a **rule-bounded pricing
  system with a learned price-response direction, not an autonomous ML pricing
  system**.
* **The rule-only benchmark is not hidden.** It is in the README's generated
  block, where `ElasticityBaselinePolicy` (+13.68% model-internal) is reported
  next to `MLPricingPolicy` (+10.45%).
* **Every causal disclaimer is intact**, and the model-internal uplift is still
  labelled as an internal simulation everywhere it appears.
* **No failed approach was deleted** from the project history.
* **No limitation was marked solved** because v1.0 was frozen.

---

## 13. Classification

**PORTFOLIO READY WITH CLEAR LIMITATIONS** — unchanged. The engineering
verification found documentation-drift defects (§8.1) and one wording defect
(§2), all of which were corrected at source; it found no new material
scientific defect, and it removed two classes of defect permanently by making
them test-enforced.
