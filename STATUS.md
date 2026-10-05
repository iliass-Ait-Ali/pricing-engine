# Status

**Version: 1.1.0** (2026-10-04). The v1.0 pricing engine (estimators,
guardrails, decision policy) is unchanged. v1.1 adds layers around it: a
synthetic public demo, a business-value and product-role analysis, a
ground-truth recovery study, and a guarded LLM copilot (see the "v1.1" rows
below and `ROADMAP.md`, Phase P).
Published on 2026-10-05 at <https://github.com/iliass-Ait-Ali/pricing-engine> (`master`, tags `v1.0.0` and `v1.1.0`).
The first GitHub-hosted CI run passed on Python 3.11, 3.12 and 3.13 plus the demo job.
v1.0 verification: **2026-08-19**, the final engineering closure
(`reports/21_FINAL_ENGINEERING_CLOSURE.md`), on top of the Phase M scientific
audit (`reports/11`-`reports/20`, `reports/VALIDATION_SUMMARY.md`).

**Portfolio classification: PORTFOLIO READY WITH CLEAR LIMITATIONS.** The
limitations that must be disclosed are listed under "Top open issues" below and
in `KNOWN_LIMITATIONS.md`; the interview defence is in
`docs/INTERVIEW_RED_TEAM.md`.

**What "frozen" means.** The scientific architecture, the estimators, the
guardrails and the decision policy are fixed for v1.0. No limitation below is
solved by the freeze; freezing is a statement about *change control*, not about
evidence. Ideas that would require new science are in `FUTURE_WORK.md` and are
deliberately **not** implemented here.
Statuses: `NOT STARTED` | `IN PROGRESS` | `IMPLEMENTED` | `TESTED` | `VALIDATED` | `BLOCKED` | `LIMITED`

