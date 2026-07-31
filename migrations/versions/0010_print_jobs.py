"""Add durable edge print jobs.

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "print_jobs" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "print_jobs",
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("subtitle", sa.Text(), nullable=True),
        sa.Column("copies", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.Text(), server_default="PENDING", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("claimed_by", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "copies BETWEEN 1 AND 20", name=op.f("ck_print_jobs_print_job_copies_range")
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name=op.f("ck_print_jobs_print_job_attempts_nonnegative")
        ),
        sa.CheckConstraint(
            "kind IN ('PRODUCT', 'CONTROL', 'GENERIC')",
            name=op.f("ck_print_jobs_print_job_kind_supported"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PRINTING', 'PRINTED', 'FAILED')",
            name=op.f("ck_print_jobs_print_job_status_supported"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_print_jobs")),
    )
    op.create_index(
        "ix_print_jobs_claim",
        "print_jobs",
        ["status", "available_at", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_print_jobs_claim", table_name="print_jobs")
    op.drop_table("print_jobs")
