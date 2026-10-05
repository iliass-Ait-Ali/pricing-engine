# Using your own data

The engine is not hard-wired to Dominick's. To point it at another retail
panel, you need a table at the grain **product x location x period** with the
columns below.

## 1. Minimum input contract

| required | meaning | Dominick's equivalent |
| --- | --- | --- |
| product id | stable identifier | `upc` |
| location id | store / site | `store` |
| period index | consecutive integer periods | `week` |
| units sold | the target | `move` |
| price paid | per transacted bundle | `price` |
| bundle size | units per bundle (1 if none) | `qty` |
| unit cost **or** margin % | to enable gross-profit optimization | `profit` (margin %) |
| promotion marker | optional but valuable | `sale` |
| validity flag | optional | `ok` |

Product metadata (description, size, category code) is optional and only
improves feature quality.

## 2. Steps

1. **Write a loader** next to `src/pricing_engine/data/loader.py` that returns a
   frame with lower-case columns matching the names above (or rename yours to
   match).
2. **Adjust the schema** in `src/pricing_engine/data/schema.py`:
   `MOVEMENT_REQUIRED_COLUMNS`, `PROMOTION_CODES` and `CANONICAL_COLUMNS`.
3. **Set the calendar anchor** in `configs/config.yaml`:
   `data.week1_start_date` must be the real start date of period 1, and periods
   must be evenly spaced. If your periods are days or months, change
   `decode_week` accordingly - it is one function.
4. **Check the derived economics** in `cleaning.add_derived_economics`. If you
   have a true unit cost instead of a margin percentage, set
   `estimated_unit_aac` directly from it and say so in the data dictionary; the
   rest of the pipeline only needs the column to exist.
5. **Re-tune the validity rules** in `cleaning.apply_validity_filters` and the
   thresholds in `configs/config.yaml` (`data.min_price`, `data.min_qty`).
6. **Run the pipeline in order**: `build_dataset` -> `validate_data` ->
   `run_eda` -> `build_features` -> `run_elasticity` -> `train` ->
   `price_response` -> `optimize` -> `backtest`.
7. **Re-check the gates**, especially:
   * the price-variation eligibility share from `run_eda` - if very few series
     have price movement, price optimization is not feasible on your data and
     that is the finding;
   * the Phase F price-response validation - if the demand curves are flat or
     wrong-signed, do not proceed to optimization.

## 3. Thresholds you must revisit

`configs/config.yaml` values were chosen for weekly US grocery data from the
1990s. At minimum re-derive:

| key | why it is data-specific |
| --- | --- |
| `eligibility.*` | how much history and price movement your category has |
| `optimization.price_step`, `price_rounding` | your currency and price ladder |
| `policy_profiles.*.max_price_change_pct` | your commercial policy |
| `policy_profiles.*.min_gross_margin_rate` | your margin structure |
| `policy_profiles.*.materiality_threshold_pct` | your model's error level |
| `risk.*` | the amount of history typical in your panel |
| `modeling.lags`, `rolling_windows` | your period length and seasonality |

The three policy profiles are **DEMO settings**, not industry benchmarks.

## 4. What will not transfer

* **The elasticity numbers.** They are specific to 1990s US cereal.
* **The choice of model.** Ridge won here; on a different panel the boosted
  model may win. `scripts/train.py` compares them again automatically.
* **The causal caveats do transfer.** Unless your prices were randomised, your
  estimates are observational too, and every claim in your README should carry
  the same language discipline (`docs/METRICS.md`, "naming discipline").

## 5. Sanity checklist before trusting any output

- [ ] grain uniqueness asserted on your canonical table
- [ ] every exclusion rule counted and documented
- [ ] all derived formulas re-verified by `validate_processed`
- [ ] chronological split, never random
- [ ] lag/rolling features shifted (run the leakage tests)
- [ ] cost held fixed across the candidate price grid
- [ ] price-response curves inspected before optimizing
- [ ] uplift described as model-estimated until an experiment says otherwise

## Worked example: another Dominick's category

Another category from the same source needs no code change, only a config:

```bash
python scripts/download_dominicks.py --category crackers
python scripts/run_category.py --config configs/crackers.yaml
python scripts/compare_categories.py --config configs/crackers.yaml
```

`configs/crackers.yaml` extends `config.yaml` and changes only the input file
names and the output folders, so the run is like for like with cereal
(`tests/test_category_config.py` enforces that). The result is
`reports/27_SECOND_CATEGORY.md`. One thing the first run surfaced: the
crackers file contains 200 blank export rows, which the loader and the
cleaning step now handle and count.
