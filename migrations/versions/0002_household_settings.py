"""Household settings for the web UI.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("household_settings"):
        op.create_table(
            "household_settings",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("removal_policy", sa.Text(), server_default="FEFO", nullable=False),
            sa.Column("expiring_window_days", sa.Integer(), server_default="4", nullable=False),
            sa.Column("auto_grocery", sa.Boolean(), server_default="true", nullable=False),
            sa.Column(
                "require_store_on_manual_add", sa.Boolean(), server_default="false", nullable=False
            ),
            sa.Column("badge_expiring_nav", sa.Boolean(), server_default="true", nullable=False),
            sa.Column("retry_failed_lookups", sa.Boolean(), server_default="true", nullable=False),
            sa.Column(
                "audible_scan_confirmation", sa.Boolean(), server_default="false", nullable=False
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint("id = 'household'", name="ck_household_settings_single_row"),
            sa.CheckConstraint(
                "removal_policy IN ('FEFO', 'FIFO', 'LIFO', 'LEFO')",
                name="ck_household_settings_removal_policy_valid",
            ),
            sa.CheckConstraint(
                "expiring_window_days BETWEEN 1 AND 30",
                name="ck_household_settings_expiring_window_days_bounded",
            ),
        )
    op.execute(
        """
        INSERT INTO household_settings (id) VALUES ('household')
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("household_settings")
