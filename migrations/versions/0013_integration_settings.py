"""UI-editable integration credentials (ZipTax API key).

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("integration_settings"):
        op.create_table(
            "integration_settings",
            sa.Column("provider", sa.Text(), primary_key=True),
            sa.Column("api_key", sa.Text()),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )


def downgrade() -> None:
    op.drop_table("integration_settings")
