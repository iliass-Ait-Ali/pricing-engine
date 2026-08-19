# Validation summary

**Version 1.0.0 — final.**

Full clean verification of the pipeline: executed on 2026-08-18 for **Phase L**
(pricing-science hardening) and **Phase M** (final scientific audit), and re-run
on 2026-08-19 for **Phase N** (engineering closure and v1.0 freeze).

Environment: Windows 11, Python 3.13.0, pandas 2.2.3, numpy 2.0.2,
scikit-learn 1.8.0, statsmodels 0.14.6.
Also verified in a **fresh virtual environment** created from the documented
install command (`pip install -e ".[api,dashboard,dev]"`), which resolved to
pandas 3.0.5, numpy 2.5.2, scikit-learn 1.9.0, pytest 9.1.1: **519 passed**,
ruff clean. Loading the model artifact there raises
`InconsistentVersionWarning` (fitted under scikit-learn 1.8.0) — recorded as
limitation 45, not silenced.

Every line below is the outcome of the command shown - no result is asserted
without having been run.

## Result table

| # | Step | Command | Result |
| --- | --- | --- | --- |
| 1 | Install package | `pip install -e ".[api,dashboard,dev]"` | **PASS** - `pricing_engine 1.0.0` imports, in this environment and in a fresh venv |
| 2 | Data acquisition | `python scripts/download_dominicks.py` | **PASS** - official Kilts URLs; `upccer.csv` 25,932 B, `wcer.zip` 42,402,094 B, extracted `wcer.csv` 458,400,475 B, manual PDF 10,182,109 B |
| 3 | Build dataset | `python scripts/build_dataset.py` | **PASS** - 6,602,582 raw -> 4,707,776 canonical rows |
| 4 | Data validation | `python scripts/validate_data.py` | **PASS** - 15 / 15 checks on 4,707,776 rows |
| 5 | Feature build | `python scripts/build_features.py` | **PASS** - 4,671,333 usable rows, 36,443 dropped for no history, 73 s |
| 6 | Pricing EDA | `python scripts/run_eda.py` | **PASS** - `reports/02`, 6 figures |
| 7 | Elasticity analysis | `python scripts/run_elasticity.py` | **PASS** - `reports/03`: naive -0.348 -> UPC x store FE -2.419 -> +controls -1.909 |
| 8 | Unit + integration tests | `python -m pytest` | **PASS** - **519 passed** in 15.9 s, 0 failed, 0 skipped, 0 xfailed |
| 9 | Model training | `python scripts/train.py` | **PASS** - 6 models compared, 243 s |
| 10 | Model selection | validation WAPE, price-aware models only | **M1 ridge log-log** (0.4135) over HGB Poisson (0.4226) |
| 11 | Test metrics | scored once after selection | MAE **8.503**, RMSE **72.064**, WAPE **0.4565**, bias -3.883 (749,040 rows) |
| 12 | Save + reload model | `python scripts/evaluate.py` | **PASS** - reload WAPE difference **0.00e+00** |
| 13 | Native ML price response | `python scripts/price_response.py --n-contexts 300` | **PASS** - 100% monotone decreasing, 0% flat, 0% negative, median implied elasticity **-3.10** (steeper than the econometric benchmark: the finding that triggered Phase L) |
| 14 | **Elasticity estimation (Phase L/M)** | `python scripts/estimate_elasticity.py` | **PASS** - training weeks 2-257 only; pooled **-2.029**, two-way clustered (UPC, week) 95% CI **[-2.24, -1.82]**; UPC x store FE **-2.222**; **239 / 372** products usable; REML tau^2 **0.766**; mean shrinkage weight **0.780** (panel-robust standard errors) |
| 15 | **Price-response comparison (Phase L)** | `python scripts/compare_price_response.py --n-contexts 300` | **PASS** - `reports/08`: ml vs pooled agree on the decision state 79.7% of the time (mean gap $0.087 = 2.74% of price); ml vs shrunk **86.7%**; pooled vs shrunk **91.7%** |
| 16 | **Elasticity sensitivity (Phase L)** | same run | **PASS** - median candidate-price spread across the -1.5/-1.9/-2.4/-3.1 scenarios: **21.51%** under wide what-if guardrails, **0.00%** under production guardrails (constraints bind first) |
| 17 | Optimizer on a real context | `python scripts/run_demo.py` | **PASS** - CAPN CRUNCH JUMBO CR, store 86: $3.35 -> $3.66 (+9.25%), applied elasticity **-2.940** (`shrunk_product`; raw -3.018, EB weight 0.921), RECOMMEND_CHANGE, model-internal estimated uplift **+7.23%** |
| 18 | Batch recommendations | `python scripts/optimize.py --batch 3000 --profile standard` | **PASS** - 58.9% actionable, 10.4% REVIEW_REQUIRED, 30.8% KEEP_CURRENT; model-internal estimated portfolio uplift **+8.30%**; **HIGH-risk actionable: 0 of 1,039**; runtime 105.3 s → **2.3 s** after vectorisation, every summary field unchanged |
| 19 | Backtest | `python scripts/backtest.py --weeks 10 --contexts-per-week 400` | **PASS** - weeks 390-399, mean weekly WAPE 0.4777; ML policy +10.45% model-internal estimated vs historical, 36.9% prices unchanged |
| 20 | Monitoring | `python scripts/monitor.py` | **PASS** - schema OK, drift + prediction + quarterly accuracy reported |
| 21 | **Zero-price audit (Phase L)** | `python scripts/audit_zero_price.py` | **PASS** - `reports/09`: 1,851,380 zero-price rows (28.04% of raw), of which only **677** recorded any sales; 73.4% are leading/trailing runs; rule kept, evidence documented |
| 22 | **Cost leakage audit (Phase L)** | `python scripts/audit_cost_leakage.py --n-series 25` | **PASS** - 4,671,333 rows compared, **0 mismatches**; future poisoning changed nothing at or before the poisoned week and did change the next week; 19 recommendations compared, **0 changed** |
| 23 | API tests | `python -m pytest tests/test_api.py` | **PASS** - 15 / 15 |
| 24 | Dashboard smoke test | `python scripts/smoke_dashboard.py` | **PASS** - all 9 pages render without exceptions |
| 25 | Lint | `python -m ruff check .` | **PASS** - All checks passed |
| 26 | **Constraint attribution (Phase M)** | `python scripts/audit_constraints.py` | **PASS** - `reports/11`: all 13,964 week-399 contexts x 11 pipeline runs; **2.9%** of final recommendations set by an interior model optimum; the change cap binds first in 44.6% |
| 27 | **Rule-only ablation (Phase M)** | `python scripts/audit_model_value.py` | **PASS** - `reports/12`: a no-demand-model rule matches the engine's final price within one grid step **82.5%** of the time and the decision state 90.0% |
| 28 | **Guardrail ablation (Phase M)** | `python scripts/audit_guardrails.py` | **PASS** - `reports/13`: median price variation across elasticity scenarios **79.8%** (research) -> 6.6% (support only) -> **0.0%** (change cap added) |
| 29 | **Shrinkage audit (Phase M)** | `python scripts/audit_shrinkage.py` | **PASS** - `reports/14`: estimator re-derived; the defect was in the *inputs*, not the formula; mean weight 0.954 -> **0.780** |
| 30 | **Robust inference (Phase M)** | `python scripts/audit_elasticity_inference.py` | **PASS** - `reports/15`: two-way clustered SE **16.2x** the conventional one; per-UPC inflation median **3.4x**; estimators validated against statsmodels to machine precision |
| 31 | **Out-of-time price response (Phase M)** | `python scripts/audit_out_of_time_response.py` | **PASS** - `reports/16`: **154,899** unseen price-change episodes in weeks 258-399; `shrunk` has the lowest WAPE in every split (0.560 non-promotion vs 0.588 native ML, 0.594 pooled, 0.715 null) |
| 32 | **Elasticity stability (Phase M)** | `python scripts/audit_elasticity_stability.py` | **PASS** - `reports/17`: category stable (max pairwise z **1.36**), product ordering not (rank corr **+0.19**) |
| 33 | **Eligibility funnel (Phase M)** | `python scripts/audit_eligibility_funnel.py` | **PASS** - `reports/18`: 489 UPCs -> 372 in training -> 272 pass the screens -> 248 correctly signed -> **239** usable, covering 64.2% of decision contexts |
| 34 | **Decision-state audit (Phase M)** | `python scripts/audit_decision_states.py` | **PASS** - `reports/19`: **0** invariant violations across three profiles and 13,964 contexts; 245 property tests |
| 35 | **Claim audit (Phase M)** | `python scripts/audit_claims.py --strict` | **PASS** - `reports/20`: **0 UNSUPPORTED** claims across the repository |
| 36 | **Docker build (Phase N)** | `docker build -t pricing-engine:local .` | **PASS** - image built, 1.66 GB, Python 3.11-slim base; no `.parquet` or `.csv` anywhere in the image |
| 37 | **Docker run (Phase N)** | `docker run -d -p 8010:8000 -v "$PWD/artifacts:/app/artifacts:ro" -v "$PWD/data/processed:/app/data/processed:ro" pricing-engine:local` | **PASS** - `/health` → `{"status":"ok","model_loaded":true,"contexts_loaded":112763}`; `/model/info` → `ridge_loglog-20260818-132847`, `shrunk`, pooled -2.0289; `POST /recommend-price` (UPC 3000006560, store 86) → $3.35 → $3.66, RECOMMEND_CHANGE, +7.2274% — **identical to the local run**; `id` → `uid=1000(appuser)`; stopped and removed cleanly |
| 38 | **Batch vectorisation (Phase N)** | `python scripts/benchmark_batch.py --contexts 3000` | **PASS** - per-context loop **122.01 s** → vectorised **2.44 s** (**50.0x**); equivalence over all 3,000 recommendations: 0 discrete-field mismatches, worst float difference **0.0** |
| 39 | **README metric generation (Phase N)** | `python scripts/update_readme_metrics.py --check` | **PASS** - the generated block matches the artifacts; the published test count matches pytest's live collection |
| 40 | **Numeric consistency audit (Phase N)** | `python scripts/audit_metric_consistency.py --strict` | **PASS** - 25 metrics, **213 statements** checked across the hand-authored documents, **0 mismatches** |
| 41 | **Report regeneration (Phase N)** | `python scripts/render_mermaid.py && python scripts/build_report_docx.py && python scripts/export_report_pdf.py` | **PASS** - Markdown → DOCX (5.61 MB; 851 headings, 278 tables, 23 figures, 10 Mermaid diagrams, 80 code blocks, 100 equations) → PDF (9.58 MB, **247 pages**, TOC field updated, 59 bookmarks) |
| 42 | **CI reproduced locally (Phase N)** | every step of `.github/workflows/ci.yml` | **PASS locally** - install, `ruff check .`, three smoke imports, README-metrics check, `pytest -q`, `docker build`. **GITHUB-HOSTED RUN NOT YET EXECUTED** (no remote configured) |

