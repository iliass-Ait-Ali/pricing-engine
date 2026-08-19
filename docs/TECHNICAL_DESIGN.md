# Technical design

## Pipeline

```text
official Kilts Center files
  scripts/download_dominicks.py      -> data/raw/dominicks/cereals/{upccer.csv, wcer.csv, SOURCE.json}
  scripts/build_dataset.py           -> data/processed/dominicks_cereals.parquet          (4.7 M rows)
  scripts/validate_data.py           -> 15 formula / structural checks
  scripts/run_eda.py                 -> reports/02, price-variation eligibility table
  scripts/build_features.py          -> data/processed/..._features.parquet
  scripts/run_elasticity.py          -> reports/03, per-UPC elasticities
  scripts/train.py                   -> artifacts/models/demand_model.joblib, reports/04
  scripts/price_response.py          -> reports/05  (gate before optimization)
  scripts/optimize.py                -> reports/07, artifacts/recommendation_log.csv
  scripts/backtest.py                -> reports/06
  scripts/monitor.py                 -> reports/MONITORING_DESIGN.md
  api/main.py                        -> FastAPI service
  dashboard/app.py                   -> Streamlit, 9 pages
```

## Package layout

```text
src/pricing_engine/
  config.py            single source of thresholds (configs/config.yaml)
  audit.py             append-only recommendation log + lifecycle states
  data/                schema, loader, cleaning, validator
  economics/           metrics (revenue, margin, price variation), elasticity
  features/            build.py - decision-time feature contract
  models/              demand_model.py, baselines.py, metrics.py
  simulation/          price_grid.py, counterfactual.py
  optimization/        objective.py, constraints.py, risk.py, optimizer.py, policies.py
  monitoring/          drift.py
  utils/               io.py (hashing, fingerprints, environment record)
api/                   main.py, schemas.py, state.py, routes/
dashboard/             app.py
scripts/               one CLI per pipeline stage
tests/                 519 tests
```

## Key design decisions

### 1. One implementation of price features

`features.build.recompute_price_features` is the *only* place price-dependent
features are computed. Training, the simulator, the optimizer, the API and the
dashboard all call it. This makes it impossible for a candidate price to be
scored against stale relative-price features, and the invariant is tested
(`test_price_dependent_feature_list_is_complete`).

### 2a. Price response is a separate, swappable component (Phase L)

`models/hybrid.py` wraps any fitted demand model with an explicit price
response:

```text
Q(p) = Q_hat(p0) * (p / p0) ** epsilon
```

Three methods (`ml`, `pooled`, `shrunk`) satisfy the same `predict(frame)`
contract, so the simulator, optimizer, API and dashboard are unchanged when the
method changes. The reference price `p0` is stamped onto the decision context
(`attach_reference_price`) before any candidate is substituted; the hybrid
raises rather than silently degrading if it is missing - a silent fallback would
make the elasticity term vanish without anyone noticing.

Elasticities live in `economics/elasticity_store.py`: estimation (pooled, UPC x
store fixed effects, per-UPC with full diagnostics), empirical-Bayes shrinkage,
CSV persistence, and lookup with a pooled fallback.

### 2b. Decision states and the risk gate (Phase L)

`optimize_price` returns `RECOMMEND_CHANGE`, `KEEP_CURRENT` or
`REVIEW_REQUIRED`, plus an `actionable` flag. Risk is assessed twice: once at
the current price (which can tighten the change cap for MEDIUM risk, or stop the
process entirely for HIGH risk) and once at the proposed price, because
extrapolation distance is only knowable then. HIGH-risk contexts are gated by
`policy_profiles.<profile>.high_risk_action` - `review_required` under
`standard`, `keep_current` under `conservative`. Only the explicitly labelled
DEMO `aggressive` profile may set `recommend`.

### 2. Model behind a narrow interface

`DemandModel.predict(frame) -> non-negative units`. Two families
(`ridge_loglog`, `hgb_poisson`) sit behind it, so swapping the model changes
nothing downstream. The model owns its own preprocessing, so serialisation is a
single joblib artifact plus metadata.

### 3. Grid optimization, not a continuous solver

The demand model can be a step function (trees), so gradients are meaningless.
A discrete grid makes every constraint check explicit, every candidate
auditable, and the analytical tests exact. Scoring is vectorised: one batched
`predict` per context (`test_single_batched_prediction_call`), and
`simulate_many` batches across contexts for portfolio runs.

### 4. Constraints as an interval, reasons as codes

All guardrails are intersected into one feasible interval
(`optimization/constraints.py`), and each binding constraint contributes a
reason code. The recommendation object therefore explains itself without any
post-hoc narrative.

### 5. Cost is a decision-time input, never an outcome

`decision_time_unit_cost` = lagged, forward-filled implied AAC. It is held
fixed across the candidate grid. Gross-profit optimization without a cost is
refused (`COST_UNAVAILABLE`) rather than silently falling back to revenue.

### 6. Configuration over magic numbers

Every threshold - price step, rounding, max change, margin floor,
extrapolation tolerance, materiality, eligibility, risk bands, model
hyper-parameters - lives in `configs/config.yaml`, with three DEMO policy
profiles (conservative / standard / aggressive).

### 7. Reproducibility

Each artifact records: Python and library versions, platform, git commit, seed,
training window, feature list, metrics, dataset fingerprint (SHA-256 of the
parquet plus a content hash of the frame).

## Performance notes

Measured on the development machine (Windows, Python 3.13):

| stage | rows | time |
| --- | --- | --- |
| build canonical dataset | 6.6 M raw -> 4.7 M | ~3 min (458 MB CSV read) |
| build features | 4.7 M | 73 s |
| train (baselines + ridge + HGB) | 3.2 M | 243 s |
| price-response validation | 300 contexts x 25 prices | ~30 s |
| batch recommendations | 3,000 contexts | 144 s |
| backtest | 10 weeks x ~390 contexts x 4 policies | ~7 min |
| test suite | 519 tests | 11 s |

The batch optimizer calls `optimize_price` per context (each doing one batched
prediction over its grid). `simulation.simulate_many` exists for the fully
vectorised path when a run needs to scale to all 36,443 series.

## Error handling

Domain errors, not `KeyError`:

```text
ConfigError          missing configuration key
SchemaError          raw file does not match the documented Dominick's schema
RawDataMissingError  official files not acquired yet (prints the command to run)
FeatureError         candidate price array invalid / frame length mismatch
ModelError           scoring a frame without the required feature columns
SimulationError      multi-row context, empty grid, negative predicted demand
ObjectiveError       unknown objective, or gross profit requested without a cost
ConstraintError      inconsistent guardrails (e.g. min margin >= 1)
PriceGridError       empty or unbuildable candidate grid
OptimizerError       recommendation impossible for this context
```
