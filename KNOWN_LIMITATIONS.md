# Known limitations

Maintained throughout the build. Nothing here is a formality: each item changes
how the output should be read.

## Scientific

1. **No causal identification.** Prices were set by the retailer, not
   randomised. The engine estimates `P(Q | price, X)`, not `P(Q | do(price))`.
   The naive-to-fixed-effects swing (-0.35 -> -2.42) shows how much the raw
   association is contaminated.
2. **The native ML price response is steeper than the econometric benchmark.**
   Implied elasticity -3.10 (median across 300 contexts) vs -1.91/-2.42 from
   controlled regressions. **Phase L response:** the default pricing method is
   no longer the ML response but an elasticity hybrid
   (`Q(p) = Q_hat(p0) * (p/p0) ** epsilon`; pooled -2.029, or a shrunk per-UPC
   estimate, fitted on training weeks only). The native response is kept as a
   benchmark in `reports/08_PRICE_RESPONSE_COMPARISON.md`. Residual limitation:
   the elasticity itself is observational and partly promotion-contaminated.
3. **Promotion coding is incomplete.** A missing `sale` code does not prove no
   promotion. Two undocumented codes (`G`, 11,075 rows; `L`, 1 row) appear in
   the Cereals file. Any "price effect" partly absorbs display and feature
   activity.
4. **Counterfactual evaluation is circular.** In the backtest the same demand
   model both chooses and scores prices. Only the demand-accuracy section is
   verifiable against outcomes.
5. **AAC is not economic cost.** The implied unit cost comes from an accounting
   margin on Average Acquisition Cost and can differ from replacement cost.
   Within-series AAC is itself volatile (median CV 0.088).
6. **No cross-price effects.** Cereal buyers substitute heavily between brands;
   the engine prices each UPC x store independently, so category-level
   cannibalisation is unmodelled - arguably the largest missing mechanism.
7. **No customer dimension.** Store-week scanner data cannot support traffic
   normalisation, basket effects or any customer-level reasoning.
8. **Historical data (1989-1997).** Price levels, category dynamics and
   competition are not today's market.
9. **Stock-outs are invisible.** Unavailability and demand collapse look
   identical in scanner data.

## Modelling

10. **Point predictions only.** No predictive intervals, so the optimizer
    maximises an expectation without weighing dispersion.
11. **Weekly WAPE ~0.46 on the test window** (0.36-0.56 across backtest weeks).
    Any counterfactual difference smaller than that error deserves scepticism -
    hence the materiality threshold.
12. **Systematic under-forecast** (test bias -3.88 units), concentrated on
    high-volume promotion weeks.
13. **A prediction cap is applied** (5x maximum training demand) to stop the
    log-target model exploding on extreme inputs. Recorded in the artifact.
14. **Promotion flag assumed known at decision time.** Realistic but an
    assumption; see the leakage audit.
15. **Only 54.1% of series are eligible** for price optimization. The rest lack
    history or price variation and always receive keep-current.

## Engineering / scope

16. **Not production ready.** No orchestration, no model registry, no live
    monitoring store, no serving SLOs, no CI on the real data (by design - the
    licence forbids redistributing it).
17. **Docker image not built in this environment** (no Docker daemon
    available). The Dockerfile and compose file are written and included in CI,
    but the build was not executed locally - see `reports/VALIDATION_SUMMARY.md`.
18. **Batch optimization is per-context.** 3,000 contexts take ~160 s.
    `simulation.simulate_many` provides the fully vectorised path for scaling to
    all 36,443 series, but the batch script does not use it yet.
19. **The dashboard loads the full feature panel** (~4.7 M rows) into memory
    with caching. Fine on a workstation, not a deployment pattern.
20. **Risk level is heuristic**, not calibrated confidence, and is labelled as
    such everywhere.
21. **Policy profiles are DEMO settings**, chosen for illustration, not
    industry benchmarks.
22. **Elasticity regressions use a 1.2 M-row subsample** (seeded) for
    tractability; full-panel estimates were not run.
23. **Notebooks are thin wrappers** over the scripts; the scripts are the
    source of truth for every number.

## Data-quality specifics found in this build

24. 10.07% of week-over-week price-change events exceed +/-50% - real promotion
    behaviour mixed with possible recording artefacts; kept, flagged, and
    bounded downstream by the extrapolation guardrail.
