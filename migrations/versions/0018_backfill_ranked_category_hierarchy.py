"""Backfill category parents from ranked product associations.

Revision ID: 0018
Revises: 0017
"""

from __future__ import annotations

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

FALLBACK = "local:uncategorized"


def upgrade() -> None:
    # Older OFF requests retained category associations and their broad-to-specific
    # rank, but did not retain categories_hierarchy in the raw payload. Reconstruct
    # immediate parent votes from those ranks and only fill still-empty links.
    # Candidate chains that contain a cycle are excluded from the backfill.
    op.execute(
        f"""
        WITH RECURSIVE ordered AS (
            SELECT
                association.product_barcode,
                association.category_id,
                lag(association.category_id) OVER (
                    PARTITION BY association.product_barcode
                    ORDER BY association.source_rank, association.category_id
                ) AS parent_id
            FROM product_categories AS association
            WHERE association.source_rank IS NOT NULL
              AND association.category_id <> '{FALLBACK}'
        ),
        votes AS (
            SELECT category_id, parent_id, count(*) AS vote_count
            FROM ordered
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
        ),
        chosen AS (
            SELECT category_id, parent_id
            FROM ranked
            WHERE preference = 1
        ),
        walk(start_id, current_id, path, cycle) AS (
            SELECT
                category_id,
                parent_id,
                ARRAY[category_id, parent_id]::text[],
                false
            FROM chosen
            UNION ALL
            SELECT
                walk.start_id,
                chosen.parent_id,
                walk.path || chosen.parent_id,
                chosen.parent_id = ANY(walk.path)
            FROM walk
            JOIN chosen ON chosen.category_id = walk.current_id
            WHERE NOT walk.cycle AND cardinality(walk.path) < 100
        ),
        unsafe AS (
            SELECT DISTINCT start_id
            FROM walk
            WHERE cycle
        )
        UPDATE food_categories AS category
        SET parent_id = chosen.parent_id
        FROM chosen
        JOIN food_categories AS parent ON parent.id = chosen.parent_id
        WHERE category.id = chosen.category_id
          AND category.parent_id IS NULL
          AND category.id <> chosen.parent_id
          AND NOT EXISTS (
              SELECT 1 FROM unsafe WHERE unsafe.start_id = chosen.category_id
          )
        """
    )


def downgrade() -> None:
    # Parent links can be edited after migration and cannot be safely identified.
    pass