## Phase L acceptance gate

| requirement | status | evidence |
| --- | --- | --- |
| Hybrid elasticity-based price response works | **PASS** | `Q(p) = Q_hat(p0) * (p/p0) ** epsilon`; at `p = p0` it reproduces the ML prediction exactly (`test_hybrid_reproduces_the_baseline_at_the_reference_price`) |
| Elasticity estimated on training-only information | **PASS** | weeks 2-257; window recorded in the table metadata; the script aborts if rows exceed it |
| Product-level reliability handled | **PASS** | per-UPC estimate + panel-robust se + CI + t + n_obs + n_distinct_prices; wrong-signed (24), imprecise (9) and thin (100) products rejected; REML empirical-Bayes shrinkage on the rest |
| Recommendation sensitivity reported | **PASS** | `reports/08` sections 4-5: pairwise disagreement + four elasticity scenarios under two guardrail regimes |
| HIGH risk gated | **PASS** | 0 of 1,039 HIGH-risk contexts actionable; 311 routed to REVIEW_REQUIRED; conservative profile keeps current; only the DEMO aggressive profile can override |
| Zero-price exclusion investigated | **PASS** | `reports/09` - counts by ok/week/UPC/store, position within series, selection probe, manual searched |
| Cost availability leakage-safe | **PASS** | `reports/10` - three independent checks, all pass; `tests/test_cost_leakage.py` (5 tests) |
| Native ML vs elasticity pricing compared | **PASS** | `reports/08`; the ML method is retained as `--method ml` |
| Tests pass | **PASS** | 519 passed, 0 failed, 0 skipped |
| Reports regenerated from execution | **PASS** | reports 01-20 + MONITORING_DESIGN + this file, all produced by the commands above |

