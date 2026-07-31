"""Add the scanner mode that places products on the grocery list.

Revision ID: 0021
Revises: 0020
"""

from __future__ import annotations

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL requires an enum value to be committed before rows can use it.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE scanner_mode ADD VALUE IF NOT EXISTS 'LIST'")
    op.execute(
        """
        INSERT INTO control_barcodes
            (payload, command_type, mode, enabled, description)
        VALUES
            ('CMD:MODE:LIST', 'SET_MODE', 'LIST', true, 'Add To List')
        ON CONFLICT (payload) DO UPDATE SET
            command_type = EXCLUDED.command_type,
            mode = EXCLUDED.mode,
            enabled = true,
            description = EXCLUDED.description
        """
    )


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed safely in place.
    op.execute("DELETE FROM control_barcodes WHERE payload = 'CMD:MODE:LIST'")
