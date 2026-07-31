"""Repair products whose fallback category remained the primary display category.

Revision ID: 0016
Revises: 0015
"""

from __future__ import annotations

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

FALLBACK = "local:uncategorized"


def upgrade() -> None:
    # Only repair products where the fallback is currently primary. This
    # preserves any deliberate non-fallback primary choice. OFF source_rank
    # increases from broad to specific, so the highest rank is the best label.
    op.execute(
        f"""
        CREATE TEMP TABLE larder_category_repair ON COMMIT DROP AS
        SELECT DISTINCT fallback.product_barcode
        FROM product_categories AS fallback
        WHERE fallback.category_id = '{FALLBACK}'
          AND fallback.is_primary = true
          AND EXISTS (
              SELECT 1
              FROM product_categories AS candidate
              JOIN food_categories AS category ON category.id = candidate.category_id
              WHERE candidate.product_barcode = fallback.product_barcode
                AND candidate.category_id <> '{FALLBACK}'
                AND category.active = true
          )
        """
    )
    op.execute(
        """
        UPDATE product_categories AS association
        SET is_primary = false
        WHERE association.product_barcode IN (
            SELECT product_barcode FROM larder_category_repair
        )
        """
    )
    op.execute(
        f"""
        WITH ranked AS (
            SELECT
                association.product_barcode,
                association.category_id,
                row_number() OVER (
                    PARTITION BY association.product_barcode
                    ORDER BY association.source_rank DESC NULLS LAST, association.category_id
                ) AS position
            FROM product_categories AS association
            JOIN food_categories AS category ON category.id = association.category_id
            JOIN larder_category_repair AS repair
              ON repair.product_barcode = association.product_barcode
            WHERE association.category_id <> '{FALLBACK}'
              AND category.active = true
        )
        UPDATE product_categories AS association
        SET is_primary = true
        FROM ranked
        WHERE ranked.position = 1
          AND association.product_barcode = ranked.product_barcode
          AND association.category_id = ranked.category_id
        """
    )
    op.execute(
        f"""
        DELETE FROM product_categories AS fallback
        USING larder_category_repair AS repair
        WHERE fallback.product_barcode = repair.product_barcode
          AND fallback.category_id = '{FALLBACK}'
        """
    )


def downgrade() -> None:
    # This data repair is intentionally irreversible: restoring stale fallback
    # primaries would make catalog display incorrect again.
    pass
