"""Remove malformed OFF category associations from the active catalog.

Revision ID: 0020
Revises: 0019
"""

from __future__ import annotations

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

FALLBACK = "local:uncategorized"
CANONICAL_PATTERN = "^[a-z]{2}:[a-z0-9]+(-[a-z0-9]+)*$"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TEMP TABLE larder_invalid_category_products ON COMMIT DROP AS
        SELECT DISTINCT product_barcode
        FROM product_categories
        WHERE category_id NOT LIKE 'local:%'
          AND category_id !~ '{CANONICAL_PATTERN}'
        """
    )
    op.execute(
        """
        UPDATE product_categories AS association
        SET is_primary = false
        WHERE association.product_barcode IN (
            SELECT product_barcode FROM larder_invalid_category_products
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
            JOIN larder_invalid_category_products AS affected
              ON affected.product_barcode = association.product_barcode
            WHERE (
                association.category_id LIKE 'local:%'
                OR association.category_id ~ '{CANONICAL_PATTERN}'
            )
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
        INSERT INTO product_categories
            (product_barcode, category_id, source_rank, is_primary, created_at)
        SELECT affected.product_barcode, '{FALLBACK}', NULL, true, now()
        FROM larder_invalid_category_products AS affected
        WHERE NOT EXISTS (
            SELECT 1
            FROM product_categories AS association
            WHERE association.product_barcode = affected.product_barcode
              AND association.is_primary = true
        )
        ON CONFLICT (product_barcode, category_id)
        DO UPDATE SET is_primary = true
        """
    )
    op.execute(
        f"""
        DELETE FROM product_categories
        WHERE category_id NOT LIKE 'local:%'
          AND category_id !~ '{CANONICAL_PATTERN}'
        """
    )
    op.execute(
        f"""
        UPDATE food_categories
        SET parent_id = NULL, parent_source = NULL
        WHERE parent_id NOT LIKE 'local:%'
          AND parent_id !~ '{CANONICAL_PATTERN}'
        """
    )
    op.execute(
        f"""
        UPDATE food_categories
        SET active = false, parent_id = NULL, parent_source = NULL
        WHERE id NOT LIKE 'local:%'
          AND id !~ '{CANONICAL_PATTERN}'
        """
    )


def downgrade() -> None:
    # The malformed source associations cannot be reconstructed safely.
    pass
