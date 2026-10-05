# 20. Portfolio claim audit

Every phrase in this repository that could overstate what the project has shown,
classified. Run by `scripts/audit_claims.py`; `--strict` makes it fail the build
if an UNSUPPORTED claim reappears.

Scanned: `.md`, `.py`, `.yaml` across the repository (excluding data, artifacts
and the original brief). **418 occurrences** of
10 watched patterns.

## 1. Verdict summary

| Verdict | Occurrences |
| --- | ---: |
| SAFE | 363 |
| NEEDS QUALIFICATION | 55 |
| UNSUPPORTED | 0 |

An occurrence is scored SAFE when the surrounding sentence *denies* the claim -
this repository states "not causal", "never realised", "not production ready"
many times, and those are the opposite of the offence.

## 2. The watched patterns

| Pattern | Default verdict | Occurrences | SAFE | NEEDS QUAL. | UNSUPPORTED | Preferred wording |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `\bcausal\b` | SAFE | 200 | 200 | 0 | 0 | observational price-response estimate |
| `production[ -]ready` | SAFE | 13 | 13 | 0 | 0 | offline portfolio project with a runnable API and dashboard |
| `\boptimal price\b` | UNSUPPORTED | 4 | 4 | 0 | 0 | candidate profit-maximising price under model assumptions |
| `\b(increased|improved|achieved|delivered|realised|realized) (profit|revenue|margin)` | UNSUPPORTED | 5 | 5 | 0 | 0 | model-internal estimated gross-profit uplift (offline simulation) |
| `profit uplift` | NEEDS QUALIFICATION | 26 | 24 | 2 | 0 | model-internal estimated profit uplift |
| `\bvalidated\b` | NEEDS QUALIFICATION | 118 | 77 | 41 | 0 | validated out of time on unseen price-change episodes |
| `no forecast skill is lost` | UNSUPPORTED | 8 | 8 | 0 | 0 | preserves the forecaster's baseline exactly at the reference price |
| `mean shrinkage weight[^.\n]{0,40}0\.95` | UNSUPPORTED | 10 | 10 | 0 | 0 | mean empirical-Bayes shrinkage weight 0.78 (panel-robust standard errors) |
| `\bguarantee[sd]?\b` | NEEDS QUALIFICATION | 34 | 22 | 12 | 0 | the optimizer never returns a price outside the feasible interval |
| `\bstate[- ]of[- ]the[- ]art\b|\bbest[- ]in[- ]class\b|\bworld[- ]class\b` | UNSUPPORTED | 0 | 0 | 0 | 0 | (delete; state the metric instead) |

Why each pattern is watched:

| Pattern | Rule |
| --- | --- |
| `\bcausal\b` | Only ever appears as a denial of causality or as the name of docs/CAUSAL_LIMITATIONS.md. Any assertive use would be UNSUPPORTED. |
| `production[ -]ready` | Only appears as an explicit denial. No orchestration, registry, live monitoring or deployment exists. |
| `\boptimal price\b` | 'Optimal' asserts a global optimum of the true profit function. What exists is the arg-max of a fitted, observational model over a constrained grid. |
| `\b(increased|improved|achieved|delivered|realised|realized) (profit|revenue|margin)` | Asserts a realised business outcome. No price recommended by this engine was ever charged. |
| `profit uplift` | Acceptable only with the 'model-internal estimated' qualifier and an adjacent statement that it is an offline simulation. |
| `\bvalidated\b` | Acceptable for things that were actually validated out of sample (forecast WAPE, price-response episodes). Never for the pricing policy itself. |
| `no forecast skill is lost` | The hybrid preserves the baseline only AT the reference price. Removed in Phase M; this pattern must find zero occurrences. |
| `mean shrinkage weight[^.\n]{0,40}0\.95` | Superseded. The 0.95 figure came from HC1 standard errors on a clustered panel; the corrected value is 0.78 (reports/14). |
| `\bguarantee[sd]?\b` | Only acceptable for mechanical guarantees of the code (non-negative predictions, bounds respected), never for business outcomes. |
| `\bstate[- ]of[- ]the[- ]art\b|\bbest[- ]in[- ]class\b|\bworld[- ]class\b` | Unfalsifiable marketing language with no benchmark behind it. |

## 3. Unsupported claims still present

**None.**

## 4. Claims needing qualification

