# Report sources and evidence trail

**Companion to** [`AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md`](AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md)
**Generated** 2026-08-19
**Purpose** map every major claim, figure and table in the full report to the
repository artifact, script, test or command that produced it.

---

## 0. How to read this file

| column | meaning |
| --- | --- |
| **§** | the section of the full report |
| **claim / figure** | what is asserted |
| **value** | the number as reported |
| **evidence** | the file that holds it |
| **produced by** | the command that regenerates it |

Three categories of evidence are used and are never mixed:

| category | meaning |
| --- | --- |
| **A — artifact** | a JSON/CSV file written by a pipeline script |
| **L — live run** | a command executed on 2026-08-19 specifically for this report |
| **C — code** | a constant, formula or contract read directly from source |

---

## 1. Verification performed for this report (live runs, 2026-08-19)

| # | command | result | used in |
| ---: | --- | --- | --- |
| 1 | `python -m pytest` | **519 passed in 15.9 s**, 0 failed, 0 skipped | §1.10, §78.1, Appendix G, J.22 |
| 2 | `python -m pytest --collect-only -q` | per-file test counts (246/69/40/23/17/16/16/15/13/13/12/10/9/9/5/4/2 = 519) | §78.2, Appendix G |
| 3 | `ruff check .` | **All checks passed!** | §78.3, J.22 |
| 4 | `python scripts/run_demo.py` | CAPN CRUNCH JUMBO CR 3000006560 / store 86 / week 399: $3.35 → $3.66, ε = −2.940 (`shrunk_product`), uplift **+7.23%** | §2.3, §69, §105, J.23 |
| 5 | `python -m uvicorn api.main:app --port 8077` + `GET /health` | `{"status":"ok","model_loaded":true,"contexts_loaded":112763,"decision_weeks":[392..399]}` | §70.2, §71.1 |
| 6 | `GET /model/info` | version `ridge_loglog-20260818-132847`, `price_response_method: shrunk`, `pooled_elasticity: -2.028859937952365`, `n_features: 26` | §71.2 |
| 7 | `POST /recommend-price` `{"upc":3000006560,"store":86,...}` | the full 30-field response reproduced verbatim | §72 |
| 8 | headless Chrome → `http://127.0.0.1:8077/docs` | `artifacts/report_figures/api_openapi_docs.png` | §71, Figure 18 |
| 9 | `streamlit run dashboard/app.py --server.port 8511` + Selenium page-by-page capture | 9 full-page PNGs in `artifacts/report_figures/` | §74, §75, Figures 19–22 |
| 10 | `python scripts/make_report_figures.py` | 14 figures in `artifacts/report_figures/` | Figures 1–17, 23 |
| 11 | `python -c "pandas.read_csv('artifacts/models/elasticity_table.csv')"` | UPC 3000006560 row: raw −3.017796, SE 0.255658, weight 0.921394, final −2.940059 | §31.8, §105.3 |
| 12 | `git log` | *"your current branch 'master' does not have any commits yet"* | §81.4, limitation L15 |

---

## 2. Data claims

| § | claim | value | evidence | produced by |
| --- | --- | ---: | --- | --- |
| 1.4, 9.1 | raw movement rows | 6,602,582 | A `artifacts/metrics/raw_audit.json` → `movement.rows` | `scripts/build_dataset.py` |
| 9.1 | raw exact duplicates / grain duplicates | 0 / 0 | A `raw_audit.json` → `exact_duplicate_rows`, `duplicate_grain_rows` | `build_dataset.py` |
| 9.1 | raw UPCs / stores / weeks | 490 / 93 / 367 | A `raw_audit.json` | `build_dataset.py` |
| 9.1, 10.2 | `ok = 1` / `ok = 0` | 6,461,297 / 141,285 | A `raw_audit.json` → `ok_value_counts` | `build_dataset.py` |
| 11.1 | sale codes B/S/G/C/L | 254,261 / 91,259 / 11,075 / 3,418 / 1 | A `raw_audit.json` → `sale_code_counts` | `build_dataset.py` |
| 1.4, 9.4 | **canonical rows** | **4,707,776** | A `build_audit.json` → `processed_rows`; A `data_validation.json` → `rows` | `build_dataset.py`, `validate_data.py` |
| 9.2 | exclusion ledger (7 rules) | `ok_flag_zero` 141,285 (2.1398%); `non_positive_price` 1,753,521 (26.5581%); others 0 | A `build_audit.json` → `exclusions` | `build_dataset.py` |
| 9.3 | metadata join | 490 metadata UPCs, 489 movement UPCs, **0** rows without metadata, 0 duplicate metadata UPCs | A `build_audit.json` → `metadata_join` | `build_dataset.py` |
| 9.4 | canonical UPCs / stores / weeks / dates | 489 / 93 / 366 / 1989-09-14…1997-05-01 | A `build_audit.json` | `build_dataset.py` |
| 1.4, 13.1 | observed units / revenue / gross profit | 90,766,941 / $262,008,582.295 / $40,091,143.487 | A `build_audit.json`, `eda_summary.json` | `build_dataset.py`, `run_eda.py` |
| 9.5 | **15 / 15 validation checks pass** | 15/15 | A `data_validation.json` → `checks` | `validate_data.py` |
| 7.3 | bundle rows (`qty > 1`) | 4,946 (0.10506%) | A `eda_summary.json` → `bundles.qty_value_counts` | `run_eda.py` |
| 12.1 | Parquet SHA-256 / DataFrame fingerprint | `51f9148b…761a9` / `e9d26f2c…22d0` | A `dataset_fingerprint.json` | `build_dataset.py` |
| 5.1 | raw file sizes and SHA-256 | `upccer.csv` 25,932 B `affa589f…`; `wcer.zip` 42,402,094 B `2d4a59f8…`; `wcer.csv` 458,400,475 B `a204106f…`; manual 10,182,109 B `d8dc133b…` | A `data/raw/dominicks/cereals/SOURCE.json` (git-ignored, present locally) | `download_dominicks.py` |
| 6.1–6.4 | field semantics, week decoding | — | C `src/pricing_engine/data/schema.py`, `cleaning.py`; `docs/DATA_DICTIONARY.md` | — |

