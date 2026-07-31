"""Track category parent provenance for canonical sync and manual overrides.

Revision ID: 0019
Revises: 0018
"""

from __future__ import annotations

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Migration 0001 creates from current ORM metadata, so fresh databases can
    # already contain this column and constraint when they reach 0019.
    op.execute("ALTER TABLE food_categories ADD COLUMN IF NOT EXISTS parent_source TEXT")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_food_categories_category_parent_source_supported'
            ) THEN
                ALTER TABLE food_categories
                ADD CONSTRAINT ck_food_categories_category_parent_source_supported
                CHECK (
                    parent_source IS NULL OR parent_source IN
                    ('INFERRED', 'OPEN_FOOD_FACTS_TAXONOMY', 'MANUAL')
                );
            END IF;
        END
        $$
        """
    )
    op.execute(
        """
        UPDATE food_categories
        SET parent_source = CASE
            WHEN id LIKE 'local:%' THEN 'MANUAL'
            ELSE 'INFERRED'
        END
        WHERE parent_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE food_categories DROP CONSTRAINT IF EXISTS "
        "ck_food_categories_category_parent_source_supported"
    )
    op.execute("ALTER TABLE food_categories DROP COLUMN IF EXISTS parent_source")
