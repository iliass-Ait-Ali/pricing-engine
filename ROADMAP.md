# ROADMAP - AI Pricing & Revenue Optimization Engine

Statuses: `NOT STARTED` | `IN PROGRESS` | `IMPLEMENTED` | `TESTED` | `VALIDATED` | `BLOCKED` | `LIMITED`

A phase is only `VALIDATED` when it is implemented, executed on the real data,
inspected, tested where appropriate, documented and integrated.

**v1.0.0 is frozen.** Phases A-N below are complete and closed. The scientific
architecture is fixed: no new estimator, model family, feature or optimisation
scope is added inside v1.0. Candidate directions for a future version live in
[`FUTURE_WORK.md`](FUTURE_WORK.md) and are deliberately not implemented here -
several of them require an experiment this data cannot supply.

| Phase | Scope | Depends on | Acceptance criteria | Status |
| --- | --- | --- | --- | --- |
| **A. Data foundation** | Official download, schema, cleaning, canonical parquet, data audit | - | Official Cereals files acquired; canonical table built; formula/structural checks pass; `reports/01_DATA_AUDIT.md` has real numbers | `VALIDATED` |
| **B. Pricing EDA + economics** | Historical scale, price variation per series, eligibility screen, reusable economics module | A | `reports/02_PRICING_EDA.md` with real numbers; figures; economics tests pass | `VALIDATED` |
| **C. Feature availability + leakage audit** | Decision-time classification of every feature; shifted lag/rolling features | B | `docs/FEATURE_AVAILABILITY.md`, `docs/DATA_LEAKAGE_AUDIT.md`; lag-alignment and poisoning tests pass | `VALIDATED` |
| **D. Elasticity analysis** | Arc elasticity, log-log naive vs controlled, causal limitations | B, C | `reports/03_ELASTICITY_ANALYSIS.md`; `docs/CAUSAL_LIMITATIONS.md`; elasticity tests pass | `VALIDATED` |
| **E. Demand modelling** | Naive baselines, interpretable regression, gradient boosting; strict temporal split | C | `reports/04_MODEL_COMPARISON.md`; `artifacts/metrics/model_metrics.json`; model serialised and reloadable | `VALIDATED` |
| **F. Price-response validation + simulation** | Demand curves at fixed context, monotonicity/extrapolation checks, vectorised counterfactual simulator | E | `reports/05_PRICE_RESPONSE_VALIDATION.md`; simulation invariant tests pass | `VALIDATED` |
| **G. Constrained optimization** | Objectives, constraints, reason codes, risk layer, analytical tests | F | Analytical optimum recovered; all constraint tests pass; `KEEP_CURRENT` / `REVIEW_REQUIRED` reachable | `VALIDATED` |
| **H. Offline / historical evaluation** | Policy comparison, recommendation stability, constraint activation | G | `reports/06_BACKTEST.md` with model-internal estimated (not causal) policy economics | `VALIDATED` (`LIMITED`: circular by construction) |
| **I. API** | FastAPI: health, model info, predict, simulate, recommend | G | API tests pass; model loaded once at startup | `VALIDATED` |
| **J. Dashboard** | Streamlit, 9 pages including a limitations page | G, H | Smoke test renders every page without exception | `TESTED` |
| **K. Monitoring, CI, Docker, final docs** | Drift/schema checks, CI on fixtures, Dockerfile, final validation report | I, J | `reports/VALIDATION_SUMMARY.md` with real command output | `VALIDATED` (Docker built and run in Phase N; GitHub-hosted CI still not executed) |
| **L. Pricing-science hardening** | Hybrid elasticity price response, elasticity estimator + shrinkage, method comparison, sensitivity, HIGH-risk gating, uplift language, zero-price audit, cost-leakage audit | E-K | Hybrid works and is the default; elasticity fitted on training weeks only; product reliability handled; sensitivity reported; HIGH risk never auto-actionable; zero-price and cost audits published; ml kept as benchmark; tests pass | `VALIDATED` |

| **M. Final scientific audit** | Constraint attribution, rule-only and guardrail ablations, shrinkage/inference re-derivation, out-of-time price response, elasticity stability, eligibility funnel, decision-state invariants, claim audit | L | `reports/11`-`reports/20`; 0 UNSUPPORTED claims | `VALIDATED` |
| **N. Engineering closure and v1.0 freeze** | Executive-summary wording fix, batch vectorisation with equivalence tests, Docker build + run, CI reproduced locally, generated README metrics, cross-document numeric audit, report regeneration, version freeze and initial Git history | M | `reports/21_FINAL_ENGINEERING_CLOSURE.md`; 519 tests at Phase N, ruff clean, 0 UNSUPPORTED claims, 0 numeric mismatches; DOCX/PDF rebuilt from the Markdown source | `VALIDATED` |

## Gate evidence (actual command output)

