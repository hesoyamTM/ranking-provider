"""
Extend price_from_rub precision from NUMERIC(14,2) to NUMERIC(18,8)
to correctly store per-minute and per-GB micro-prices.
"""

from yoyo import step

__depends__ = {"20260509_01_init"}

steps = [
    step(
        "ALTER TABLE services ALTER COLUMN price_from_rub TYPE NUMERIC(18, 8)",
        "ALTER TABLE services ALTER COLUMN price_from_rub TYPE NUMERIC(14, 2)",
    ),
]