### 2.1 Zero-price audit (§10)

| claim | value | evidence |
| --- | ---: | --- |
| zero-price rows | 1,851,380 (28.0402% of raw) | A `zero_price_audit.json` → `zero_price_rows`, `zero_price_share` |
| …with sales | **677** | → `zero_price_and_move_gt_0` |
| rows with price > 0 and zero sales | **0** | → `nonzero_price_and_move_eq_0` |
| by `ok`: 1,753,521 / 97,859 | | → `zero_price_by_ok` |
| leading / trailing / interior | 15.733% / 57.688% / 26.208% | → `position_within_series` |
| series never priced | 6,868 | → `series_never_priced` |
| interior gaps with sales | 660 | → `interior_gap_with_sales` |
| UPCs above 80% zero | 112 of 490 | → `by_upc.n_upcs_above_80pct_zero` |
| selection probe: 3.053 vs 20.999 mean units | | → `selection_probe` |

Produced by `python scripts/audit_zero_price.py` → `reports/09_ZERO_PRICE_AUDIT.md`.

---

## 3. EDA claims (§13–§18)

All from A `artifacts/metrics/eda_summary.json`, produced by
`python scripts/run_eda.py` → `reports/02_PRICING_EDA.md`.

| § | claim | value | JSON key |
| --- | --- | ---: | --- |
| 13.1 | blended gross margin | 15.3015% | `scale.overall_gross_margin_pct` |
| 13.1 | mean / median effective unit price | $3.1157 / $3.15 | `scale.mean_effective_unit_price`, `median_…` |
| 13.1 | mean gross-margin rate | 17.419% | `scale.mean_gross_margin_rate` |
| 13.1 | recorded-promotion share | 7.3484% | `scale.share_recorded_promotion_rows` |
| 14 | UPCs for 80% of revenue | 121 | `concentration.upcs_for_80pct_revenue` |
| 14 | top-10 revenue / GP share | 14.147% / 12.677% | `concentration.top10_*` |
| 14 | UPCs with negative GP | 6 | `concentration.n_upcs_negative_gross_profit` |
| 15.1 | median distinct prices per UPC | 51 | `price_variation.upc_level.median_distinct_prices` |
| 15.2 | series / median obs / median distinct prices | 36,443 / 78 / 8 | `price_variation.upc_store_series` |
| 15.2 | median price CV / change rate | 0.0791 / 13.803% | same |
| 15.2 | **eligible series** | **19,707 (54.076%)** | `n_eligible`, `pct_eligible` |
| 16 | median / p90 cross-store spread | 13.627% / 27.452% | `store_price_dispersion` |
| 17 | median / p90 within-series AAC CV | 0.0884 / 0.1528 | `aac_stability` |
| 17.1 | corr(price, margin rate) | −0.0143 | `margin_mechanics.corr_price_vs_margin_rate` |
| 17.1 | price-change events / share cuts | 766,384 / 42.590% | `margin_mechanics` |
| 17.1 | mean AAC change on a cut | −9.159% | `margin_mechanics.mean_aac_change_pct_on_price_cut` |
| 18.3 | mean units / price with vs without promo | 54.46 / 16.49 · $2.544 / $3.161 | `promotion` |
| 18.5 | WoW price jumps > 50% | 10.067% (77,152) | `artefacts` |
| 18.5 | max / min unit price | $26.02 / $0.05 | `artefacts` |
| 18.1 | zero-sales rows | 0 | `artefacts.n_upc_store_weeks_with_zero_sales` |

---

## 4. Feature and leakage claims (§19–§23)

| § | claim | value | evidence |
| --- | --- | ---: | --- |
| 22.5 | feature rows / usable / dropped | 4,707,776 / 4,671,333 / 36,443 | A `feature_build.json` |
| 22.5 | features built | 29 | A `feature_build.json` → `n_features` |
| 22.6 | per-feature missing shares | 0.705%–3.017% | A `feature_build.json` → `missing_share_by_feature` |
| 35.1 | split weeks / rows / dates | 2–257 / 258–342 / 343–399; 3,206,437 / 715,856 / 749,040 | A `feature_build.json` → `split` |
| 22.5 | build time | 73.4 s | A `feature_build.json` → `build_seconds` |
| 20 | feature availability classification | 4 classes, per-column | C `docs/FEATURE_AVAILABILITY.md` |
| 23.1 | **12 leakage channels + enforcing tests** | 12 | C `docs/DATA_LEAKAGE_AUDIT.md` |
| 22.2 | shift-then-roll implementation | — | C `src/pricing_engine/features/build.py` lines building `roll_mean_move_*` |
| 22.7 | single `recompute_price_features` | — | C `features/build.py`; called from `counterfactual.py`, `optimizer.py`, `hybrid.py`, `api/routes/pricing.py`, `dashboard/app.py` |
| 23.3 | cost-leakage identity check | 4,671,333 rows, **0 mismatches** | A `cost_leakage_audit.json` → `identity_check` |
| 23.3 | poisoning check | 25 series, week 398, 5,537 rows; unchanged before, changed after | A `cost_leakage_audit.json` → `poisoning_check` |
| 23.3 | recommendation check | 19 series, **0 changed** | A `cost_leakage_audit.json` → `recommendation_check` |
| 23.3 | share equal to same-week AAC | 55.440% | A `cost_leakage_audit.json` |

