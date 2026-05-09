"""Add regions and region_coords columns to services."""

from yoyo import step

__depends__ = {"20260509_02_extend_price_precision"}

steps = [
    step(
        """
        ALTER TABLE services
            ADD COLUMN IF NOT EXISTS regions       TEXT[]  NOT NULL DEFAULT '{}',
            ADD COLUMN IF NOT EXISTS region_coords JSONB   NOT NULL DEFAULT '[]'
        """,
        """
        ALTER TABLE services
            DROP COLUMN IF EXISTS regions,
            DROP COLUMN IF EXISTS region_coords
        """,
    )
]
