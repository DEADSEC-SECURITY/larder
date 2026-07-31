"""Scanner control barcodes that set the active storage location.

CMD:LOC:<code> puts a storage location into the scanner context (like
CMD:STORE:<code> does for stores); ADD-mode scans then store new units there
instead of the product/category default. CMD:LOC:CLEAR drops it.

Revision ID: 0024
Revises: 0023
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Migration 0001 creates tables straight from the current models, so on a
    # fresh database these columns/constraints already exist — only patch
    # databases that predate them.
    inspector = sa.inspect(op.get_bind())

    context_columns = {c["name"] for c in inspector.get_columns("scanner_contexts")}
    if "storage_location_id" not in context_columns:
        op.add_column(
            "scanner_contexts",
            sa.Column(
                "storage_location_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("storage_locations.id", ondelete="RESTRICT"),
                nullable=True,
            ),
        )
        op.add_column(
            "scanner_contexts",
            sa.Column(
                "storage_location_expires_at", sa.DateTime(timezone=True), nullable=True
            ),
        )
        op.create_check_constraint(
            "storage_location_expiry_paired",
            "scanner_contexts",
            "(storage_location_id IS NULL) = (storage_location_expires_at IS NULL)",
        )

    control_columns = {c["name"] for c in inspector.get_columns("control_barcodes")}
    if "storage_location_id" not in control_columns:
        op.add_column(
            "control_barcodes",
            sa.Column(
                "storage_location_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("storage_locations.id", ondelete="CASCADE"),
                nullable=True,
            ),
        )
        op.drop_constraint("command_type_valid", "control_barcodes", type_="check")
        op.create_check_constraint(
            "command_type_valid",
            "control_barcodes",
            "command_type IN ('SET_MODE', 'CLEAR_MODE', 'SET_STORE', 'CLEAR_STORE', "
            "'SET_LOCATION', 'CLEAR_LOCATION')",
        )
        op.drop_constraint("command_target_valid", "control_barcodes", type_="check")
        op.create_check_constraint(
            "command_target_valid",
            "control_barcodes",
            "(command_type = 'SET_MODE' AND mode IS NOT NULL AND store_id IS NULL "
            "AND storage_location_id IS NULL) OR "
            "(command_type = 'SET_STORE' AND mode IS NULL AND store_id IS NOT NULL "
            "AND storage_location_id IS NULL) OR "
            "(command_type = 'SET_LOCATION' AND mode IS NULL AND store_id IS NULL "
            "AND storage_location_id IS NOT NULL) OR "
            "(command_type IN ('CLEAR_MODE', 'CLEAR_STORE', 'CLEAR_LOCATION') "
            "AND mode IS NULL AND store_id IS NULL AND storage_location_id IS NULL)",
        )

    op.execute(
        """
        INSERT INTO control_barcodes (payload, command_type, mode, enabled, description)
        VALUES ('CMD:LOC:CLEAR', 'CLEAR_LOCATION', NULL, true, 'Clear storage location context')
        ON CONFLICT (payload) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM control_barcodes "
        "WHERE command_type IN ('SET_LOCATION', 'CLEAR_LOCATION')"
    )
    op.drop_constraint("command_target_valid", "control_barcodes", type_="check")
    op.create_check_constraint(
        "command_target_valid",
        "control_barcodes",
        "(command_type = 'SET_MODE' AND mode IS NOT NULL AND store_id IS NULL) OR "
        "(command_type = 'SET_STORE' AND mode IS NULL AND store_id IS NOT NULL) OR "
        "(command_type IN ('CLEAR_MODE', 'CLEAR_STORE') AND mode IS NULL AND store_id IS NULL)",
    )
    op.drop_constraint("command_type_valid", "control_barcodes", type_="check")
    op.create_check_constraint(
        "command_type_valid",
        "control_barcodes",
        "command_type IN ('SET_MODE', 'CLEAR_MODE', 'SET_STORE', 'CLEAR_STORE')",
    )
    op.drop_column("control_barcodes", "storage_location_id")
    op.drop_constraint("storage_location_expiry_paired", "scanner_contexts", type_="check")
    op.drop_column("scanner_contexts", "storage_location_expires_at")
    op.drop_column("scanner_contexts", "storage_location_id")