Produced by `python scripts/build_features.py`,
`python scripts/audit_cost_leakage.py --n-series 25` →
`reports/10_COST_LEAKAGE_AUDIT.md`.

---

## 5. Elasticity claims

### 5.1 Analysis ladder (§27–§28) — A `artifacts/metrics/elasticity.json`

| specification | elasticity | SE | R² | JSON path |
| --- | ---: | ---: | ---: | --- |
| M1 naive pooled | −0.3480002 | 0.0032147 | 0.0126039 | `loglog[0]` |
| M2 + UPC FE | −2.2894092 | 0.0072119 | 0.1315432 | `loglog[1]` |
| M3 + UPC × store FE | −2.4187451 | 0.0711464 | 0.1966225 | `loglog[2]` |
| M4 + promo/season/trend | −1.9092974 | 0.0689925 | 0.2267701 | `loglog[3]` |
| promotion weeks | −2.6616966 | 0.0074200 | 0.2999311 | `promotion_split` |
| non-promotion weeks | −1.8300353 | 0.0077701 | 0.0987359 | `promotion_split` |
| arc: usable pairs / undefined / median | 716,858 / 84.104% / −2.8218608 | | | `arc` |
| per-UPC median / p10 / p90 | −2.3580407 / −4.0943513 / −0.2399279 | | | `heterogeneity` |
| share wrong-signed significant | 5.0704% | | | `heterogeneity` |
| per-store median / min / max | −0.3854942 / −0.9469538 / +0.2970882 | | | `heterogeneity` |

Produced by `python scripts/run_elasticity.py` → `reports/03_ELASTICITY_ANALYSIS.md`.

### 5.2 The pricing elasticity table (§28.5, §30, §31) — A `elasticity_estimation.json`

| claim | value | JSON key |
| --- | ---: | --- |
| **pooled controlled** | **−2.028859937952365** | `pooled_elasticity` |
| pooled SE (UPC-clustered) | 0.0827640 | `pooled_se` |
| **two-way clustered SE** | **0.1058457** | `pooled_se_two_way_cluster` |
| **two-way 95% CI** | **[−2.2363140, −1.8214059]** | `pooled_ci_low_two_way`, `..._high_two_way` |
| HC1 SE | 0.0116144 | `pooled_se_hc1` |
| UPC × store FE | −2.2217184 (SE 0.0839796) | `fe_elasticity`, `fe_se` |
| **τ² (REML)** | **0.7661348** | `tau2_between_product_variance` |
| τ | 0.8752913 | `shrinkage_tau` |
| τ² (moments) | 0.6730688 | `shrinkage_tau2_method_of_moments` |
| prior mean pooled / free | −2.0288599 / −1.9334085 | `shrinkage_prior_mean`, `…_estimated_freely` |
| **mean shrinkage weight** | **0.7797418** | `mean_shrinkage_weight` |
| products / usable | 372 / **239** | `n_products`, `n_products_usable` |
| rejected thin / wrong-sign / imprecise | 100 / 24 / 9 | `n_insufficient`, `n_wrong_sign`, `n_se_too_large` |
| clipped | 0 | `n_clipped` |
| median SE inflation vs HC1 | 3.7928013 | `median_per_upc_se_inflation_vs_hc1` |
| final median / p10 / p90 | −2.0288599 / −2.7897809 / −1.1690926 | `final_elasticity_*` |
| training window / rows | [2, 257] / 3,206,437 | `training_weeks_*`, `n_training_rows` |
| runtime | 35.0 s | `runtime_seconds` |

Produced by `python scripts/estimate_elasticity.py`.

**Per-product distribution (§30.8)** computed live from A
`artifacts/models/elasticity_table.csv`, rows where
`elasticity_source == "shrunk_product"` (n = 239): mean −1.9544, median
−1.8980, sd 0.7692, min −4.0578, max −0.3306, p25 −2.4645, p75 −1.3963.

**Worked example (§31.8, §105.3)** — row `upc == 3000006560`:
`n_obs` 20,125 · `n_distinct_prices` 215 · `elasticity_raw` −3.017796 ·
`std_error` 0.255658 · `std_error_hc1` 0.039782 · `shrinkage_weight` 0.921394 ·
`elasticity_shrunk` = `elasticity_final` −2.940059 · `elasticity_source`
`shrunk_product` · `clipped` false.

### 5.3 Inference audit (§29) — A `elasticity_inference.json`

Specification A (`pooled_specifications[0]`, coefficient −2.0288599, n = 1.2M):

| covariance | SE | ratio | JSON path |
| --- | ---: | ---: | --- |
| classical | 0.0065191 | 1.000 | `specifications.classical` |
| hc1 | 0.0116144 | 1.782 | `specifications["hc1 (heteroskedasticity-robust)"]` |
| UPC × store panel | 0.0153339 | 2.352 | `specifications["clustered by UPC x store panel"]` |
| store | 0.0304549 | 4.672 | `specifications["clustered by store"]` |
| week | 0.0812125 | 12.458 | `specifications["clustered by week"]` |
| UPC | 0.0836901 | 12.838 | `specifications["clustered by UPC"]` |
| **two-way (UPC, week)** | **0.1058457** | **16.236** | `specifications["two-way (UPC, week)"]` |

