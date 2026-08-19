# 01 - Data Audit (Dominick's Cereals)

**Generated:** 2026-08-18T08:05:28+00:00
**Status:** VALIDATED

All numbers below were produced by `python scripts/build_dataset.py` on the
official Kilts Center files. Nothing here is hardcoded.

## 1. Source files

| file | bytes | sha256 | url_or_member |
| --- | --- | --- | --- |
| upccer.csv | 25,932 | affa589f080ab18a... | https://www.chicagobooth.edu/research/kilts/research-data/-/media/enterprise/centers/kilts/datasets/dominicks-dataset/upc_csv-files/upccer.csv |
| wcer.zip | 42,402,094 | 2d4a59f88e4b9725... | https://www.chicagobooth.edu/research/kilts/research-data/-/media/enterprise/centers/kilts/datasets/dominicks-dataset/movement_csv-files/wcer.zip |
| wcer.csv | 458,400,475 | a204106f6dff8a6c... | wcer.csv |
| dominicks-manual-and-codebook.pdf | 10,182,109 | d8dc133bfe402a21... | https://www.chicagobooth.edu/research/kilts/research-data/-/media/enterprise/centers/kilts/datasets/dominicks-dataset/dominicks-manual-and-codebook_kiltscenter.pdf |

Provider: Kilts Center for Marketing, University of Chicago Booth School of Business.
Academic-use data; not redistributed in this repository (see `docs/DATA_SOURCE.md`).

## 2. Raw movement file (before any cleaning)

| property | value |
| --- | --- |
| rows | 6,602,582 |
| columns | store, upc, week, move, qty, price, sale, profit, ok |
| distinct UPCs | 490 |
| distinct stores | 93 |
| distinct weeks | 367 |
| week index range | 1 .. 399 |
| exact duplicate rows | 0 |
| duplicate (upc, store, week) rows | 0 |

### Dtypes and missingness (raw)

| column | dtype | nulls |
| --- | --- | --- |
| store | int32 | 0 |
| upc | int64 | 0 |
| week | int32 | 0 |
| move | float64 | 0 |
| qty | float64 | 0 |
| price | float64 | 0 |
| sale | string | 6,242,568 |
| profit | float64 | 0 |
| ok | int8 | 0 |

### ok flag (manual: 1 = valid, 0 = suspect / trash)

| ok | rows |
| --- | --- |
| 1 | 6,461,297 |
| 0 | 141,285 |

### Recorded sale codes (B = Bonus Buy, C = Coupon, S = simple price reduction)

| sale | rows |
| --- | --- |
| <NA> | 6,242,568 |
| B | 254,261 |
| S | 91,259 |
| G | 11,075 |
| C | 3,418 |
| L | 1 |

The manual states promotion coding is incomplete: a present code indicates a
promotion, but a missing code does **not** prove that no promotion occurred.

### Raw numeric distributions

| metric | count | missing | mean | std | min | p01 | median | p99 | max | n_zero | n_negative |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| price | 6,602,582 | 0 | 2.2412 | 1.5419 | 0.0000 | 0.0000 | 2.7900 | 4.7500 | 26.0200 | 1,851,380 | 0 |
| qty | 6,602,582 | 0 | 1.0012 | 0.0493 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 4.0000 | 0 | 0 |
| move | 6,602,582 | 0 | 14.0812 | 50.5852 | 0.0000 | 0.0000 | 8.0000 | 103.0000 | 18,688.0000 | 1,850,703 | 0 |
| profit | 6,602,582 | 0 | 12.5222 | 11.4400 | -99.6900 | 0.0000 | 13.7400 | 46.2100 | 99.9900 | 1,852,626 | 53,057 |

## 3. Raw UPC metadata file

| property | value |
| --- | --- |
| rows | 490 |
| distinct UPCs | 490 |
| duplicate UPC rows | 0 |
| columns | com_code, upc, descrip, size, case, nitem |

## 4. Exclusions applied (every rule counted)

Raw rows: **6,602,582** -> canonical rows: **4,707,776**
(removed 1,894,806, 28.698% of raw).

| rule | rows_removed | pct_of_raw | reason |
| --- | --- | --- | --- |
| missing_key | 0 | 0.0000% | upc / store / week must be present to identify an observation |
| missing_core_measure | 0 | 0.0000% | price, move, qty and profit are required for pricing economics |
| ok_flag_zero | 141,285 | 2.1398% | the manual marks ok = 0 rows as suspect / trash |
| non_positive_price | 1,753,521 | 26.5581% | bundle price must be at least 0.01 |
| non_positive_qty | 0 | 0.0000% | bundle quantity must be at least 1.0 to compute a unit price |
| negative_move | 0 | 0.0000% | negative unit movement cannot be interpreted as demand |
| metadata_unmatched_kept | 0 | 0.0000% | 0 rows have no UPC metadata; they are kept and flagged, not dropped |

## 5. Metadata join quality

