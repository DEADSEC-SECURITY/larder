"""Dedupe tax locations by address instead of label.

Labels become renamable display names; what makes an active location unique
is its address (street + city + state + ZIP, case-insensitive).

Revision ID: 0015
Revises: 0014
"""

from __future__ import annotations

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_tax_locations_label_active")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_tax_locations_address_active "
        "ON tax_locations (lower(coalesce(address_line1, '')), lower(city), "
        "lower(state), postal_code) WHERE active"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_tax_locations_address_active")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_tax_locations_label_active "
        "ON tax_locations (label) WHERE active"
    )
