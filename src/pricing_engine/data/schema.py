"""Schema definitions for the Dominick's Cereals raw and canonical tables.

The Kilts Center manual/codebook is the source of truth for the raw fields.
Relevant movement fields (per the official documentation):

    upc     UPC code of the item
    store   store number
    week    Dominick's week index (1 = week starting 1989-09-14)
    move    number of individual units sold
    price   price of the bundle (may cover several units)
    qty     number of items in the bundle
    sale    promotion code: B = Bonus Buy, C = Coupon, S = simple price reduction
    profit  gross margin *percentage* (based on Average Acquisition Cost)
    ok      1 = valid observation, 0 = suspect / trash

Derived quantities (documented in docs/DATA_DICTIONARY.md):

    effective_unit_price = price / qty
    revenue              = effective_unit_price * move
    gross_margin_rate    = profit / 100
    estimated_unit_aac   = effective_unit_price * (1 - gross_margin_rate)
    gross_profit         = revenue * gross_margin_rate
"""

from __future__ import annotations

MOVEMENT_REQUIRED_COLUMNS: tuple[str, ...] = (
    "store",
    "upc",
    "week",
    "move",
    "qty",
    "price",
    "sale",
    "profit",
    "ok",
)

MOVEMENT_OPTIONAL_COLUMNS: tuple[str, ...] = (
    "price_hex",
    "profit_hex",
)

UPC_REQUIRED_COLUMNS: tuple[str, ...] = (
    "com_code",
    "upc",
    "descrip",
    "size",
    "case",
    "nitem",
)

#: Logical grain of the movement table.
GRAIN: tuple[str, ...] = ("upc", "store", "week")

#: Promotion codes documented in the manual.
PROMOTION_CODES: dict[str, str] = {
    "B": "Bonus Buy",
    "C": "Coupon",
    "S": "Simple price reduction",
}

#: Columns of the canonical processed table (order matters for readability).
CANONICAL_COLUMNS: tuple[str, ...] = (
    "upc",
    "store",
    "week",
    "week_start_date",
    "year",
    "month",
    "quarter",
    "week_of_year",
    "move",
    "price",
    "qty",
    "effective_unit_price",
    "profit",
    "gross_margin_rate",
    "estimated_unit_aac",
    "revenue",
    "gross_profit",
    "sale",
    "recorded_promotion_flag",
    "recorded_promotion_type",
    "ok",
    "com_code",
    "descrip",
    "size",
    "case",
    "nitem",
)


class SchemaError(ValueError):
    """Raised when a raw file does not match the documented Dominick's schema."""


def assert_required_columns(columns: list[str], required: tuple[str, ...], source: str) -> None:
    """Raise a domain-specific error listing every missing column."""
    missing = [c for c in required if c not in columns]
    if missing:
        raise SchemaError(
            f"{source} is missing required Dominick's column(s): {missing}. "
            f"Found columns: {sorted(columns)}. "
            "Check that the file came from the official Kilts Center distribution."
        )
