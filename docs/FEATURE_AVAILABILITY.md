# Feature availability at decision time

The decision being modelled: **at the end of week `t-1`, choose the price for
UPC `u` in store `s` for week `t`.** A feature is admissible only if its value
is knowable at that moment.

Classification used below:

* **KNOWN BEFORE DECISION** - fully determined by weeks `<= t-1`.
* **KNOWN AT DECISION TIME** - part of the decision itself, or planned.
* **KNOWN ONLY AFTER OUTCOME** - realised during week `t`; never an input.
* **REQUIRES ASSUMPTION** - available only under a stated assumption.

## Target

| column | class | note |
| --- | --- | --- |
| `move` (units sold in week `t`) | KNOWN ONLY AFTER OUTCOME | this is `y`; the model predicts it |

Revenue, gross profit and margin are **functions of the target** and are never
predictors. Predicting units and deriving money afterwards is what makes the
counterfactual simulation coherent.

## Model inputs

| feature | class | justification |
| --- | --- | --- |
| `effective_unit_price` | KNOWN AT DECISION TIME | it *is* the decision variable |
| `log_price` | KNOWN AT DECISION TIME | transform of the decision variable |
| `price_vs_last_week` | KNOWN AT DECISION TIME | candidate price vs `lag_price_1` |
| `price_vs_series_reference` | KNOWN AT DECISION TIME | candidate price vs expanding median of past prices |
| `price_vs_recent_mean` | KNOWN AT DECISION TIME | candidate price vs mean of previous 4 weeks |
| `recorded_promotion_flag` | KNOWN AT DECISION TIME (assumption) | promotions are planned in advance; the *code* is observed only afterwards, so this assumes the planned promotion calendar is known. Stated in `docs/DATA_LEAKAGE_AUDIT.md` |
| `lag_move_1..4`, `roll_mean_move_4/8/13`, `roll_std_move_4` | KNOWN BEFORE DECISION | shifted by >= 1 week by construction |
| `lag_price_1`, `lag_price_2`, `roll_mean_price_4`, `series_reference_price` | KNOWN BEFORE DECISION | past prices |
| `lag_promotion_1` | KNOWN BEFORE DECISION | last week's promotion code |
| `decision_time_unit_cost` | REQUIRES ASSUMPTION | latest known implied AAC, lagged one week and forward-filled. The contemporaneous AAC of week `t` is an outcome |
| `store`, `upc`, `com_code`, `package_size_oz` | KNOWN BEFORE DECISION | static identity / metadata |
| `week_of_year`, `month`, `quarter`, `time_index` | KNOWN BEFORE DECISION | calendar |
| `series_age_weeks` | KNOWN BEFORE DECISION | count of prior observations |

## Explicitly excluded

| column | class | why excluded |
| --- | --- | --- |
| `revenue`, `gross_profit` | KNOWN ONLY AFTER OUTCOME | mechanical functions of `move` - direct target leakage |
| `profit`, `gross_margin_rate` (week `t`) | KNOWN ONLY AFTER OUTCOME | accounting margin realised in week `t`; also mechanically tied to that week's price |
| `estimated_unit_aac` (week `t`) | KNOWN ONLY AFTER OUTCOME | derived from week `t` margin; replaced by `decision_time_unit_cost` |
| `sale` / `recorded_promotion_type` raw code of week `t` | partially KNOWN ONLY AFTER OUTCOME | only the binary planned flag is used, and the assumption is documented |
| `ok` | KNOWN ONLY AFTER OUTCOME | data-quality verdict, used for filtering only |
| `qty`, `price` (bundle form) | superseded | replaced by `effective_unit_price = price / qty` |

## Cost at recommendation time

Gross-profit optimization needs a unit cost. The rule adopted:

```text
decision_time_unit_cost(u, s, t) = estimated_unit_aac(u, s, t-1),
                                   forward-filled within the series
```

Rationale: the analyst knows what the item cost last week, not what the
accounting system will report for the week being priced. If no prior AAC
exists, the optimizer refuses to optimise gross profit and returns the
`COST_UNAVAILABLE` reason code rather than inventing a cost.

Consequence for evaluation: reported gross-profit figures use this lagged cost
consistently for both the current price and the candidate price, so the
comparison is internally consistent even where the lagged AAC is imperfect.