25. 6 UPCs have negative total observed gross profit over the panel.
26. Maximum observed unit price is $26.02 (a non-cereal item, "TONY THE TIGER
    T-SHIRT", present in the Cereals commodity file).

## Phase L (pricing-science hardening) - what changed and what remains

27. **Uplift figures are model-INTERNAL estimates.** The same fitted price
    response both proposes and scores candidate prices. Every report, API field
    and dashboard label now says so, and the metric is named
    model_internal_estimated_profit_uplift_pct.
28. **Recommendations depend on the price-response assumption.** On 300 matched
    contexts, ml vs pooled disagree by $0.087 on average (2.7% of price) and
    agree on the decision state only 79.7% of the time (ml vs shrunk: 86.7%).
    Any single uplift number is meaningless without naming the method.
29. **The elasticity assumption moves the answer.** Under wide what-if
    guardrails the candidate profit-maximising price spans a median of 21.5% across the
    -1.5 / -1.9 / -2.4 / -3.1 scenarios. Under production guardrails the
    constraints usually bind first (spread 0.0%), which is its own finding: the
    guardrails, not the elasticity, often decide the price.
30. **133 of 372 products have no usable own elasticity** (100 too thin, 24
    wrong-signed, 9 too imprecise under panel-robust standard errors) and fall
    back to the pooled estimate - 35.8% of decision contexts. Those
    recommendations are flagged POOLED_ELASTICITY_FALLBACK and can never be
    rated LOW risk. Full funnel: reports/18_ELASTICITY_ELIGIBILITY_FUNNEL.md.
31. **The zero-price exclusion is now evidenced, not assumed.** 1,851,380 raw
    rows carry price = 0; only 677 of them recorded any sales, and 73.4% sit in
    leading or trailing runs (item not yet carried, or delisted). The rule is
    unchanged; the resulting tilt toward continuously stocked, higher-volume
    series is documented in reports/09_ZERO_PRICE_AUDIT.md.
32. **The official manual is silent on zero or missing prices**, so that
    interpretation is inferred from the data, not documented by the provider.
33. **REVIEW_REQUIRED is a queue, not a solution.** 10.4% of scored contexts now
    land there. In a real deployment somebody has to work that queue; this demo
    only produces it. Post-freeze (`reports/22_POST_FREEZE_ENGINEERING.md`)
    added the tool a reviewer would use to work it - `scripts/review.py` and
    a dashboard "Review queue" page - but not the reviewer, an SLA, or a
    notification path.
34. **Shrinkage improves stability, not identification.** If the pooled
    elasticity is biased by promotion contamination, every shrunk product
    estimate inherits part of that bias.

## Phase M (final scientific audit) - what the audit found

These are the limitations to disclose in an interview, ranked by how much they
should change what you claim.

32. **Business rules, not the learned price response, set the magnitude of most
    recommendations.** Over all 13,964 decision contexts of week 399 under the
    default policy: **2.9%** of final recommendations come from an interior
    optimum of the estimated price response. 57.1% sit on a guardrail corner,
    25.3% are screened out before optimisation, 9.7% are risk-gated and 5.1%
    fail materiality. The constraint that binds first is the +/-10% price-change
    cap (44.6% of contexts). `reports/11_CONSTRAINT_ATTRIBUTION.md`.

33. **A rule with no demand model reproduces most of the engine's decisions.**
    "Always take the maximum allowed increase" lands within one 5-cent grid step
    of the engine's final price in **82.5%** of contexts and agrees on the
    decision state in 90.0%. `reports/12_MODEL_VALUE_ABLATION.md`.

34. **The demand forecast does not choose the price at all.** Under the hybrid
    response the baseline `Q_hat(p0)` is a positive constant across candidate
    prices, so it cancels out of `argmax (p - c) Q(p)`. The chosen price is a
    function of `(p0, cost, epsilon)` and the guardrails only; the forecast sets
    the predicted volume and the dollar amounts. Verified numerically - scaling
    the base model by 10x leaves every recommended price unchanged
    (`tests/test_attribution.py`).

35. **The guardrails neutralise the elasticity assumption.** Median variation in
    the selected price across the four sensitivity elasticities: 79.8% with only
    a cost floor, 6.6% with the historical-support band, **0.0%** once the
    +/-10% change cap is added. The pricing signal stops determining the
    recommendation at that layer. `reports/13_GUARDRAIL_ABLATION.md`.

36. **The Phase L standard errors were too small by a median factor of 3.8.**
    HC1 on a panel with within-store persistence and category-wide weekly
    shocks. Two-way clustering (UPC, week) widens the pooled elasticity standard
    error from 0.0065 to 0.106 - a factor of 16 - giving a 95% interval of
    [-2.24, -1.82] instead of [-2.04, -2.02]. The share of products significant
    at 5% falls from 90.1% to 75.2%.
    `reports/15_ELASTICITY_INFERENCE_AUDIT.md`.

37. **The 0.95 mean shrinkage weight was partly an artifact.** Understated
    standard errors inflate `w = tau^2/(tau^2 + se^2)` from both directions.
    Corrected: tau^2 1.101 -> 0.766 (REML) and mean weight 0.954 -> **0.780**.
    The remaining 0.78 is genuine - tau is about 0.88 against a median robust
    standard error of 0.39. `reports/14_SHRINKAGE_AUDIT.md`.

38. **Product-level elasticity does not reproduce across time windows.** Median
    rank correlation of shrunk product elasticities between disjoint training
    windows is **+0.19**, though sign stability is high. The *category*
    elasticity is stable: the largest pairwise z between disjoint windows is
    1.36, not significant. So a stable category price sensitivity is defensible;
    a durable product-specific elasticity is not.
    `reports/17_ELASTICITY_STABILITY.md`.

39. **Out-of-time price-response validation is naturalistic, not causal.** On
    154,899 unseen price-change episodes in weeks 258-399, `shrunk` has the
    lowest WAPE in every split (0.560 on non-promotion episodes vs 0.588 native
    ML, 0.594 pooled, 0.715 for a no-price-response null). But weeks in which
    the retailer changed price are not a random sample of weeks, and 73% of
    episodes carry a promotion code in one of the two weeks.
    `reports/16_OUT_OF_TIME_PRICE_RESPONSE.md`.

40. **The materiality threshold and the risk gate are asserted, not fitted.**
    1% and the risk bands are demo settings. Calibrating them would mean fitting
    realised out-of-sample error against the bands, which was not done.

41. **The 5-cent candidate grid quantises the answer.** The grid optimum sits a
    median of $0.025 (p90 $0.043) from the continuous optimum inside the same
    feasible interval - about 1% of a typical price.

## Engineering (recorded at the v1.0 freeze, 2026-08-19)

**Freezing v1.0 solved none of the limitations above.** They are scientific
limits of the data and the design, not a backlog. The items below are what the
engineering closure could and could not establish.

42. **The batch speed-up is a single local measurement, not a scalability
    result.** 3,000 contexts on one machine, one process: 122.0 s → 2.4 s
    (`artifacts/metrics/batch_performance.json`). Nothing here demonstrates
    behaviour at millions of SKUs, on other hardware, or under concurrency, and
    no such claim is made anywhere in the repository.

43. **The GitHub-hosted CI run has never executed.** `.github/workflows/ci.yml`
    exists and every step in it - install, lint, smoke imports, README-metrics
    check, pytest, Docker build - was reproduced locally and passes. But no Git
    remote is configured and none was created, so "CI is green on GitHub" is
    not a claim this project can make.

44. **The Docker image is not a production artifact.** It builds, runs as
    non-root (`appuser`, uid 1000) and serves `/health`, `/model/info` and a
    real recommendation identical to the local run. It is not hardened, has no
    authentication or rate limiting, is not load-tested, and is not deployed.
    The model artifact and the processed panel are **mounted read only** and are
    never baked into the image, because the Dominick's data may not be
    redistributed.

45. **The serialised model is not version-pinned to its training environment.**
    `demand_model.joblib` was fitted with scikit-learn 1.8.0; loading it under
    1.9.0 (the clean-environment and Docker runs) raises
    `InconsistentVersionWarning`. Predictions were verified to be identical in
    both environments for the audited contexts, but an unpinned pickle is a
    real reproducibility hazard, and a future version should either pin the
    runtime or persist the model in a version-stable format.

46. **The numeric consistency audit covers headline metrics, not every number.**
    It anchors on 25 named metrics across the hand-authored documents (212
    statements at the freeze, 0 mismatches). A number no check names can still
    drift; prose claims are covered separately - and only by *classification* -
    in `reports/20_CLAIM_AUDIT.md`.

47. **`make` is not available in the environment where v1.0 was frozen.** The
    Makefile targets were verified by running the underlying commands directly.
    The targets themselves are therefore documented but not executed as
    `make ...` in this environment.