## Phase M acceptance gate

| requirement | status | evidence |
| --- | --- | --- |
| Constraint binding attributed per recommendation | **PASS** | `reports/11` - present / removes candidates / binding at optimum / changes the decision, leave-one-out over 10 constraints x 13,964 contexts |
| Model contribution measured against rule-only policies | **PASS** | `reports/12` - five rules, decisions compared (not profits, which would be circular) |
| Guardrail ablation across five policy layers | **PASS** | `reports/13` - the pricing signal stops determining the recommendation at layer C |
| Empirical-Bayes shrinkage audited and corrected | **PASS** | `reports/14` - all eight audit questions answered; inputs fixed, formula confirmed; 12 deterministic tests |
| Robust inference for elasticity | **PASS** | `reports/15` - classical / HC1 / four clusterings / two-way; estimators validated against statsmodels |
| Out-of-time price-response check | **PASS** | `reports/16` - explicitly labelled naturalistic prediction, not causal identification |
| Elasticity stability across windows | **PASS** | `reports/17` - five windows, three disjoint, formal z tests, leakage guard on the test window |
| Eligibility funnel explained | **PASS** | `reports/18` - every transition has a counted reason |
| Overbroad "forecast skill" claim corrected | **PASS** | removed repository-wide; replaced with the precise statement; 15 parametrised regression tests pin `Q_hybrid(p0) == Q_ML(p0)` |
| Decision-state invariants hold | **PASS** | `reports/19` - 0 violations; 245 property tests; proposal and final price are now separate fields |
| Portfolio claims audited | **PASS** | `reports/20` - 0 UNSUPPORTED; `--strict` makes it enforceable |
| Default price-response method re-decided on evidence | **PASS** | `shrunk` retained on out-of-time WAPE (`reports/16`), not on inheritance; DECISIONS.md #50 |

