# Data dictionary - canonical Cereals table

File: `data/processed/dominicks_cereals.parquet`
Grain: **one valid `upc` x `store` x `week` observation** (uniqueness asserted).

## Raw fields (as documented in the Kilts Center manual)

| column | type | meaning |
| --- | --- | --- |
| `upc` | int64 | Universal Product Code of the item |
| `store` | int32 | Dominick's store number |
| `week` | int32 | Dominick's week index (1 = week starting 1989-09-14) |
| `move` | float | number of **individual units** sold in that store-week |
| `price` | float | price of the **bundle** actually scanned (may cover several units) |
| `qty` | float | number of items in the bundle |
| `sale` | string | promotion code: `B` = Bonus Buy, `C` = Coupon, `S` = simple price reduction (also observed: undocumented `G`, `L`) |
| `profit` | float | gross margin **percentage** (based on Average Acquisition Cost) |
| `ok` | int8 | 1 = valid, 0 = suspect / trash per the manual (only `ok = 1` is kept) |

Raw fields intentionally **not** loaded: `PRICE_HEX`, `PROFIT_HEX` (redundant
binary encodings of `price` and `profit`).

## Product metadata (`upccer.csv`)

| column | meaning |
| --- | --- |
| `com_code` | commodity code (Cereals) |
| `descrip` | product description |
| `size` | package size string, e.g. `12 OZ` |
| `case` | units per case |
| `nitem` | internal item number |

Joined with `validate="many_to_one"`; the row count is asserted before/after.

## Derived economics (the equations the whole project rests on)

```text
effective_unit_price = price / qty
revenue              = effective_unit_price * move          ( = price * move / qty )
gross_margin_rate    = profit / 100
estimated_unit_aac   = effective_unit_price * (1 - gross_margin_rate)
gross_profit         = revenue * gross_margin_rate          ( = (p - aac) * move )
```

| column | meaning |
| --- | --- |
| `effective_unit_price` | price actually paid per individual unit; the decision variable of this project |
| `revenue` | historical observed revenue of that store-week-UPC |
| `gross_margin_rate` | margin as a fraction, not a percentage |
| `estimated_unit_aac` | unit cost **implied by the accounting margin** |
| `gross_profit` | historical observed gross profit |

### Why the name `estimated_unit_aac` matters

`profit` is a margin computed on **Average Acquisition Cost** - an inventory
accounting measure of what the retailer paid on average for stock on hand. It
is **not necessarily the economically relevant replacement cost** at the moment
of a pricing decision (forward buying, trade deals and inventory ageing all
drive a wedge). The column is therefore never called `true_unit_cost`, and
`docs/PRICING_SCIENCE.md` discusses the consequences for profit optimization.

## Calendar fields

| column | meaning |
| --- | --- |
| `week_start_date` | `1989-09-14 + (week - 1) * 7 days`; Dominick's store weeks run Thursday -> Wednesday |
| `year`, `month`, `quarter`, `week_of_year` | derived from `week_start_date` |

The raw integer `week` is never used as a bare continuous ML feature before
decoding; models use calendar features plus an explicit time index.

## Promotion fields

| column | meaning |
| --- | --- |
| `recorded_promotion_flag` | 1 if a documented promotion code (`B`/`C`/`S`) is present |
| `recorded_promotion_type` | `Bonus Buy` / `Coupon` / `Simple price reduction` / `UNKNOWN_CODE` / `NONE_RECORDED` |

`NONE_RECORDED` means *no code was recorded*, **not** *no promotion happened*.

## Quality flags (kept, never used to silently drop rows)

| column | meaning |
| --- | --- |
| `margin_implausible_flag` | `gross_margin_rate` outside (-1, 1) |
| `aac_nonpositive_flag` | implied unit AAC <= 0 |
| `zero_move_flag` | zero units sold that week (real information, kept) |
| `bundle_flag` | `qty > 1` (multi-unit bundle) |
| `has_metadata_flag` | UPC matched the metadata file |

## Exclusion rules applied when building the canonical table

Every rule is counted in `reports/01_DATA_AUDIT.md` with rows removed and share
of raw: `missing_key`, `missing_core_measure`, `ok_flag_zero`,
`non_positive_price`, `non_positive_qty`, `negative_move`, plus exact-duplicate
and duplicate-grain handling.
