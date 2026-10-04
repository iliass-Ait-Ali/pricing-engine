# ROADMAP - AI Pricing & Revenue Optimization Engine

Statuses: `NOT STARTED` | `IN PROGRESS` | `IMPLEMENTED` | `TESTED` | `VALIDATED` | `BLOCKED` | `LIMITED`

A phase is only `VALIDATED` when it is implemented, executed on the real data,
inspected, tested where appropriate, documented and integrated.

> **Looking forward?** The plan after Phase O is at the bottom of this file:
> [Forward roadmap](#forward-roadmap-from-2026-10-03). Everything above it is
> the historical phase log.

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

---

## Forward roadmap (from 2026-10-03)

Phases A-O built and audited the engine. This section is the plan for what
comes next. It keeps the project's own rule from `FUTURE_WORK.md`:
**engineering work** (code, tests, time) is separated from **evidence work**
(needs a new intervention, so it is blocked on this dataset).

### Where the project actually stands (re-checked 2026-10-03)

| area | state |
| --- | --- |
| Science | Done to the limit of the data. Pipeline, elasticity + shrinkage, hybrid price response, guardrails, risk gate, out-of-time validation and a full claim audit all exist and are evidenced (`reports/01`-`reports/20`). |
| Engineering | API, dashboard (10 pages), review workflow, monitoring history, scale harness, Docker image built and run. |
| Build health | **Not green.** Full `pytest` run today: 1 known failure (`test_metric_consistency.py`, the verbatim transcript at line 7407 of the full report, see `reports/22` §5.1) **plus 1 newly found intermittent failure**: `test_review_workflow.py::test_reject_is_terminal` (details in P1). `ruff check .` is clean. |
| Visibility | **Nothing is public.** No Git remote, the `v1.0.0` tag is local only, and the GitHub CI workflow has never run. |
| Docs | Very thorough, with some drift: README and `docs/TECHNICAL_DESIGN.md` still say "9 pages" (the dashboard has 10). `KNOWN_LIMITATIONS.md` items 17 and 18 describe problems that were fixed at Phase N (Docker not built, batch not vectorised). Item numbers 32-34 appear twice. |

**Assessment.** The scientific work has reached diminishing returns: another
audit report will not change what the project can claim. Right now the best
value comes, in order, from (1) a green, trustworthy build, (2) making the
work visible, and (3) one or two pieces of new science that build on the
project's most interesting finding: *the guardrails, not the model, set most
prices*. Most of the remaining ideas are blocked on an experiment that this
data cannot supply, and should stay blocked.

### Phase P - Green build and consistency (v1.0.1) - size S

No science changes. Goal: `pytest` passes with no exceptions, and every
document matches the current code.

| # | item | acceptance criterion |
| --- | --- | --- |
| P1 | **Fix the `rec_id` CSV parsing bug.** `rec_id` is 12 hex characters, so about 0.6% of ids are made only of digits plus at most one `e` (for example `659826115e98`). `pd.read_csv` then parses them as floats (`6.59826115e+106`). In a small transitions file that silently corrupts the id: the earlier transition is not found and a legal `REVIEWED -> REJECTED` is refused as `GENERATED -> REJECTED`. This is why `test_reject_is_terminal` fails intermittently. Fix: read with `dtype={"rec_id": str}` in `audit.py` (both `read()` methods) and `dashboard/app.py`, or generate ids that cannot parse as numbers. | Regression test with a fixed numeric-looking id; the review tests pass 200 times in a row |
| P2 | **Close the known consistency failure honestly.** Let `audit_metric_consistency.py` skip text that is explicitly marked as a verbatim historical transcript (for example a `<!-- metric-audit: verbatim-transcript -->` marker before the fenced block), instead of editing captured output. | Full `pytest` has 0 failures; the transcript is unchanged |
| P3 | Fix the doc drift: "9 pages" -> 10; mark `KNOWN_LIMITATIONS.md` items 17 and 18 as resolved (with a pointer to Phase N); fix the duplicate item numbers. | `audit_metric_consistency.py` and `audit_claims.py --strict` stay clean |
| P4 | Add Python 3.13 to the CI matrix. The local environment where all of this was verified runs 3.13, but CI only tests 3.11 and 3.12. | Workflow file updated |
| P5 | Repo hygiene: decide whether the two generation prompts (`AI_Pricing_..._Master_Prompt.txt` and `Claude Code Prompt - ...md`, the latter duplicated in `reports/`) belong in a public portfolio repo. Either move them to `docs/process/` with a sentence explaining them, or remove them from the tree. | One copy or none, by explicit choice |

### Phase Q - Make it visible - size S/M - **needs the owner's decision**

The earlier "no remote, no push" decision was correct while the build was
being frozen. For a portfolio project, though, it is now the biggest gap.
Nothing below needs new science.

| # | item | acceptance criterion |
| --- | --- | --- |
| Q1 | Create the GitHub repo (the licensed data stays git-ignored, as it already is) and push `master` and the `v1.0.0` tag. | `git remote -v` shows the remote; the tag is visible |
| Q2 | **First real CI run.** Fix whatever only shows up on a clean Ubuntu runner. | Green badge in the README; `STATUS.md` open issue 10 closed |
| Q3 | GitHub Release `v1.0.1` with the PDF report attached. | Release page exists |
| Q4 | A 2-minute front door: shorten the top of the README to the question, the headline finding (rule-bounded pricing with a learned direction), one dashboard screenshot and one figure. Move the detail below the fold. | A first-time reader gets the point without scrolling |
| Q5 | Short demo (GIF or 60 to 90 s video) of the simulator, a recommendation and the review queue. | Linked from the README |
| Q6 | (Optional) A hosted read-only demo on synthetic or aggregated data only, since the Dominick's licence forbids redistributing the raw panel. | Public URL serves no raw Dominick's rows |

### Phase R - Engineering hardening (v1.1) - size M

| # | item | why |
| --- | --- | --- |
| R1 | Lock the dependencies (`uv lock` or `pip-tools`) and pin the training runtime. Persist the model in a version-stable form (for example the ridge coefficients as JSON, or `skops`). | `KNOWN_LIMITATIONS.md` #45: an unpinned scikit-learn pickle |
| R2 | A cross-platform task runner (a `scripts/run_all.py` or `nox` session) that mirrors the Makefile targets. | `make` is not available on the Windows machine where everything was verified (#47) |
| R3 | API: API-key auth, request limits, structured logging, request IDs. | Currently demo scope with no auth |
| R4 | Dashboard reads pre-aggregated parquet (or DuckDB) instead of the whole feature panel. | Lower memory use; a prerequisite for Q6 |
| R5 | Run `monitor.py` on a schedule (GitHub Actions cron on fixtures, or a local scheduled task) and send alerts somewhere a person sees them. | Monitoring is offline only today |
| R6 | Review workflow: actor required, notes recorded, and an "age in queue" column. A basic SLA view on the dashboard page. | Turns the tool into something a process could use |

### Phase S - New science on the same data (v2.0) - size L

Everything here is still **observational** and must be labelled that way.
Ordered by value per unit of risk.

| # | item | why it is worth doing |
| --- | --- | --- |
| S1 | **Synthetic ground-truth study.** Simulate panels with *known* elasticities and confounded pricing (promotion-driven price cuts, store heterogeneity), run the unchanged pipeline, and measure how far the estimates and the chosen prices are from the truth. | The only way to measure estimator bias without an experiment. It turns "observational, could be biased" into "this is how much bias we see under these confounding levels". Not in `FUTURE_WORK.md` yet. |
| S2 | **Replicate on a second Dominick's category** (for example soft drinks or canned soup), using `docs/USING_YOUR_OWN_DATA.md`. | Tests whether the headline findings (interior-optimum share, rule-only agreement, elasticity stability) are about cereal or about the method. The strongest generalisation evidence available without new data. |
| S3 | **Evidence-weighted guardrails** (`FUTURE_WORK.md` 1.5): make the change cap a function of the elasticity confidence-interval width. Report it as a *policy* change, side by side with v1.0, and never loosen a cap only to make the model look more influential. | Turns the project's main finding into a design. |
| S4 | **Risk bands checked against out-of-time error.** The 154,899 out-of-time price-change episodes have realised demand. Check whether HIGH-risk contexts really have larger prediction errors there. | Moves the risk layer from asserted to *empirically checked for prediction error*. It is still not causal uplift; `FUTURE_WORK.md` 1.4 should be reworded to make that difference clear. |
| S5 | Quantile demand forecasts (`FUTURE_WORK.md` 1.3), so the optimiser can weigh downside risk. | Feeds S4 and a risk-aware objective |
| S6 | **Cross-price effects for a small set of plausible substitute pairs** (`FUTURE_WORK.md` 1.1), with honest uncertainty, before any full demand system. | The largest missing mechanism. Only after S1, so its bias can be measured on simulated data first. |
| S7 | Joint category optimiser (`FUTURE_WORK.md` 1.2). | Only once S6 gives credible estimates |

### Phase T - Evidence-gated (blocked) - unchanged

A randomised pricing experiment (`docs/PRICING_EXPERIMENT.md`), then causal
estimation, realised uplift measurement and any bandit or RL approach. These
stay blocked until a data partner or an experimental dataset exists. No
causal-looking method gets applied to the observational panel in the
meantime.

### Explicitly not on the roadmap

* More audit reports on the v1.0 engine. The claims are already audited.
* More forecasting model families or deep learning (`FUTURE_WORK.md` §4).
* Rebuilding the DOCX and PDF for every change. Keep the Markdown report as
  the live source and rebuild the other formats only at releases.

### Suggested order

P (all) -> Q1-Q4 -> R1, R2 -> S1 -> S2 -> S3/S4 -> the rest as interest allows.
