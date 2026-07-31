"""Add server-side web sessions for Authentik sign-in.

Revision ID: 0027
Revises: 0026
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Migration 0001 runs create_all from the current models, so a fresh
    # database already has this table.
    inspector = sa.inspect(op.get_bind())
    if "web_sessions" in set(inspector.get_table_names()):
        return

    op.create_table(
        "web_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column(
            "claims",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("refresh_token", sa.Text()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revalidate_after", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("user_agent", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_web_sessions"),
    )
    op.create_index("ix_web_sessions_subject", "web_sessions", ["subject"])
    op.create_index("ix_web_sessions_expires_at", "web_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_web_sessions_expires_at", table_name="web_sessions")
    op.drop_index("ix_web_sessions_subject", table_name="web_sessions")
    op.drop_table("web_sessions")