| Location | Text | Preferred wording |
| --- | --- | --- |
| `docs/COPILOT_CARD.md:43` | 2. **Wording.** Claims that are causal, guaranteed, proven or "realised", or | the optimizer never returns a price outside the feasible interval |
| `docs/INTERVIEW_PITCH.md:63` | re-derived them with clustered errors and validated against statsmodels. | validated out of time on unseen price-change episodes |
| `README.md:81` | -> validated canonical panel (4,707,776 UPC x store x week rows) | validated out of time on unseen price-change episodes |
| `README.md:332` | make features        # modelling table with decision-time guarantees | the optimizer never returns a price outside the feasible interval |
| `reports/01_DATA_AUDIT.md:4` | **Status:** VALIDATED | validated out of time on unseen price-change episodes |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:131` | → validated canonical panel (4,707,776 UPC × store × week rows) | validated out of time on unseen price-change episodes |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:203` | materiality threshold. The optimizer is validated against closed-form optima | validated out of time on unseen price-change episodes |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:794` | This is implemented in `pricing_engine.data.cleaning.decode_week` and validated | validated out of time on unseen price-change episodes |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:1670` | weekly counts and guarantees non-negative predictions. | the optimizer never returns a price outside the feasible interval |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:1699` | split guarantees a large PSI. Reporting them anyway is deliberate: a drift | the optimizer never returns a price outside the feasible interval |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:2565` | **validated against statsmodels to machine precision** | validated out of time on unseen price-change episodes |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:3422` | | `loss` | **poisson** | the right likelihood for weekly counts; guarantees `Q̂ ≥ 0` by construction | | the optimizer never returns a price outside the feasible interval |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:4500` | monotonicity is guaranteed by construction for `ε < 0`; the diagnostics remain | the optimizer never returns a price outside the feasible interval |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:4501` | because the `ml` method is still selectable and because guarantees that are also | the optimizer never returns a price outside the feasible interval |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:4502` | checked are worth more than guarantees that are only asserted. | the optimizer never returns a price outside the feasible interval |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:4745` | The optimizer is validated against problems whose answers are known in closed | validated out of time on unseen price-change episodes |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:7512` | pre-existing. All are uses of *validated*, *guarantees* or *profit uplift* | model-internal estimated profit uplift |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:7512` | pre-existing. All are uses of *validated*, *guarantees* or *profit uplift* | validated out of time on unseen price-change episodes |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:7512` | pre-existing. All are uses of *validated*, *guarantees* or *profit uplift* | the optimizer never returns a price outside the feasible interval |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:7513` | either as pipeline-stage labels (`**Status:** VALIDATED`, | validated out of time on unseen price-change episodes |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:9717` | The pipeline runs from an official download through a validated canonical table, | validated out of time on unseen price-change episodes |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:10047` | 90.1% to **75.2%**. The estimators were validated against statsmodels to machine | validated out of time on unseen price-change episodes |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:10415` | seven covariance assumptions, validated against statsmodels → find two-way | validated out of time on unseen price-change episodes |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:10451` | > 1997. About 6.6 million raw rows became 4.7 million validated observations at a | validated out of time on unseen price-change episodes |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:10547` | > Dominick's Cereals panel, 4.7 million validated store-week observations. | validated out of time on unseen price-change episodes |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:10555` | > elasticity fitted on training weeks only. A grid optimizer validated against | validated out of time on unseen price-change episodes |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:10578` | > constrained optimizer validated against closed-form optima, and a risk gate | validated out of time on unseen price-change episodes |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:10605` | * *"Built a validated canonical panel of **4,707,776** UPC × store × week | validated out of time on unseen price-change episodes |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:10648` | validated against statsmodels."* | validated out of time on unseen price-change episodes |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:10757` | > * Built an end-to-end retail price-recommendation system on **4.7M validated | validated out of time on unseen price-change episodes |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:10764` | >   constrained grid optimizer validated against **closed-form optima**, and a | validated out of time on unseen price-change episodes |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:10789` | >   empirical-Bayes shrinkage, constrained optimizer validated against | validated out of time on unseen price-change episodes |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:10856` | | **econometrics** | log-log elasticity; **fixed-effects absorption** by within transformation; **HC1, one-way and two-way clustered covariance** validated agai | validated out of time on unseen price-change episodes |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:10886` | | **real, licensed, documented data** | 4,707,776 validated observations from official Kilts Center sources, with provenance hashes | | validated out of time on unseen price-change episodes |
| `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md:10889` | | **defensible econometrics** | fixed effects, panel-robust and two-way clustered inference validated against statsmodels, REML empirical-Bayes shrinkage, forma | validated out of time on unseen price-change episodes |
| `ROADMAP.md:22` | | **C. Feature availability + leakage audit** | Decision-time classification of every feature; shifted lag/rolling features | B | `docs/FEATURE_AVAILABILITY.md` | validated out of time on unseen price-change episodes |
| `ROADMAP.md:23` | | **D. Elasticity analysis** | Arc elasticity, log-log naive vs controlled, causal limitations | B, C | `reports/03_ELASTICITY_ANALYSIS.md`; `docs/CAUSAL_LIMITA | validated out of time on unseen price-change episodes |
| `ROADMAP.md:24` | | **E. Demand modelling** | Naive baselines, interpretable regression, gradient boosting; strict temporal split | C | `reports/04_MODEL_COMPARISON.md`; `artifac | validated out of time on unseen price-change episodes |
| `ROADMAP.md:25` | | **F. Price-response validation + simulation** | Demand curves at fixed context, monotonicity/extrapolation checks, vectorised counterfactual simulator | E | ` | validated out of time on unseen price-change episodes |
| `ROADMAP.md:110` | | Fix the intermittent `rec_id` parsing bug | `tests/test_review_workflow.py` (numeric-looking ids) | `VALIDATED` | | validated out of time on unseen price-change episodes |
| `ROADMAP.md:111` | | Close the known consistency failure honestly (verbatim-transcript marker) | `tests/test_metric_consistency.py` | `VALIDATED` | | validated out of time on unseen price-change episodes |
| `ROADMAP.md:112` | | Documentation drift, generation briefs moved to `docs/process/` | `KNOWN_LIMITATIONS.md` 17-18, `docs/process/README.md` | `VALIDATED` | | validated out of time on unseen price-change episodes |
| `ROADMAP.md:113` | | Synthetic generator + demo config + `make_demo.py` | `tests/test_synthetic.py`, `tests/test_demo_config.py`, `tests/test_demo_e2e.py` | `VALIDATED` | | validated out of time on unseen price-change episodes |
| `ROADMAP.md:114` | | Shared serving layer (`pricing_engine.serving`), data-mode labels | API and demo tests | `VALIDATED` | | validated out of time on unseen price-change episodes |
| `ROADMAP.md:115` | | CI runs the API and dashboard end to end on the demo; demo Docker image | `.github/workflows/ci.yml` (`demo` job), `Dockerfile.demo` built and run locally | ` | validated out of time on unseen price-change episodes |
| `ROADMAP.md:116` | | Value range in dollars | `reports/23_BUSINESS_VALUE.md` | `VALIDATED` | | validated out of time on unseen price-change episodes |
| `ROADMAP.md:117` | | Product roles (traffic drivers, margin builders) | `reports/24_PRODUCT_ROLES.md` | `VALIDATED` | | validated out of time on unseen price-change episodes |
| `ROADMAP.md:118` | | Executive summary, Business impact page, README front door | `docs/EXECUTIVE_SUMMARY.md`, dashboard page 1 | `VALIDATED` | | validated out of time on unseen price-change episodes |
| `ROADMAP.md:120` | | Ground-truth recovery study | `reports/25_GROUND_TRUTH_STUDY.md`, `tests/test_ground_truth_study.py` | `VALIDATED` (`LIMITED`: synthetic) | | validated out of time on unseen price-change episodes |
| `ROADMAP.md:121` | | Interview pack | `docs/INTERVIEW_PITCH.md` | `VALIDATED` | | validated out of time on unseen price-change episodes |
| `scripts/build_dataset.py:147` | **Status:** {"VALIDATED" if all_pass else "FAILED CHECKS - see below"} | validated out of time on unseen price-change episodes |
| `scripts/build_features.py:1` | """Phase C - build the modelling feature table with decision-time guarantees. | the optimizer never returns a price outside the feasible interval |
| `scripts/ground_truth_study.py:194` | a2.set_xlabel("portfolio gross-profit uplift (%)") | model-internal estimated profit uplift |
| `src/pricing_engine/copilot/guard.py:38` | "guarantee": re.compile(r"\bguarantee", re.I), | the optimizer never returns a price outside the feasible interval |
| `tests/test_copilot_agent.py:89` | LLMTurn(text="It is guaranteed to raise profit by 50%."), | the optimizer never returns a price outside the feasible interval |

## 5. What Phase M corrected

| Claim | Status before | Status now |
| --- | --- | --- |
| "no forecast skill is lost" | UNSUPPORTED - asserted for all prices | Replaced everywhere with "preserves the forecaster's baseline exactly at the reference price; counterfactuals away from it are governed by the elasticity model" |
| "optimal price" | UNSUPPORTED - asserts a true optimum | "candidate profit-maximising price under model assumptions" |
| "mean shrinkage weight 0.95" | UNSUPPORTED - artifact of HC1 SEs on a clustered panel | 0.78 with panel-robust standard errors (`reports/14`) |
| `recommended_price` on a REVIEW_REQUIRED row | Misleading - a proposal presented as a price | Split into `proposed_candidate_price` and `final_recommended_price` (`reports/19`) |
| "the ML model produces the recommendations" | Unsupported by the constraint attribution | Under the default policy 2.1% of final recommendations come from an interior model optimum (`reports/11`, `reports/12`, `reports/13`) |

## 6. The safe vocabulary

Use:

* model-internal estimated profit uplift
* candidate profit-maximising price under model assumptions
* offline simulated recommendation
* predictive price-response estimate
* training-estimated elasticity
* observational retail scanner data
* out-of-time predictive validation

Never use:

* optimized profit by X% / increased profit / achieved X% uplift
* optimal price / the best price
* causal price effect / proven price effect
* validated pricing uplift
* production ready
* no forecast skill is lost

## 7. Claims that are legitimate and must NOT be weakened

These are measured facts and stay exactly as they are:

* 4,707,776 processed `UPC x store x week` observations from Dominick's Finer
  Foods Cereals.
* Test WAPE 0.4565 on 749,040 held-out rows, against 0.7326 for the best naive
  baseline, on a chronological split.
* The reported test counts, lint status and runtimes.
* The elasticity point estimates and their (panel-robust) confidence intervals.
* Zero HIGH-risk contexts actionable under both default policy profiles.

---

*Generated by `scripts/audit_claims.py` in 2.2s.*
