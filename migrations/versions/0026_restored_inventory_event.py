"""Add the RESTORED inventory event type for undoing remove/waste.

Revision ID: 0026
Revises: 0025
"""

from __future__ import annotations

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL requires an enum value to be committed before rows can use it.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE inventory_event_type ADD VALUE IF NOT EXISTS 'RESTORED'")


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed safely in place.
    pass
