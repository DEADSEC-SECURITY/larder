"""Add scanner intake batches for the live add-item prompt.

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Migration 0001 intentionally uses current Base.metadata with checkfirst,
    # so a brand-new database may already contain this table and column before
    # Alembic reaches 0011. Production upgrades from 0010 take the creation path.
    if "intake_batches" in sa.inspect(op.get_bind()).get_table_names():
        op.execute("DROP TRIGGER IF EXISTS larder_notify_intake_batches ON intake_batches")
        op.execute(
            """
            CREATE TRIGGER larder_notify_intake_batches
            AFTER INSERT OR UPDATE ON intake_batches
            FOR EACH STATEMENT EXECUTE FUNCTION larder_notify_change()
            """
        )
        return
    op.create_table(
        "intake_batches",
        sa.Column("scanner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_barcode", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="OPEN", nullable=False),
        sa.Column("quantity", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "last_scan_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'CONFIRMED', 'DISMISSED')",
            name=op.f("ck_intake_batches_status_supported"),
        ),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_intake_batches_quantity_positive")),
        sa.CheckConstraint(
            "(status = 'OPEN' AND finalized_at IS NULL) OR "
            "(status IN ('CONFIRMED', 'DISMISSED') AND finalized_at IS NOT NULL)",
            name=op.f("ck_intake_batches_finalized_state_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["product_barcode"],
            ["products.barcode"],
            name=op.f("fk_intake_batches_product_barcode_products"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scanner_id"],
            ["scanners.id"],
            name=op.f("fk_intake_batches_scanner_id_scanners"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_intake_batches")),
    )
    op.create_index(
        "ix_intake_batches_open",
        "intake_batches",
        ["scanner_id", "last_scan_at"],
        unique=False,
        postgresql_where=sa.text("status = 'OPEN'"),
    )
    op.add_column(
        "inventory_items",
        sa.Column("intake_batch_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_inventory_items_intake_batch_id_intake_batches"),
        "inventory_items",
        "intake_batches",
        ["intake_batch_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_inventory_items_intake_batch_id"),
        "inventory_items",
        ["intake_batch_id"],
        unique=False,
    )
    op.execute("DROP TRIGGER IF EXISTS larder_notify_intake_batches ON intake_batches")
    op.execute(
        """
        CREATE TRIGGER larder_notify_intake_batches
        AFTER INSERT OR UPDATE ON intake_batches
        FOR EACH STATEMENT EXECUTE FUNCTION larder_notify_change()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS larder_notify_intake_batches ON intake_batches")
    op.drop_index(op.f("ix_inventory_items_intake_batch_id"), table_name="inventory_items")
    op.drop_constraint(
        op.f("fk_inventory_items_intake_batch_id_intake_batches"),
        "inventory_items",
        type_="foreignkey",
    )
    op.drop_column("inventory_items", "intake_batch_id")
    op.drop_index("ix_intake_batches_open", table_name="intake_batches")
    op.drop_table("intake_batches")