## Analytical optimizer verification

Deterministic fixtures with closed-form answers (`tests/test_optimizer.py`):

| fixture | ground truth | result |
| --- | --- | --- |
| linear demand profit optimum, `Q = 100 - 10p`, `c = 2.50` | `p* = (a + bc)/(2b) = 6.25` | recovered within one grid step (0.05) |
| linear demand revenue optimum | `p* = a/(2b) = 5.00` | recovered within one grid step |
| constant elasticity, inelastic (`e = -0.5`) | optimum at the upper guardrail | recommended price equals the feasible upper bound, `PRICE_CHANGE_LIMIT` raised |
| starting at the optimum | no change | `KEEP_CURRENT`, `KEEP_CURRENT_OPTIMAL` |
| gain below materiality (0.45% vs 1% threshold) | no change | `KEEP_CURRENT`, `NON_MATERIAL_UPLIFT` |
| hybrid with fixed `epsilon` | `Q(p) = Q0 (p/p0)^e` exactly | reproduced to floating-point tolerance |
| empirical-Bayes shrinkage | `w = tau^2/(tau^2 + se^2)` | weights and blend match the closed form |

## Phase N acceptance gate (engineering closure and v1.0 freeze)

| requirement | status | evidence |
| --- | --- | --- |
| Executive-summary margin wording made unambiguous | **PASS** | §1.2 of the full report now states the relative-gross-profit illustration ($0.4 M) and the percentage-point-of-margin illustration ($2.6 M) separately, both labelled arithmetic, neither presented as uplift; `docs/BUSINESS_CASE.md` and the sources file match |
| Batch path vectorised without changing a single decision | **PASS** | rows 18 and 38 above; `tests/test_batch_equivalence.py` (13 tests) compares proposed price, final price, decision state, actionable, elasticity source, risk level, reason codes, risk notes, units, revenue, gross profit and diagnostics across three profiles and both objectives |
| Docker built, run and verified | **PASS** | rows 36-37 |
| CI reproduced locally; hosted status stated honestly | **PASS** | row 42 |
| README headline metrics generated, not hand-copied | **PASS** | rows 39-40; `tests/test_readme_metrics.py`, `tests/test_metric_consistency.py` |
| Report regenerated from the Markdown source of truth | **PASS** | row 41; DOCX/PDF never hand-edited; page count read back from Word into `artifacts/metrics/report_build.json` |
| Zero failing tests at the freeze | **PASS** | 519 passed, 0 failed, 0 skipped, 0 xfailed |
| Scientific findings preserved | **PASS** | 2.9% interior-optimum attribution, the rule-only benchmark, the circularity of the backtest and every causal disclaimer are unchanged and re-verified |