| Gate | Evidence |
| --- | --- |
| A | `python scripts/build_dataset.py` -> 6,602,582 raw -> 4,707,776 canonical rows; `python scripts/validate_data.py` -> 15/15 checks pass |
| B | `python scripts/run_eda.py` -> `reports/02_PRICING_EDA.md`; 19,707 / 36,443 series (54.1%) eligible for pricing |
| C | `python scripts/build_features.py` -> 4,671,333 usable rows; 13 leakage/lag tests pass |
| D | `python scripts/run_elasticity.py` -> naive -0.348, +UPC FE -2.289, +UPC x store FE -2.419, +controls -1.909 |
| E | `python scripts/train.py` -> selected ridge log-log, validation WAPE 0.4135, test WAPE 0.4565 |
| F | `python scripts/price_response.py` -> 100% monotone decreasing curves, median implied elasticity -3.10 |
| G | `pytest tests/test_optimizer.py` -> 23 passed, analytical optimum `(a+bc)/(2b)` recovered within one grid step |
| H | `python scripts/backtest.py` -> weeks 390-399, mean weekly WAPE 0.4777; ML policy +10.45% model-internal estimated |
| I | `pytest tests/test_api.py` -> 15 passed |
| J | `python scripts/smoke_dashboard.py` -> all 9 pages rendered |
| K | `pytest` -> 149 passed *(at Phase K; the current count is generated into README section 2)*; `ruff check .` -> All checks passed; `docker build` -> NOT TESTED at Phase K (daemon unavailable at the time; built and run in Phase N) |
| L | `python scripts/estimate_elasticity.py` -> pooled -2.029 (training weeks 2-257 only); `python scripts/compare_price_response.py` -> ml vs pooled agree on the decision state 79.7% of the time; `python scripts/optimize.py --batch 3000` -> 0 of 1,039 HIGH-risk contexts actionable, 10.4% REVIEW_REQUIRED; `python scripts/audit_zero_price.py` -> 677 of 1,851,380 zero-price rows had sales; `python scripts/audit_cost_leakage.py` -> 3/3 checks pass |
| M | `make audit` -> `reports/11`-`reports/20`. Constraint attribution: **2.9%** of final recommendations set by an interior model optimum. Rule-only ablation: a no-demand-model rule matches the engine within one grid step **82.5%** of the time. Shrinkage corrected (mean weight 0.954 -> **0.780** on panel-robust standard errors), **239/372** products usable. Out-of-time check on 154,899 unseen price-change episodes: `shrunk` lowest WAPE in every split. `pytest` -> **519 passed**; `ruff check .` -> All checks passed; `audit_claims.py --strict` -> 0 UNSUPPORTED |

## After v1.0

Everything that was listed here as a "next step" has moved to
[`FUTURE_WORK.md`](FUTURE_WORK.md), which separates the two categories that
must not be confused:

* work that only needs engineering, and
* work that needs **new evidence** - above all a randomised pricing experiment,
  without which no causal claim, and no honest measurement of realised uplift,
  is available at any amount of modelling effort.

One item from that list was completed inside the v1.0 closure rather than
deferred: the Docker image is now built and run (Phase N). The
GitHub-hosted CI run remains outstanding and is recorded as such.

## Phase O - post-freeze engineering (no new science)

Three items from `FUTURE_WORK.md` §3 ("Product and operations - no new
science") were implemented after the v1.0.0 freeze: a recommendation
review/approval workflow, monitoring metric history + local threshold
alerting, and a batch scale-testing harness. **This is not a reopening of
v1.0's frozen scientific scope** - no estimator, model, feature or
optimisation logic changed; the pricing engine's outputs are identical to
before this phase. Full evidence: `reports/22_POST_FREEZE_ENGINEERING.md`.

Two related items from the same list were explicitly NOT attempted, because
they are not engineering tasks: a real cost feed needs an external data
source this project does not have, and "staffing" the `REVIEW_REQUIRED`
queue is an operational/HR decision, not code - what was built is the tool a
human reviewer would use, not the reviewer.

| Sub-item | Evidence |
| --- | --- |
| Review/approval workflow | `pytest tests/test_review_workflow.py` -> 9 passed; `python scripts/review.py --list`; one real `REVIEW_REQUIRED` recommendation walked GENERATED -> REVIEWED -> APPROVED -> PUBLISHED via the CLI, and a second via the dashboard's "Review queue" page |
| Monitoring history + local alerts | `python scripts/monitor.py` -> writes `artifacts/metrics/monitoring_history.jsonl` (append-only, one line per run) and prints `alerts: PASS/FAIL`; `pytest tests/test_monitoring.py` -> 16 passed |
| Batch scale benchmark | `python scripts/benchmark_scale.py --sizes 500,3000,10000,30000` -> `artifacts/metrics/batch_scale_benchmark.json`; `pytest tests/test_batch_scale_benchmark.py` -> 3 passed |

Adding `rec_id` to the recommendation-log schema triggered the log's own
existing archive-on-schema-change behaviour: the real, pre-Phase-O
`artifacts/recommendation_log.csv` (6,005 rows) was archived - not deleted -
to `recommendation_log__schema_20260825T115810.csv` on the first `optimize.py`
run after this change, exactly as it did once before at the Phase M schema
change. This was expected and disclosed before implementation, not an
incident.