Specification C (full training sample, n = 3,206,437): coefficient
**−2.0442587**.

Per-UPC (`per_upc`, n = 262): median HC1 0.1071402, store 0.1445114, week
0.3832114; median robust ratio 3.3816878; p90 6.8282282; share significant
HC1 **0.9007634** → robust **0.7519084**.

Produced by `python scripts/audit_elasticity_inference.py` →
`reports/15_ELASTICITY_INFERENCE_AUDIT.md`.

### 5.4 Shrinkage audit (§31.7) — A `shrinkage_audit.json`

Seven variants under `variants`; the two quoted in the report:

| variant key | τ² | median SE | mean weight | median shrunk |
| --- | ---: | ---: | ---: | ---: |
| `Phase L: moment tau^2, sample-mean prior, HC1 se, filtered` | 0.9058654 | 0.1014256 | **0.9551688** | −1.8415401 |
| `Phase M (shipped): REML tau^2, pooled prior, ROBUST se, filtered` | 0.7661348 | 0.3903591 | **0.7797418** | −1.8979711 |

`weight_bands`: `<0.25` 0 · `0.25-0.5` 25 · `0.5-0.75` 48 · `0.75-0.9` 96 ·
`0.9-0.99` 70 · `>0.99` 0. `weights_monotone_in_se`: **true**.

Produced by `python scripts/audit_shrinkage.py` → `reports/14_SHRINKAGE_AUDIT.md`.

### 5.5 Eligibility funnel (§30.4) — A `elasticity_funnel.json`

`funnel[]` stages: 489 → 476 → 372 → 372 → 330 → 272 → 272 → 248 → **239**.
`rejections`: insufficient 100, wrong sign 24, SE too large 9.
`n_decision_contexts` 13,964; `elasticity_source_counts` `shrunk_product`
8,962 / `pooled_fallback` 5,002; `share_shrunk_product` 0.6417932.

Produced by `python scripts/audit_eligibility_funnel.py` →
`reports/18_ELASTICITY_ELIGIBILITY_FUNNEL.md`.

### 5.6 Stability (§32) — A `elasticity_stability.json`

`windows.W1..W5` supply every pooled / SE / FE / τ² / mean-weight / usable
figure. `pairwise_pooled_z_tests` supplies z = +1.3605 / +1.3168 / −0.3345;
`max_abs_z_between_disjoint_windows` = 1.3605038856698592. `pairwise` supplies
rank correlations −0.0536 / +0.3498 / +0.1867 and
`median_pairwise_rank_correlation` = 0.1867217; sign stability 1.0; 34 common
products.

Produced by `python scripts/audit_elasticity_stability.py` →
`reports/17_ELASTICITY_STABILITY.md`.

---

## 6. Demand model claims (§34–§41)

A `artifacts/metrics/model_metrics.json` (all six models),
A `artifacts/metrics/evaluation.json` (the selected model re-scored),
A `artifacts/models/demand_model_metadata.json` (provenance).

| § | claim | value | source |
| --- | --- | ---: | --- |
| 36.1 | four baselines, valid + test WAPE | see §36.1 table | `model_metrics.results["M0a..M0d"]` |
| 39.1 | M1 valid / test WAPE | 0.4134593 / 0.4564943 | `results["M1 ridge log-log"]` |
| 41.1 | M1 test MAE / RMSE / bias / sMAPE | 8.5025670 / 72.0642532 / −3.8830525 / 0.4000264 | `evaluation.test_metrics` |
| 38.2 | M2 valid WAPE / MAE / RMSE / bias | 0.4226396 / 8.0501351 / 38.3781440 / +0.3627634 | `results["M2 HGB poisson"]` |
| 37.9, 38.1 | fit times | 31.0 s / 181.9 s | `fit_seconds` |
| 37.7 | raw `log_price` coefficient | −0.0134952 | `results["M1…"].raw_log_price_coefficient` |
| 37.8 | M1 implied elasticity (valid / test) | median −3.3223092 / −3.1884301 | `model_metrics`, `evaluation.implied_elasticity` |
| 38.3 | M2 implied elasticity | median −4.5121858, share negative 0.93022 | `results["M2…"].implied_elasticity` |
| 39.3 | reload WAPE difference | **0.0** | `evaluation.reload_wape_difference` |
| 41.3 | promotion segments | see §41.3 table | `results["M1…"].segments.by_promo` |
| 41.5 | price-variation segments | see §41.5 table | `…segments.by_price_variation` |
| 41.6 | worst 10 stores | see §41.6 table | `…segments.worst_10_stores_by_wape` |
| 81.1 | model version / fingerprint / environment | `ridge_loglog-20260818-132847`, `e9d26f2c…`, Python 3.13.0 | `demand_model_metadata.json` |
| C.1 | all Ridge pipeline parameters | 37 entries | `demand_model_metadata.params` |
| C.2 | all HGB parameters | 18 entries | `model_metrics.results["M2…"].params` |

Produced by `python scripts/train.py`, `python scripts/evaluate.py` →
`reports/04_MODEL_COMPARISON.md`.

**Derived in this report (arithmetic, not an artifact):**
relative WAPE improvement `(0.7325558 − 0.4564943)/0.7325558 = 0.3769` → 37.7%
(§36.3, §41.2); bias decomposition `57,204 × (−37.238)/749,040 = −2.844`, i.e.
73% of −3.883 (§41.4).

