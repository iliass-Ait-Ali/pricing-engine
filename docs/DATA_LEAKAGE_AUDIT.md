# Data leakage audit

Leakage is the single most common way a retail-pricing portfolio project turns
into a fiction. This document lists each leakage channel, the control applied,
and the test that enforces it.

| # | Leakage channel | Why it would happen | Control | Enforcing test |
| --- | --- | --- | --- | --- |
| 1 | Target-derived features (`revenue`, `gross_profit`) | They are in the canonical table and correlate perfectly with `move` | They are never in `FEATURE_COLUMNS`; the feature list is a module-level constant | `test_no_outcome_columns_are_features` |
| 2 | Contemporaneous accounting margin / AAC | `profit` of week `t` is computed from week `t` transactions | Replaced by `decision_time_unit_cost` = AAC lagged one week, forward-filled | `test_decision_time_cost_is_lagged_not_contemporaneous` |
| 3 | Unshifted rolling windows | `rolling(4).mean()` on the raw target includes week `t` | All rolling windows are computed on the **already shifted** series | `test_rolling_windows_are_shifted`, `test_lag_features_never_use_the_current_week` |
| 4 | Expanding reference price including today | An expanding median over prices would embed the candidate price | `series_reference_price` is the expanding median of **shifted** prices | `test_series_reference_price_uses_only_past_prices` |
| 5 | Random train/test split on a time-indexed panel | Default sklearn behaviour | Chronological split by Dominick's week index; no shuffling anywhere | `test_temporal_split_is_chronological_and_disjoint` |
| 6 | Series with no history entering training | Lag columns would be NaN and imputed with future-informed statistics | `training_frame` requires `lag_move_1` and `lag_price_1`; 36,443 first-week rows are dropped | `test_training_frame_drops_rows_without_history` |
| 7 | Price-feature staleness in simulation | Changing the candidate price but scoring stale `log_price` / relative-price features | One shared `recompute_price_features` used by training, simulator, optimizer and API | `test_recompute_price_features_changes_only_price_features`, `test_price_features_recomputed_for_each_candidate` |
| 8 | Cost moving with the candidate price | Reusing the observed margin percentage at a new price mechanically fixes margin and fakes profit | Cost is held fixed across the candidate grid unless a scenario changes it explicitly | `test_cost_is_held_fixed_across_price_grid` |
| 9 | Model selection on the test period | Tuning until the test number looks good | Selection uses the validation window only; the test window is scored once for the final report | training log in `artifacts/metrics/model_metrics.json` records both |
| 10 | Backtest using the full-sample model | Scoring 1996 weeks with a model that saw 1996 | The backtest scores only weeks after the training window of the loaded model | `reports/06_BACKTEST.md` states the windows |
| 11 | **Elasticity fitted on evaluation outcomes** (Phase L) | The pricing elasticity is itself estimated from data; fitting it on validation/test weeks would leak those outcomes into the recommendations scored there | `scripts/estimate_elasticity.py` restricts estimation to the model's training weeks (2-257), records the window in the elasticity table metadata, and aborts if any input row exceeds it | `tests/test_phase_l_pricing.py::test_build_elasticity_table_marks_sources_and_records_the_window` |
| 12 | **Future cost information** (Phase L deep audit) | The AAC of the priced week is an outcome | Lagged, forward-filled AAC, verified by three independent checks on the real data (identity, future poisoning, recommendation-level) | `reports/10_COST_LEAKAGE_AUDIT.md`, `tests/test_cost_leakage.py` |

## Documented assumption: the promotion flag

`recorded_promotion_flag` is used as an input for week `t`. Strictly, the
`sale` code is *observed* in the movement record, i.e. after the fact.

The assumption is: **a retailer setting next week's price already knows whether
that item is scheduled for a Bonus Buy / coupon / price reduction that week.**
This is realistic (promotion calendars are agreed weeks ahead), but it is an
assumption, not a data-derived fact.

Its impact is bounded and reported: 7.35% of rows carry a recorded code, and
`reports/04_MODEL_COMPARISON.md` reports error separately for promotion and
non-promotion weeks so the dependence is visible.

## What is *not* claimed

No leakage control turns an observational panel into an experiment. Even a
perfectly leak-free model estimates `P(units | price, context)`, not
`P(units | do(price))`. See `docs/CAUSAL_LIMITATIONS.md`.