## Test suite composition (519 tests)

| file | tests | covers |
| --- | --- | --- |
| `test_data_pipeline.py` | 17 | derived formulas, zero-qty safety, promotion coding, week decoding, exclusion rules, metadata join, grain uniqueness |
| `test_repo_and_downloader.py` | 10 | licensed data git-ignored, official URLs only, zip-slip and absolute-path rejection, archive member selection |
| `test_features.py` | 13 | lag alignment, shifted rolling windows, leakage poisoning, decision-time cost, price-feature recomputation, temporal split |
| `test_economics.py` | 16 | revenue/margin identities, price variation, arc elasticity edge cases, log-log recovery, fixed-effects confounding |
| `test_simulation.py` | 16 | price grid, only price features move, cost held fixed, single batched call, curve diagnostics |
| `test_optimizer.py` | 23 | analytical optima, every constraint, keep-current paths, objective errors, risk levels |
| `test_phase_l_pricing.py` | **40** | hybrid mechanics, shrinkage maths, per-UPC estimator, risk gating across all three profiles, uplift naming, elasticity provenance, `Q_hybrid(p0) == Q_ML(p0)` across 5 elasticities x 3 price levels |
| `test_cost_leakage.py` | **5** | decision cost identity, future poisoning (and that the test has teeth), recommendation invariance, missing-cost refusal |
| `test_api.py` | 15 | health, model info, model loaded once, prediction contract, validation rejections, decision states, policy profiles |
| `test_monitoring.py` | 9 | PSI, KS, schema checks, drift ranking, performance windows |
| `test_integration.py` | 2 | raw -> processed -> features -> model -> simulation -> optimization -> audit log |
| `test_attribution.py` (Phase M) | **69** | the audit replica reproduces `optimize_price` exactly across 60 parameter combinations; the baseline demand level cannot change the recommended price; closed-form unconstrained optimum; constraint intervals |
| `test_shrinkage.py` (Phase M) | **12** | the four limiting cases (imprecise -> w=0, precise -> w=1, zero heterogeneity -> strong pooling, large heterogeneity -> weak pooling), monotonicity, bounds, prior-mean handling, and that understated standard errors silently disable the shrinkage |
| `test_decision_states.py` (Phase M) | **246** | risk x decision x actionable x proposal x final price invariants over every combination and all three profiles; audit-log schema rotation |
| `test_batch_equivalence.py` (Phase N) | **13** | the vectorised batch path equals the per-context path field by field - exact for discrete fields, 1e-12 for floats - across three policy profiles, both objectives, the native ML response, no-statistics contexts, a shifted index and a forced one-row chunking |
| `test_readme_metrics.py` (Phase N) | **9** | the README generated block matches the artifacts; the published test count matches pytest's live collection; no hand-copied test count survives outside the block; every metric family names its source of truth; model-internal uplift is never a headline metric |
| `test_metric_consistency.py` (Phase N) | **4** | no hand-authored document contradicts the artifact that owns a metric; the audit is not vacuous; the 2.9% attribution finding is still stated |

## Generated artifacts