---

## 7. Price-response claims

### 7.1 Native ML validation (§42.1) — A `price_response.json` → `summary`

`n_contexts` 300 · `share_monotone_decreasing` 1.0 ·
`share_with_any_increasing_segment` 0.0 · `share_flat_response` 0.0 ·
`share_negative_predicted_units` 0.0 ·
**`median_local_elasticity` −3.1019869** · `p10` −3.4820366 · `p90` −2.6671501 ·
`median_relative_demand_span` 1.8317502 ·
`share_profit_optimum_below_current` 0.2666667 ·
`share_revenue_optimum_below_current` **1.0** ·
`share_profit_optimum_at_grid_edge` 0.0766667.

Produced by `python scripts/price_response.py --n-contexts 300` →
`reports/05_PRICE_RESPONSE_VALIDATION.md`.

### 7.2 Method comparison (§44) — A `price_response_comparison.json`

`per_method.{ml,pooled,shrunk}` supplies every row of the §44.1 table.
`pairwise_disagreement[]` supplies the §44.3 table.
`elasticity_used` supplies pooled −2.0288599, shrunk median −2.0288599, p10
−2.9734217, p90 −1.2741749, `share_product_specific` 0.7366667.
`sensitivity[]` (20 rows) supplies the §44.4 worked example (HONEY NUT CHEERIOS
1600068290, store 116).
`sensitivity_price_spread_pct_median_constrained` **0.0**;
`sensitivity_price_spread_pct_median_wide` **21.5053763**.

Produced by `python scripts/compare_price_response.py --n-contexts 300` →
`reports/08_PRICE_RESPONSE_COMPARISON.md`.

### 7.3 Out-of-time validation (§45) — A `out_of_time_price_response.json`

`n_episodes` 154,899 · `n_dropped_zero_baseline` 3 ·
`min_abs_price_change` 0.05 · `max_abs_price_change` 0.60 ·
`share_with_recorded_promotion` 0.7333101 ·
`median_observed_implied_elasticity` −2.4523705 ·
`…_no_promo` −1.8622752 · `training_weeks` [2, 257] ·
`evaluation_weeks` [258, 399].

`results["all episodes"]`, `results["no recorded promotion"]`,
`results["recorded promotion in either week"]` supply every WAPE / MAE / bias /
sign-accuracy / median-log-error / rank-correlation figure in §45.3–45.5.

Produced by `python scripts/audit_out_of_time_response.py` →
`reports/16_OUT_OF_TIME_PRICE_RESPONSE.md`.

---

## 8. Optimization, constraint and policy claims

### 8.1 Constraint attribution (§54) — A `constraint_attribution.json`

| claim | value | JSON key |
| --- | ---: | --- |
| contexts | 13,964 | `n_contexts` |
| **final determinants** | guardrail corner 7,967 · screened out 3,528 · risk gate 1,353 · materiality 716 · **learned signal 400** | `final_determinant_counts` |
| **share learned signal** | **0.0286451** | `share_determined_by_learned_signal` |
| first binding | MAX_PRICE_CHANGE 6,222 · HISTORICAL_SUPPORT 3,373 · NONE 1,924 · EXTRAPOLATION 1,877 · MATERIALITY 565 · RISK_GATE 3 | `first_binding_constraint_counts` |
| unconstrained optimum inside bounds | 968 (6.932%) | `n_/share_unconstrained_optimum_inside_bounds` |
| proposal / final matches it | 638 (4.569%) / 635 (4.547%) | `n_proposal_matches…`, `n_model_optimum_survives_policy` |
| median / p90 rounding distance | $0.0250 / $0.0427 | `median_/p90_rounding_distance_dollars` |
| median unconstrained change | +48.997% (p10 −4.682%, p90 +174.613%) | `median_/p10_/p90_unconstrained_price_change_pct` |
| median final change | +3.746% | `median_final_price_change_pct` |
| unconstrained optimum above current | 86.680% | `share_unconstrained_optimum_above_current` |
| per-constraint present/removes/binds/changes | full §54.6 table | `per_constraint` |

Produced by `python scripts/audit_constraints.py` →
`reports/11_CONSTRAINT_ATTRIBUTION.md`. The audit replica is pinned by
`tests/test_attribution.py` (69 tests over 60 parameter combinations plus 400
real contexts).

### 8.2 Guardrail ablation (§55) — A `guardrail_ablation.json`

`elasticity_scenarios` [−1.5, −1.9, −2.4, −3.1]; `layers["A research…"]` …
`layers["E conservative"]` supply every column of the §55.3 table, including
`median_price_variation_across_scenarios_pct` 0.7977208 → 0.0659631 → 0.0 →
0.0 → 0.0 and `share_elasticity_scenario_changes_price` 0.9999284 → 0.6102120
→ 0.3062160 → 0.2134775 → 0.2016614.

Produced by `python scripts/audit_guardrails.py` →
`reports/13_GUARDRAIL_ABLATION.md`.

### 8.3 Model-value ablation (§56) — A `model_value_ablation.json`

`rules["R0..R4"]` supply the §56.4 table.
**R4** `share_final_price_differs_more_than_one_grid_step` = 0.1745202 ⟹
within one grid step **82.548%**; `share_same_decision_state` 0.8995990;
`mean_abs_price_difference` $0.0604803; `median_abs_price_difference_pct`
0.0061489. Model `median_price_change_pct_when_actionable` 0.0836120.

Produced by `python scripts/audit_model_value.py` →
`reports/12_MODEL_VALUE_ABLATION.md`.

