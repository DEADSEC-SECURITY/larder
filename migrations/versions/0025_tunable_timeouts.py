"""Persist user-tunable workflow and interface timing.

Revision ID: 0025
Revises: 0024
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


COLUMNS = (
    ("intake_popup_timeout_seconds", 30),
    ("intake_scan_group_timeout_seconds", 30),
    ("scanner_mode_timeout_seconds", 30),
    ("scanner_context_timeout_seconds", 1800),
    ("scanner_offline_after_seconds", 172800),
    ("toast_duration_seconds", 3),
    ("confirmation_timeout_seconds", 3),
    ("live_update_debounce_ms", 300),
    ("fallback_refresh_seconds", 60),
)

CHECKS = (
    (
        "intake_popup_timeout_seconds_bounded",
        "intake_popup_timeout_seconds BETWEEN 0 AND 3600",
    ),
    (
        "intake_scan_group_timeout_seconds_bounded",
        "intake_scan_group_timeout_seconds BETWEEN 1 AND 3600",
    ),
    (
        "scanner_mode_timeout_seconds_bounded",
        "scanner_mode_timeout_seconds BETWEEN 5 AND 3600",
    ),
    (
        "scanner_context_timeout_seconds_bounded",
        "scanner_context_timeout_seconds BETWEEN 30 AND 86400",
    ),
    (
        "scanner_offline_after_seconds_bounded",
        "scanner_offline_after_seconds BETWEEN 60 AND 604800",
    ),
    (
        "toast_duration_seconds_bounded",
        "toast_duration_seconds BETWEEN 1 AND 60",
    ),
    (
        "confirmation_timeout_seconds_bounded",
        "confirmation_timeout_seconds BETWEEN 1 AND 60",
    ),
    (
        "live_update_debounce_ms_bounded",
        "live_update_debounce_ms BETWEEN 50 AND 5000",
    ),
    (
        "fallback_refresh_seconds_bounded",
        "fallback_refresh_seconds BETWEEN 5 AND 3600",
    ),
)


def upgrade() -> None:
    # Migration 0001 creates tables from current metadata on fresh databases.
    # Only patch databases that predate these settings.
    inspector = sa.inspect(op.get_bind())
    existing_columns = {column["name"] for column in inspector.get_columns("household_settings")}
    for name, default in COLUMNS:
        if name not in existing_columns:
            op.add_column(
                "household_settings",
                sa.Column(
                    name,
                    sa.Integer(),
                    server_default=sa.text(str(default)),
                    nullable=False,
                ),
            )

    inspector = sa.inspect(op.get_bind())
    existing_checks = {
        constraint["name"] for constraint in inspector.get_check_constraints("household_settings")
    }
    for name, condition in CHECKS:
        full_name = f"ck_household_settings_{name}"
        if name not in existing_checks and full_name not in existing_checks:
            op.create_check_constraint(name, "household_settings", condition)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_checks = {
        constraint["name"] for constraint in inspector.get_check_constraints("household_settings")
    }
    for name, _condition in reversed(CHECKS):
        full_name = f"ck_household_settings_{name}"
        actual_name = full_name if full_name in existing_checks else name
        if actual_name in existing_checks:
            op.drop_constraint(op.f(actual_name), "household_settings", type_="check")

    existing_columns = {column["name"] for column in inspector.get_columns("household_settings")}
    for name, _default in reversed(COLUMNS):
        if name in existing_columns:
            op.drop_column("household_settings", name)
