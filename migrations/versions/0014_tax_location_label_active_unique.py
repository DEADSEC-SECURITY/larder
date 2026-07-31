"""Free deactivated tax-location labels for reuse.

Removing a location only deactivates it, so the plain unique constraint on
label blocked re-creating a location with the same name. Replace it with a
partial unique index over active rows.

Revision ID: 0014
Revises: 0013
"""

from __future__ import annotations

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tax_locations DROP CONSTRAINT IF EXISTS uq_tax_locations_label")
    op.execute("DROP INDEX IF EXISTS uq_tax_locations_label")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_tax_locations_label_active "
        "ON tax_locations (label) WHERE active"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_tax_locations_label_active")
    op.create_unique_constraint("uq_tax_locations_label", "tax_locations", ["label"])
