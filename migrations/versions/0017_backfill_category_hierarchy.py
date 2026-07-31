"""Backfill category parent links from stored Open Food Facts hierarchies.

Revision ID: 0017
Revises: 0016
"""

from __future__ import annotations

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A category can appear in multiple product payloads. Pick its most common
    # reported immediate parent (then ID for deterministic ties), and only fill
    # missing links so manual/existing hierarchy decisions remain authoritative.
    op.execute(
        """
        WITH expanded AS (
            SELECT
                product.barcode AS product_barcode,
                element.value #>> '{}' AS category_id,
                element.ordinality AS position
            FROM products AS product
            CROSS JOIN LATERAL jsonb_array_elements(
                CASE
                    WHEN jsonb_typeof(product.raw_source_payload -> 'categories_hierarchy') = 'array'
                    THEN product.raw_source_payload -> 'categories_hierarchy'
                    ELSE '[]'::jsonb
                END
            ) WITH ORDINALITY AS element(value, ordinality)
        ),
        deduplicated AS (
            SELECT product_barcode, category_id, min(position) AS position
            FROM expanded
            WHERE category_id <> ''
            GROUP BY product_barcode, category_id
        ),
        hierarchy AS (
            SELECT
                product_barcode,
                category_id,
                lag(category_id) OVER (
                    PARTITION BY product_barcode ORDER BY position
                ) AS parent_id
            FROM deduplicated
        ),
        votes AS (
            SELECT category_id, parent_id, count(*) AS vote_count
            FROM hierarchy
            WHERE parent_id IS NOT NULL AND category_id <> parent_id
            GROUP BY category_id, parent_id
        ),
        ranked AS (
            SELECT
                category_id,
                parent_id,
                row_number() OVER (
                    PARTITION BY category_id
                    ORDER BY vote_count DESC, parent_id
                ) AS preference
            FROM votes
        )
        UPDATE food_categories AS category
        SET parent_id = ranked.parent_id
        FROM ranked
        JOIN food_categories AS parent ON parent.id = ranked.parent_id
        WHERE ranked.preference = 1
          AND category.id = ranked.category_id
          AND category.parent_id IS NULL
          AND category.id <> ranked.parent_id
        """
    )


def downgrade() -> None:
    # Parent links may be edited after this migration, so they cannot be safely
    # distinguished from the backfilled values during downgrade.
    pass