### 8.4 Decision states (§58, §59) — A `decision_state_audit.json`

`profiles.{standard,conservative,aggressive}` supply the §58.6 table;
`invariant_violations` supplies 0/0 for the default profiles and 4,717 for
`aggressive`; `high_risk_decision_split` supplies KEEP_CURRENT 3,407 /
REVIEW_REQUIRED 1,353; `high_risk_keep_current_causes` supplies the §59.2
breakdown.

Produced by `python scripts/audit_decision_states.py` →
`reports/19_DECISION_STATE_AUDIT.md`.

### 8.5 Batch recommendations (§106.1) — A `recommendations.json`

Every figure in §106.1, including
`portfolio_model_internal_estimated_profit_uplift_pct` **0.0830248**,
`portfolio_expected_gross_profit_current` 37,580.819,
`…_if_applied` 40,700.960, `high_risk_actionable` **0**, and the complete
`reason_code_counts` map used in §60.2 and Appendix F.

Produced by `python scripts/optimize.py --batch 3000 --profile standard` →
`reports/07_RECOMMENDATION_SUMMARY.md`.

### 8.6 Constraints and analytical validation (§52, §53)

| claim | evidence |
| --- | --- |
| the six guardrails and their intersection | C `src/pricing_engine/optimization/constraints.py` → `build_bounds` |
| `p* = (a+bc)/(2b)`, `p* = a/(2b)` | C `optimization/objective.py` → `analytical_linear_optimum`, `analytical_linear_revenue_optimum` |
| the fixture results | `tests/test_optimizer.py`; summarised in `reports/VALIDATION_SUMMARY.md` § "Analytical optimizer verification" |
| the `build_bounds` binding-set correction | C `constraints.py` docstring; `DECISIONS.md` #48 |
| `Q̂(p₀)` cancels from the arg-max | C `models/hybrid.py`; verified in `tests/test_attribution.py` (10× scaling leaves prices unchanged) |

---

## 9. Backtest and monitoring claims

### 9.1 Backtest (§61–§63) — A `backtest.json`

`accuracy_by_week[]` supplies the §62.1 table; `accuracy_anchor` supplies mean
0.4777031, min 0.3638491, max 0.5602457, mean bias −4.6610546;
`policy_totals[]` supplies the §63.2 table including
`model_internal_estimated_gp_vs_historical_pct` 13.6825735
(ElasticityBaselinePolicy) and 10.4512853 (MLPricingPolicy).

Produced by `python scripts/backtest.py --weeks 10 --contexts-per-week 400` →
`reports/06_BACKTEST.md`.

### 9.2 Monitoring (§18.2, §41.8, §82) — A `monitoring.json`

`schema.passed` true, 749,040 rows · `feature_drift[]` (22 features with PSI,
KS, band and means) · `prediction_drift` PSI 0.0555561, KS 0.09502, mean
16.6912200 → 15.0009754 · `performance_by_quarter[]` supplies the §41.8 table
(1996Q2 WAPE 0.4090958 → 1997Q2 0.5277826).

Produced by `python scripts/monitor.py` → `reports/MONITORING_DESIGN.md`.

---

## 10. Claim-audit and quality-gate evidence

| claim | before this report | **with this report included** | evidence |
| --- | ---: | ---: | --- |
| files scanned / occurrences | 43 / 162 | **49 / 363** | A `claim_audit.json` |
| **UNSUPPORTED** | **0** | **0** | `counts.UNSUPPORTED` |
| NEEDS QUALIFICATION / SAFE | 11 / 151 | 38 / 325 | `counts.*` |
| qualification hits by file | — | this report 27 · `ROADMAP.md` 6 · `README.md` 2 · `01_DATA_AUDIT.md` 1 · `build_dataset.py` 1 · `build_features.py` 1 | `needs_qualification[]` |

Produced by `python scripts/audit_claims.py --strict` → `reports/20_CLAIM_AUDIT.md`.

**The scanner was run against this report itself.** On the first pass it
flagged one UNSUPPORTED occurrence — the phrase *"realised margin"* in §20.3,
used to mean the accounting margin a week actually produced, which matched the
watched pattern for a claimed business outcome. The sentence was reworded and
the scan now returns **0 UNSUPPORTED** with the report included. Exit code 0
under `--strict`.

| gate | value | evidence |
| --- | --- | --- |
| pytest | 519 passed / 0 failed / 0 skipped | L live run 2026-08-19 (v1.0 freeze) |
| per-file test counts | 246/69/40/23/17/16/16/15/13/12/10/9/5/2 | L `pytest --collect-only -q` |
| ruff | All checks passed | L live run 2026-08-19 |
| dashboard smoke | 9/9 pages | `scripts/smoke_dashboard.py`; L re-verified by Selenium capture |
| Docker | **NOT TESTED** | `reports/VALIDATION_SUMMARY.md` row 36; daemon unavailable |
| CI | implemented, never executed | C `.github/workflows/ci.yml`; L `git log` (no commits) |

---

## 11. Figures — provenance of every image in the report