| property | value |
| --- | --- |
| movement UPCs | 489 |
| metadata UPCs | 490 |
| movement UPCs without metadata | 0 |
| metadata UPCs without movement | 1 |
| rows without metadata (kept, flagged) | 0 (0.000%) |
| duplicate metadata UPC rows | 0 |

The join is executed with `validate="many_to_one"`, so it cannot multiply rows;
the row count is asserted before/after.

## 6. Canonical table

| property | value |
| --- | --- |
| grain | one valid UPC x store x week observation |
| rows | 4,707,776 |
| UPCs | 489 |
| stores | 93 |
| weeks | 366 |
| week index range | 1 .. 399 |
| calendar range | 1989-09-14 .. 1997-05-01 |
| total units sold (historical observed) | 90,766,941 |
| total revenue (historical observed) | $262,008,582.30 |
| total gross profit (historical observed) | $40,091,143.49 |
| parquet sha256 | `51f9148bd3955129a2d7e86953d487a4707296e7ec1b306d1ced5089799761a9` |
| content fingerprint | `e9d26f2c0eda9f7e9d8dbb508053e56a932608668e16d8b5aeec0032373722d0` |

### Processed numeric distributions

| metric | count | missing | mean | std | min | p01 | median | p99 | max | n_zero | n_negative |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| effective_unit_price | 4,707,776 | 0 | 3.1157 | 0.7653 | 0.0500 | 0.7900 | 3.1500 | 4.8500 | 26.0200 | 0 | 0 |
| move | 4,707,776 | 0 | 19.2802 | 43.7618 | 1.0000 | 1.0000 | 13.0000 | 134.0000 | 17,824.0000 | 0 | 0 |
| qty | 4,707,776 | 0 | 1.0017 | 0.0578 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 4.0000 | 0 | 0 |
| gross_margin_rate | 4,707,776 | 0 | 0.1742 | 0.0987 | -0.9969 | -0.0348 | 0.1663 | 0.4873 | 0.9999 | 1,246 | 53,057 |
| estimated_unit_aac | 4,707,776 | 0 | 2.5741 | 0.6866 | 0.0000 | 0.6290 | 2.6169 | 4.0343 | 9.7523 | 0 | 0 |
| revenue | 4,707,776 | 0 | 55.6544 | 93.6018 | 0.0500 | 2.9200 | 38.9300 | 314.1000 | 26,736.0000 | 0 | 0 |
| gross_profit | 4,707,776 | 0 | 8.5159 | 29.0663 | -18,768.6720 | -1.4125 | 6.1283 | 55.5616 | 6,591.2451 | 1,246 | 53,057 |

### Recorded promotion coding

| recorded_promotion_type | rows | share |
| --- | --- | --- |
| NONE_RECORDED | 4,350,772 | 92.42% |
| Bonus Buy | 251,546 | 5.34% |
| Simple price reduction | 91,077 | 1.93% |
| UNKNOWN_CODE | 11,059 | 0.23% |
| Coupon | 3,322 | 0.07% |

### Quality flags (kept, not dropped)

| flag | rows | share |
| --- | --- | --- |
| margin_implausible_flag | 0 | 0.000% |
| aac_nonpositive_flag | 0 | 0.000% |
| zero_move_flag | 0 | 0.000% |
| bundle_flag | 4,946 | 0.105% |
| has_metadata_flag | 4,707,776 | 100.000% |

## 7. Formula and structural checks

| check | result | detail |
| --- | --- | --- |
| non_empty | PASS | 4,707,776 rows |
| grain_unique | PASS | 0 duplicated upc x store x week rows |
| only_ok_rows | PASS | 0 rows with ok != 1 |
| formula_effective_unit_price | PASS | max abs deviation 0.000e+00 |
| formula_revenue | PASS | max abs deviation 1.421e-14 |
| formula_gross_margin_rate | PASS | max abs deviation 0.000e+00 |
| formula_estimated_unit_aac | PASS | max abs deviation 0.000e+00 |
| formula_gross_profit | PASS | max abs deviation 0.000e+00 |
| positive_price | PASS | all bundle prices are strictly positive |
| positive_qty | PASS | all bundle quantities are strictly positive |
| non_negative_move | PASS | no negative unit movement |
| no_infinite_unit_price | PASS | effective_unit_price is finite everywhere (no silent divide-by-zero) |
| week_date_mapping_unique | PASS | each Dominick's week index maps to exactly one calendar date |
| date_range_plausible | PASS | 1989-09-14 .. 1997-05-01 |
| promotion_flag_binary | PASS | recorded promotion share = 0.0735 |

## 8. Reproducibility

| property | value |
| --- | --- |
| python | 3.13.0 |
| pandas | 2.2.3 |
| numpy | 2.0.2 |
| platform | Windows-11-10.0.26200-SP0 |
| git commit | HEAD |

Command: `python scripts/build_dataset.py`