Reports: `01_DATA_AUDIT`, `02_PRICING_EDA`, `03_ELASTICITY_ANALYSIS`,
`04_MODEL_COMPARISON`, `05_PRICE_RESPONSE_VALIDATION`, `06_BACKTEST`,
`07_RECOMMENDATION_SUMMARY`, `08_PRICE_RESPONSE_COMPARISON`,
`09_ZERO_PRICE_AUDIT`, `10_COST_LEAKAGE_AUDIT`,
`11_CONSTRAINT_ATTRIBUTION`, `12_MODEL_VALUE_ABLATION`, `13_GUARDRAIL_ABLATION`,
`14_SHRINKAGE_AUDIT`, `15_ELASTICITY_INFERENCE_AUDIT`,
`16_OUT_OF_TIME_PRICE_RESPONSE`, `17_ELASTICITY_STABILITY`,
`18_ELASTICITY_ELIGIBILITY_FUNNEL`, `19_DECISION_STATE_AUDIT`,
`20_CLAIM_AUDIT`, `MONITORING_DESIGN`,
`VALIDATION_SUMMARY`.

Metrics: `raw_audit`, `build_audit`, `dataset_fingerprint`, `data_validation`,
`eda_summary`, `price_variation_upc_store`, `elasticity`, `elasticity_by_upc`,
`elasticity_estimation`, `feature_build`, `model_metrics`, `evaluation`,
`price_response`, `price_response_comparison`, `recommendations`, `backtest`,
`monitoring`, `zero_price_audit`, `cost_leakage_audit`.

Models: `artifacts/models/demand_model.joblib` (+ metadata) and
`artifacts/models/elasticity_table.csv` (per-UPC elasticities with
diagnostics, shrinkage weights and the estimation window).

Figures: 22 PNGs. Audit log: `artifacts/recommendation_log.csv`.

Phase N artifacts: `batch_performance.json` (loop vs vectorised runtime and the
field-by-field equivalence result), `metric_consistency.json` (every checked
statement and its source of truth), `test_suite.json` (the collected test
count), `report_build.json` (DOCX/PDF page, word and heading counts).

## Freeze record

| item | value |
| --- | --- |
| version | **1.0.0** (`pyproject.toml`, `src/pricing_engine/__init__.py`, and the FastAPI app which imports it) |
| Git history | 12 commits; the repository had **no commits** before this phase, and none were fabricated or back-dated |
| verified commit | `32f4653` — the tree at which the gauntlet in this file was executed |
| tag | annotated **`v1.0.0`**, created after the gauntlet passed; read the target with `git rev-parse v1.0.0^{commit}` |
| pushed? | **no** — no remote is configured, and neither commits nor tag were published |
| working tree | clean |
| licensed data in Git | none: no `data/` path, no `.parquet`, no `.zip`, no row-level derived extract |

## Open issues (not hidden)

Freezing v1.0 closed none of these.

1. **The GitHub-hosted CI run has never executed.** The workflow exists and
   every step was reproduced locally; no remote is configured and none was
   created. Status: LOCAL PASS, HOSTED NOT EXECUTED.
2. **Uplift numbers remain model-internal estimates.** The same fitted price
   response proposes and scores candidate prices. Only an experiment
   (`docs/PRICING_EXPERIMENT.md`) can turn them into measured uplift.
3. **The elasticity itself is observational.** Shrinkage improves stability,
   not identification; promotion contamination in the pooled estimate
   propagates into every shrunk product estimate.
4. **133 of 372 products have no usable own elasticity** and are priced with
   the pooled fallback (35.8% of decision contexts), flagged
   `POOLED_ELASTICITY_FALLBACK` and never rated LOW risk.
5. **REVIEW_REQUIRED produces a human queue** (10.4% of contexts) that this
   demo does not staff.
6. **Zero-price exclusion tilts the sample** toward continuously stocked,
   higher-volume series (mean weekly units 21.0 vs 3.1 for heavily excluded
   series).
7. **Batch optimization is per-context** (~4 min for 3,000 contexts); the
   vectorised `simulate_many` path exists but is not used by the batch script.
8. **A second agent session was writing into this repository** during the
   original build. The tree was re-verified end to end afterwards, and every
   command in this table was re-run for Phase L.
9. **Not production ready**: no orchestration, model registry, live monitoring
   store or serving SLOs - by design for a portfolio build.
7. **The batch speed-up is one local measurement**, not a scalability result:
   3,000 contexts, one machine, one process.
8. **The serialised model is not pinned to its training environment** - it
   loads under a newer scikit-learn with a version warning.