| figure | file | source data | generated by |
| ---: | --- | --- | --- |
| 1 | `fig_01_temporal_split.png` | `feature_build.json` → `split` | `scripts/make_report_figures.py::fig_temporal_split` |
| 2, 23 | `fig_08_decision_context_curves.png` | **live pipeline call** — model + elasticity table + `simulate_price_grid` on UPC 3000006560 / store 86 | `…::fig_decision_context` |
| 3 | `artifacts/figures/eda_price_cv.png` | `price_variation_upc_store.csv` | `scripts/run_eda.py` |
| 4 | `fig_02_elasticity_ladder.png` | `elasticity.json`, `elasticity_estimation.json`, `price_response.json` | `…::fig_elasticity_ladder` |
| 5 | `fig_03_inference_standard_errors.png` | `elasticity_inference.json` → `pooled_specifications[0]` | `…::fig_inference` |
| 6 | `fig_05_elasticity_funnel.png` | `elasticity_funnel.json` → `funnel` | `…::fig_funnel` |
| 7 | `fig_04_shrinkage.png` | `shrinkage_audit.json` → `variants`, `weight_bands` | `…::fig_shrinkage` |
| 8 | `fig_12_elasticity_stability.png` | `elasticity_stability.json` → `windows`, `pairwise` | `…::fig_stability` |
| 9 | `fig_10_model_comparison.png` | `model_metrics.json` → `results` | `…::fig_models` |
| 10 | `fig_11_backtest_wape.png` | `backtest.json` → `accuracy_by_week` | `…::fig_backtest` |
| 11 | `artifacts/figures/compare_change_distribution.png` | comparison run | `scripts/compare_price_response.py` |
| 12 | `fig_09_out_of_time_price_response.png` | `out_of_time_price_response.json` → `results` | `…::fig_out_of_time` |
| 13 | `fig_06_constraint_attribution.png` | `constraint_attribution.json` → `final_determinant_counts`, `first_binding_constraint_counts` | `…::fig_constraints` |
| 14 | `fig_07_guardrail_ablation.png` | `guardrail_ablation.json` → `layers` | `…::fig_guardrails` |
| 15 | `fig_14_model_value_ablation.png` | `model_value_ablation.json` → `rules` | `…::fig_model_value` |
| 16 | `fig_13_decision_states.png` | `decision_state_audit.json` → `profiles` | `…::fig_decision_states` |
| 17 | `artifacts/figures/backtest_policy_profit.png` | backtest run | `scripts/backtest.py` |
| 18 | `api_openapi_docs.png` | **live FastAPI instance** | headless Chrome → `/docs` |
| 19 | `dashboard_1_executive_overview.png` | **live Streamlit instance** | headless Chrome + Selenium |
| 20 | `dashboard_5_price_simulator.png` | live Streamlit | same |
| 21 | `dashboard_6_recommendation_engine.png` | live Streamlit | same |
| 22 | `dashboard_8_data_quality.png` | live Streamlit | same |

**Screenshots not embedded but captured and stored:**
`dashboard_2_product_store_explorer.png`,
`dashboard_3_pricing_and_demand.png`,
`dashboard_4_elasticity_analysis.png`,
`dashboard_7_model_performance.png`,
`dashboard_9_methodology_limitations.png`.

**No image in this report is mocked, drawn by hand, or edited.** All 24 files
in `artifacts/report_figures/` were produced on 2026-08-19 by
`scripts/make_report_figures.py` or by driving the live applications with
headless Chrome.

---

## 12. Discrepancies between repository prose and current artifacts

Found while writing this report. In every case the **artifact** value is used
and the prose value is recorded here.

| # | location | prose says | artifact says | report uses | impact |
| ---: | --- | --- | --- | --- | --- |
| 1 | `README.md` §3 (demo block) | elasticity **−3.016** labelled `shrunk_product` | applied **−2.9400592**; −3.017796 is the **raw** per-UPC estimate | −2.9401 | none on conclusions; the README quotes the pre-shrinkage value with a post-shrinkage label |
| 2 | `README.md` §3 | model-internal uplift **+6.51%** | **+7.2274%** (live `run_demo.py` and `/recommend-price`, 2026-08-19) | +7.23% | none; follows mechanically from #1 |
| 3 | `README.md` §10, `KNOWN_LIMITATIONS.md` #28, `VALIDATION_SUMMARY.md` row 15 | ml vs shrunk decision agreement **87.3%** | `price_response_comparison.json` → 0.8666667 = **86.67%** | 86.67% | negligible; **corrected at source in v1.0** |
| 4 | same | pooled vs shrunk decision agreement **89.0%** | 0.9166667 = **91.67%** | 91.67% | negligible; the artifact is *more* favourable; **corrected at source in v1.0** |
| 5 | `KNOWN_LIMITATIONS.md` #37, `DECISIONS.md` #32 | Phase L τ² **1.101 / 1.10** | `shrinkage_audit.json` Phase L **reconstruction** gives 0.9058654 | both, with the reason | the reconstruction filters on the *current* panel-robust SE threshold, so it is not byte-identical to the original Phase L run — explained in §31.7 |
| 6 | `README.md` §8 (`make test lint` comment) | "121 tests" | the live collected count | generated | a stale inline comment; **the README no longer states a test count outside the generated block** |

**Note on #3 vs the ml-vs-pooled figure:** the ml-vs-pooled agreement (79.7%)
matches the artifact exactly, which suggests the other two were transcribed
from an earlier `compare_price_response.py` run and not refreshed.

**Recommended fix:** a `make regenerate-readme` step that rebuilds the README's
numeric blocks from `artifacts/metrics/*.json`, closing this class of drift
permanently.

---

## 13. Claims in the report that are *derived* rather than read from an artifact

These are arithmetic performed for this report. Each is shown with its
computation so it can be checked.