| Component | Status | Evidence | Remaining issue |
| --- | --- | --- | --- |
| Official data acquisition | `VALIDATED` | `SOURCE.json` with URLs, sizes and SHA-256; `wcer.zip` 42,402,094 B, `wcer.csv` 458,400,475 B | First automated attempt failed mid-download (connection reset); a resumable retry completed it. The downloader retries and reports BLOCKED rather than substituting data. |
| Canonical dataset | `VALIDATED` | 6,602,582 -> 4,707,776 rows; `reports/01_DATA_AUDIT.md` | 26.56% of raw rows excluded for `price = 0`; documented, not silent |
| Data validation | `VALIDATED` | 15 / 15 checks, `scripts/validate_data.py` | - |
| Pricing EDA + economics module | `VALIDATED` | `reports/02_PRICING_EDA.md`, 6 figures, 16 economics tests | - |
| Feature layer + leakage controls | `VALIDATED` | `docs/FEATURE_AVAILABILITY.md`, `docs/DATA_LEAKAGE_AUDIT.md`, 13 tests incl. a poisoning test | Promotion flag assumed known at decision time (documented assumption) |
| Elasticity analysis | `VALIDATED` | `reports/03_ELASTICITY_ANALYSIS.md`: -0.348 naive -> -2.419 with UPC x store FE -> -1.909 with controls | Observational only; no causal identification possible in this data |
| Demand models | `VALIDATED` | `reports/04_MODEL_COMPARISON.md`; selected ridge log-log, valid WAPE 0.4135, test 0.4565 | Point predictions only; systematic under-forecast (bias -3.88) |
| Model persistence | `VALIDATED` | `scripts/evaluate.py`: reload WAPE difference 0.00e+00 | - |
| Price-response validation | `VALIDATED` | `reports/05_PRICE_RESPONSE_VALIDATION.md`: 100% monotone decreasing, median implied elasticity -3.10 | Implied elasticity steeper than the econometric benchmark - flagged everywhere |
| Counterfactual simulator | `VALIDATED` | 16 tests: only price features move, cost held fixed, one batched call | - |
| Constrained optimizer | `VALIDATED` | 23 tests incl. analytical optima `(a+bc)/(2b)` and `a/(2b)` | - |
| Risk layer + decision gate | `VALIDATED` | HIGH = risky; 0 of 1,039 HIGH-risk contexts actionable; REVIEW_REQUIRED for 10.4%; 245 decision-state invariant tests (`reports/19`) | Still a heuristic, not calibrated confidence. **Checked in v1.1** against out-of-time prediction error (`reports/26`): bands only weakly separate error (HIGH/LOW WAPE 1.04x); extrapolation is the factor that matters |
| Price response (Phase L/M) | `VALIDATED` | hybrid `Q(p) = Q_hat(p0) * (p/p0) ** epsilon`; pooled -2.029 / shrunk per-UPC; best out-of-time WAPE of the three methods (`reports/16`) | Elasticity is observational; shrinkage fixes stability, not identification |
| Elasticity estimation (Phase L/M) | `VALIDATED` | training weeks 2-257 only; 239/372 products usable; REML tau^2 0.766, mean weight 0.780, panel-robust SEs; `reports/14`, `reports/15`, `reports/18` | 133 products priced with the pooled fallback (35.8% of decision contexts) |
| Zero-price audit (Phase L) | `VALIDATED` | `reports/09`: 1,851,380 rows, 677 with sales, 73.4% leading/trailing | Rule unchanged; selection tilt documented |
| Cost leakage audit (Phase L) | `VALIDATED` | `reports/10`: 3/3 checks pass on real data; 5 unit tests | Proves temporal availability, not economic correctness of AAC |
| Batch recommendations + audit log | `VALIDATED` | `reports/07`: 58.9% actionable / 10.4% review / 30.8% keep; +8.30% model-internal estimated | **v1.0: the batch path is now vectorised** (`optimize_price_batch`, 122.0 s → 2.4 s on 3,000 contexts, bit-identical output; `tests/test_batch_equivalence.py`, `artifacts/metrics/batch_performance.json`) |
| Constraint attribution (Phase M) | `VALIDATED` | `reports/11`: 2.9% of final recommendations set by an interior model optimum; change cap binds first in 44.6% | The finding itself is the limitation - see open issue 1 |
| Rule-only ablation (Phase M) | `VALIDATED` | `reports/12`: a no-model "max allowed increase" rule matches the engine's price within one grid step 82.5% of the time | Decisions compared, not profits - a profit comparison would be circular |
| Guardrail ablation (Phase M) | `VALIDATED` | `reports/13`: median price variation across elasticity scenarios 79.8% research -> 0.0% under the default policy | - |
| Robust inference (Phase M) | `VALIDATED` | `reports/15`: two-way clustered SE is 16.2x the conventional one; estimators validated against statsmodels | Two-way clustering is used at pooled level only; per-UPC uses max(store, week) |
| Out-of-time price response (Phase M) | `VALIDATED` | `reports/16`: 154,899 unseen price-change episodes; `shrunk` best WAPE in every split | Naturalistic prediction, NOT causal identification |
| Elasticity stability (Phase M) | `VALIDATED` (`LIMITED`) | `reports/17`: category stable (max pairwise z 1.36), product ordering not (rank corr +0.19) | Product-level elasticity must not be described as a durable asset |
| Claim audit (Phase M) | `VALIDATED` | `reports/20`: 0 UNSUPPORTED claims; `scripts/audit_claims.py --strict` enforces it | - |
| Offline backtest | `VALIDATED` (`LIMITED`) | `reports/06`, weeks 390-399: ML policy +10.45% model-internal estimated, 36.9% unchanged | Circular by construction: the same fitted response chooses and scores |
| FastAPI service | `VALIDATED` | 15 API tests; model loaded once at startup | No auth, no rate limiting - demo scope |
| Streamlit dashboard | `TESTED` | `scripts/smoke_dashboard.py`: all 10 pages render (Phase O added the "Review queue" page) | Loads the full 4.7 M-row panel in memory; not a deployment pattern |
| Monitoring | `IMPLEMENTED` | `reports/MONITORING_DESIGN.md`, 16 tests; Phase O added `artifacts/metrics/monitoring_history.jsonl` (append-only run history) and local threshold alerting (`evaluate_alerts`, `configs/config.yaml` `monitoring:` block) | Offline only; thresholds are local, loosely calibrated against this project's own numbers - not agreed with a business or fitted against realised out-of-sample error; still no scheduling, no metric-store database, no paging |
| Review/approval workflow (Phase O) | `VALIDATED` | `pytest tests/test_review_workflow.py` -> 9 passed; `scripts/review.py` CLI + dashboard "Review queue" page; `recommendation_transitions.csv` (append-only, never rewrites `recommendation_log.csv`) | Gives a human the tool to review/approve/reject/publish a recommendation; does not staff, schedule or SLA the queue itself |
| CLI + Makefile + demo | `VALIDATED` | `make demo` produces a real recommendation on real data | - |
| Documentation | `VALIDATED` | 15 docs + 23 reports, all numbers generated from execution | **v1.0: README headline metrics are generated** (`scripts/update_readme_metrics.py`) and a cross-document numeric audit (`scripts/audit_metric_consistency.py`, 212 statements, 0 mismatches) is test-enforced |
| Test suite | `VALIDATED` | **657 tests collected**; full run: 0 failed, 1 skipped by design (the live-count check skips on subsets) | Decision-critical paths prioritised over coverage percentage |
| Lint | `VALIDATED` | `ruff check .` - All checks passed | - |
| CI workflow | `VALIDATED` | `.github/workflows/ci.yml`: lint, smoke import, README-metrics check and tests on Python 3.11, 3.12 and 3.13; a `demo` job builds the synthetic demo, renders every dashboard page and builds both Docker images. First hosted run green on 2026-10-05 ([run](https://github.com/iliass-Ait-Ali/pricing-engine/actions/runs/37248266778)) | CI never sees the licensed data: real-data tests skip there, and the API contract runs on the synthetic demo |
| Docker | `VALIDATED` | **built and run**: `docker build -t pricing-engine:local .` (1.66 GB), container serves `/health`, `/model/info` and a real `/recommend-price` identical to the local run, runs as non-root `appuser` (uid 1000); image contains no data files | Model and processed data are **mounted read-only**, never baked in (Dominick's licence); the image is not hardened, load-tested or deployed |
| v1.1 public demo (synthetic) | `VALIDATED` | `configs/demo.yaml`, `scripts/make_demo.py`, `Dockerfile.demo` built and served locally (dashboard + `/api`); `tests/test_demo_config.py` (demo paths never touch real artifacts), `tests/test_demo_e2e.py` | Not yet deployed: the Hugging Face Space needs the owner's token (`.github/workflows/deploy-demo.yml`) |
| v1.1 business value + product roles | `VALIDATED` | `reports/23_BUSINESS_VALUE.md` (+4.1% to +9.4%, model-internal), `reports/24_PRODUCT_ROLES.md`; headline numbers checked by `audit_metric_consistency.py` | Same circularity as every uplift: a range, not a measurement |
| v1.1 ground-truth recovery study | `VALIDATED` (`LIMITED`) | `reports/25_GROUND_TRUTH_STUDY.md`, 7 scenarios x 5 seeds, `tests/test_ground_truth_study.py` | Synthetic; the demand form matches the engine's, which flatters it; not evidence about Dominick's |
| v1.1 Pricing Copilot | `VALIDATED` (`LIMITED`) | `src/pricing_engine/copilot/`, `POST /copilot/ask`, dashboard page; live evaluation recorded on 2026-10-05 with a local open-weights model (`qwen2.5:7b`): 17 of 20 cases pass, 0 ungrounded numbers shown in any run; the first run (8 of 20) exposed invented product codes and a guard gap, both fixed (`docs/COPILOT_CARD.md`) | One small model, one run, 20 questions, and the fixes were made on the same questions: a development-set figure. The default hosted model has not been run. The guard checks form, not meaning |
| Second category (crackers) | `VALIDATED` (`LIMITED`) | `reports/27_SECOND_CATEGORY.md`: unchanged pipeline and settings on 2,228,269 crackers rows; 7 of 8 headline findings point the same way (1.4% of final prices from the model's own optimum; a no-model rule within one grid step 91.6% of the time) | One retailer, one decade: a robustness check, not proof of generality. In crackers the model-internal value range is -0.1% to +3.8%, so its low end is not positive |
| Randomised experiment | `NOT STARTED` (by design) | `docs/PRICING_EXPERIMENT.md` designs it | Would be required for any causal claim |

## Top open issues

Ranked by how much they should change what you claim about the project.

1. **Business rules, not the model, set the magnitude of most recommendations.**
   Only 2.9% of final recommendations come from an interior optimum of the
   estimated price response; 57.1% sit on a guardrail corner. A rule that never
   looks at demand matches the engine's price within one 5-cent grid step 82.5%
   of the time. This is a legitimate architecture, but the project must be
   described as rule-bounded pricing with a learned direction, not as ML
   pricing. `reports/11`, `reports/12`, `reports/13`.
2. **No causal identification.** Prices were set by the retailer, not
   randomised. Every price effect here is observational.
   `docs/CAUSAL_LIMITATIONS.md`.
3. **Cross-price / substitution effects are not modelled.** All elasticities are
   own-price, so the category effect of pricing many products at once is not the
   sum of the per-product effects.
4. **Counterfactual policy economics are model-internal and circular**: the same
   fitted response proposes and scores the prices. Only an experiment can
   validate them.
5. **Product-level elasticity is not stable across windows** (rank correlation
   +0.19 between disjoint training windows), though the category elasticity is
   (max pairwise z = 1.36). `reports/17`.
6. **133 of 372 products have no usable own elasticity** and are priced with the
   pooled fallback - 35.8% of decision contexts. `reports/18`.
7. **The risk layer is a heuristic, not calibrated confidence.** Thresholds are
   asserted, not fitted. v1.1 checked them against out-of-time prediction
   error (`reports/26_RISK_CALIBRATION.md`): the typical episode's error rises
   from LOW to HIGH, but only slightly (HIGH/LOW WAPE 1.04x), and MEDIUM has
   the lowest volume-weighted error. The band is a weak guide to forecast
   accuracy; its value is gating extrapolation and thin evidence.
8. **REVIEW_REQUIRED creates a human queue** (10.4% of contexts, 1,353 of
   13,964 in the full week) that this demo does not staff. Phase O added the
   software a reviewer would use (`scripts/review.py`, the dashboard's
   "Review queue" page, and an append-only `recommendation_transitions.csv`)
   - no real staffing, SLA, or notification path is claimed.
9. **The AAC cost proxy** is an average acquisition cost, so it lags true
   replacement cost. Temporal availability is proven (`reports/10`); economic
   correctness is not.
10. ~~**The GitHub-hosted CI run has never executed.**~~ **Resolved on
    2026-10-05:** the repository is public and the first hosted run passed on
    all four jobs. CI runs on synthetic fixtures and the synthetic demo only,
    never on the licensed data.
11. A concurrent agent session wrote into this repository during the original
    build; the tree was re-verified end to end afterwards (519 tests, lint, all
    reports regenerated).
12. **Batch scale is measured, not proven.** The vectorisation benchmark is one
    single-process run on one machine (3,000 contexts). It says nothing about
    millions of SKUs, and no such claim is made. Phase O added a multi-size
    sweep (`scripts/benchmark_scale.py`, `reports/22`) up to 30,000 contexts,
    but it remains single-machine, and sizes above the 3,000-context real
    sample are that same sample **replicated**, not independent catalogue
    growth.
13. ~~**One known, disclosed test failure at the end of Phase O.**~~ **Resolved
    in v1.1.** The consistency audit now skips fenced blocks marked
    `<!-- metric-audit: verbatim-transcript -->`, and the Phase N pytest
    transcript in the full report carries that marker. The captured output
    itself is unchanged. v1.1 also fixed an intermittent review-workflow
    failure: `rec_id` values that look like numbers were parsed as floats.
14. **The copilot has been evaluated against one small local model only.**
    `qwen2.5:7b` passes 17 of the 20 cases after the fixes its first run prompted
    (8 of 20 before). That is a development-set figure on one model. The
    default hosted model (free on Groq) has not been run; re-recording needs a key.
15. **The public demo is built and runs locally but is not deployed.** The
    Hugging Face Space needs the owner's token.