| § | derived claim | computation |
| --- | --- | --- |
| 1.2 | "a 1% *relative* increase in the gross-profit total ≈ $0.4 M" | `0.01 × 40,091,143 = 400,911` |
| 1.2 | "a +1 *percentage point* gross-margin rate on observed revenue ≈ $2.6 M" | `0.01 × 262,008,582 = 2,620,086` |
| 36.3, 41.2 | 37.7% relative WAPE improvement | `(0.7325558 − 0.4564943) / 0.7325558 = 0.37690` |
| 41.4 | 73% of the overall bias comes from promotion rows | `57,204 × (−37.238) / 749,040 = −2.844`; `2.844 / 3.883 = 0.732` |
| 42.2 | "expects to lose 26.5% where the truth is 17.4%" | `1 − 1.10^{−3.10} = 0.2649`; `1 − 1.10^{−2.0} = 0.1736` |
| 2.3, 105.4 | unconstrained CE optimum $3.875 | `2.5570550 × (−2.9400592)/(−1.9400592) = 3.8748` |
| 44.2 | ml optimum multiplier 1.476 vs pooled 1.972 | `3.10/2.10 = 1.4762`; `2.029/1.029 = 1.9718` |
| 45.6 | 21.7% relative WAPE improvement over the null (non-promotion) | `(0.7151635 − 0.5595707)/0.7151635 = 0.2176` |
| 54.5 | "the model wants ≈2× cost" | `p* = c·2.029/1.029 = 1.9718·c` versus a historical blended margin of 15.30% ⟹ `p ≈ 1.181·c` |
| 56.5 | R4 within one grid step 82.55% | `1 − 0.1745202` |
| 56.4 | R0..R3 within one grid step | `1 − share_final_price_differs_more_than_one_grid_step` |
| 18.3 | "a naive promotion elasticity would be ≈ −5.5" | `ln(54.46/16.49) / ln(2.544/3.161) = 1.195 / (−0.217) = −5.51` |
| 94.5 | "+10% for four consecutive weeks compounds to +46%" | `1.10⁴ = 1.4641` |
| 105.6 | units change −22.9%, revenue −15.78% | `19.7077/25.5647 − 1`; `72.1302/85.6418 − 1` |
| 31.8 | shrinkage arithmetic | `0.2557² = 0.06538`; `0.7661/(0.7661+0.06538) = 0.9214`; `0.9214×(−3.0178) + 0.0786×(−2.0289) = −2.9401` |
| 104.1 | "up to 45,477 weekly price decisions" | `489 UPCs × 93 stores` |
| 9.2 | "1,851,380 zero-price rows but only 1,753,521 removed by rule 4" | `1,851,380 − 97,859 (already removed as ok = 0)` |

---

## 14. Evidence that could not be obtained

Recorded rather than worked around.

| item | why | consequence for the report |
| --- | --- | --- |
| **Docker image build** | the Docker daemon is not running in this environment (`npipe:////./pipe/dockerDesktopLinuxEngine` unreachable) | §83.2 reports **NOT TESTED**, not PASS |
| **CI workflow execution** | no GitHub remote, and the repository has **no commits** | §83.4 reports implemented-but-never-run |
| **`git_commit` in model metadata** | no commits exist, so the field holds the literal string `"HEAD"` | §81.4, limitation L15 |
| **Realised outcomes at recommended prices** | those prices were never charged — this is a property of observational data, not of this environment | §64, §65.2; the entire reason uplift figures are model-internal |
| **A causal elasticity** | no randomisation, no valid instrument in the Cereals extract | §33, §103 |
| **Full-panel (non-subsampled) elasticity for all specifications** | tractability; one full-sample check was run (−2.0443 vs −2.0289) | §26.4, limitation E6 |
| **Screenshots of a hosted deployment** | the app runs locally only | §75 states the capture method explicitly |

---

## 14b. The Word edition

| deliverable | file |
| --- | --- |
| Markdown (source of truth) | `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md` |
| **Word, 247 pages** | `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.docx` |
| PDF (verification export) | `reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.pdf` |

Built by three scripts added for this report, all lint-clean:

| script | job |
| --- | --- |
| `scripts/make_report_figures.py` | the 14 analysis figures, from `artifacts/metrics/*.json` and one live pipeline call |
| `scripts/render_mermaid.py` | the 10 Mermaid diagrams to PNG (headless Chrome + a locally cached `mermaid.min.js`) |
| `scripts/_latex_unicode.py` | LaTeX to Unicode for the 100 display equations, with a 10-fixture self-test |
| `scripts/build_report_docx.py` | assembles the `.docx` from the Markdown |

The table of contents, page numbering and pagination were finalised by driving
Microsoft Word 16.0 through COM (`TablesOfContents.Update`, `Fields.Update`,
`Repaginate`, `Save`, `ExportAsFixedFormat`), so the contents are **already
built** when the file is opened rather than needing F9.

**The Word edition contains no content that is not in the Markdown**, and both
are generated from the same source, so a number cannot differ between them.

---

## 15. Regenerating everything in this report

```bash
# the pipeline (requires the licensed data — see docs/DATA_SOURCE.md)
make all

# this report's figures, diagrams and the Word edition
python scripts/make_report_figures.py
python scripts/render_mermaid.py
python scripts/build_report_docx.py

# the live verification
python -m pytest && python -m ruff check .
python scripts/run_demo.py
python -m uvicorn api.main:app --port 8000        # then GET /health, /model/info, POST /recommend-price
python -m streamlit run dashboard/app.py          # then capture the 9 pages
```

---

*Every number in the full report is traceable through this file to an artifact,
a live command or a source constant. Where an artifact and the repository's
prose disagree, §12 above records the discrepancy and the report uses the
artifact.*
