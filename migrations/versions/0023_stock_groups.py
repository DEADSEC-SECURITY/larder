"""Add interchangeable stock groups and automated match decisions.

Revision ID: 0023
Revises: 0022
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

KINDS = "'FOOD', 'HOUSEHOLD', 'PERSONAL_CARE', 'MEDICAL', 'PET', 'OFFICE', 'OTHER'"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "stock_groups" not in tables:
        op.create_table(
            "stock_groups",
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("canonical_key", sa.Text(), nullable=False),
            sa.Column("product_kind", sa.Text(), nullable=False),
            sa.Column("representative_product_barcode", sa.Text()),
            sa.Column(
                "required_terms",
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'[]'::jsonb"),
                nullable=False,
            ),
            sa.Column(
                "excluded_terms",
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'[]'::jsonb"),
                nullable=False,
            ),
            sa.Column(
                "allowed_forms",
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'[]'::jsonb"),
                nullable=False,
            ),
            sa.Column(
                "category_ids",
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'[]'::jsonb"),
                nullable=False,
            ),
            sa.Column("ignore_brand", sa.Boolean(), server_default="true", nullable=False),
            sa.Column(
                "ignore_package_size", sa.Boolean(), server_default="true", nullable=False
            ),
            sa.Column(
                "auto_match_threshold",
                sa.Numeric(5, 4),
                server_default="0.90",
                nullable=False,
            ),
            sa.Column(
                "review_threshold",
                sa.Numeric(5, 4),
                server_default="0.65",
                nullable=False,
            ),
            sa.Column("created_source", sa.Text(), server_default="USER", nullable=False),
            sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint(
                f"product_kind IN ({KINDS})",
                name=op.f("ck_stock_groups_product_kind_supported"),
            ),
            sa.CheckConstraint(
                "review_threshold >= 0 AND review_threshold <= 1",
                name=op.f("ck_stock_groups_review_threshold_fraction"),
            ),
            sa.CheckConstraint(
                "auto_match_threshold >= 0 AND auto_match_threshold <= 1",
                name=op.f("ck_stock_groups_auto_threshold_fraction"),
            ),
            sa.CheckConstraint(
                "auto_match_threshold >= review_threshold",
                name=op.f("ck_stock_groups_threshold_order"),
            ),
            sa.CheckConstraint(
                "created_source IN ('USER', 'AUTOMATIC', 'MIGRATION')",
                name=op.f("ck_stock_groups_created_source_supported"),
            ),
            sa.ForeignKeyConstraint(
                ["representative_product_barcode"],
                ["products.barcode"],
                name=op.f(
                    "fk_stock_groups_representative_product_barcode_products"
                ),
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_groups")),
        )

    stock_group_indexes = {
        row["name"] for row in sa.inspect(op.get_bind()).get_indexes("stock_groups")
    }
    if "ix_stock_groups_kind_canonical_key" not in stock_group_indexes:
        op.create_index(
            "ix_stock_groups_kind_canonical_key",
            "stock_groups",
            ["product_kind", "canonical_key"],
        )

    if "stock_group_products" not in tables:
        op.create_table(
            "stock_group_products",
            sa.Column("stock_group_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("product_barcode", sa.Text(), nullable=False),
            sa.Column("source", sa.Text(), nullable=False),
            sa.Column("confidence", sa.Numeric(5, 4)),
            sa.Column(
                "evidence",
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'{}'::jsonb"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint(
                "source IN ('USER', 'AUTOMATIC', 'MIGRATION')",
                name=op.f("ck_stock_group_products_source_supported"),
            ),
            sa.CheckConstraint(
                "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
                name=op.f("ck_stock_group_products_confidence_fraction"),
            ),
            sa.ForeignKeyConstraint(
                ["stock_group_id"],
                ["stock_groups.id"],
                name=op.f("fk_stock_group_products_stock_group_id_stock_groups"),
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["product_barcode"],
                ["products.barcode"],
                name=op.f("fk_stock_group_products_product_barcode_products"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint(
                "stock_group_id", "product_barcode", name=op.f("pk_stock_group_products")
            ),
            sa.UniqueConstraint(
                "product_barcode", name=op.f("uq_stock_group_products_product_one_group")
            ),
        )

    if "stock_group_candidates" not in tables:
        op.create_table(
            "stock_group_candidates",
            sa.Column("stock_group_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("product_barcode", sa.Text(), nullable=False),
            sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
            sa.Column("status", sa.Text(), server_default="PENDING", nullable=False),
            sa.Column(
                "evidence",
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'{}'::jsonb"),
                nullable=False,
            ),
            sa.Column(
                "evaluated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("decided_at", sa.DateTime(timezone=True)),
            sa.CheckConstraint(
                "status IN ('PENDING', 'REJECTED')",
                name=op.f("ck_stock_group_candidates_status_supported"),
            ),
            sa.CheckConstraint(
                "confidence >= 0 AND confidence <= 1",
                name=op.f("ck_stock_group_candidates_confidence_fraction"),
            ),
            sa.ForeignKeyConstraint(
                ["stock_group_id"],
                ["stock_groups.id"],
                name=op.f("fk_stock_group_candidates_stock_group_id_stock_groups"),
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["product_barcode"],
                ["products.barcode"],
                name=op.f("fk_stock_group_candidates_product_barcode_products"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint(
                "stock_group_id",
                "product_barcode",
                name=op.f("pk_stock_group_candidates"),
            ),
        )

    if "stock_group_policies" not in tables:
        op.create_table(
            "stock_group_policies",
            sa.Column("stock_group_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("reorder_point", sa.Integer(), nullable=False),
            sa.Column("target_stock", sa.Integer(), nullable=False),
            sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint(
                "reorder_point >= 0",
                name=op.f("ck_stock_group_policies_reorder_point_nonnegative"),
            ),
            sa.CheckConstraint(
                "target_stock >= reorder_point",
                name=op.f("ck_stock_group_policies_target_at_least_reorder"),
            ),
            sa.ForeignKeyConstraint(
                ["stock_group_id"],
                ["stock_groups.id"],
                name=op.f("fk_stock_group_policies_stock_group_id_stock_groups"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("stock_group_id", name=op.f("pk_stock_group_policies")),
        )

    if "stock_group_shopping_items" not in tables:
        op.create_table(
            "stock_group_shopping_items",
            sa.Column("stock_group_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("automatic_quantity", sa.Integer(), server_default="0", nullable=False),
            sa.Column("manual_quantity", sa.Integer(), server_default="0", nullable=False),
            sa.Column("checked_quantity", sa.Integer(), server_default="0", nullable=False),
            sa.Column("preferred_store_id", postgresql.UUID(as_uuid=True)),
            sa.Column("auto_reason", sa.Text()),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint(
                "automatic_quantity >= 0",
                name=op.f(
                    "ck_stock_group_shopping_items_automatic_quantity_nonnegative"
                ),
            ),
            sa.CheckConstraint(
                "manual_quantity >= 0",
                name=op.f("ck_stock_group_shopping_items_manual_quantity_nonnegative"),
            ),
            sa.CheckConstraint(
                "checked_quantity >= 0",
                name=op.f("ck_stock_group_shopping_items_checked_quantity_nonnegative"),
            ),
            sa.ForeignKeyConstraint(
                ["stock_group_id"],
                ["stock_groups.id"],
                name=op.f(
                    "fk_stock_group_shopping_items_stock_group_id_stock_groups"
                ),
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["preferred_store_id"],
                ["stores.id"],
                name=op.f("fk_stock_group_shopping_items_preferred_store_id_stores"),
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint(
                "stock_group_id", name=op.f("pk_stock_group_shopping_items")
            ),
        )

    for table in (
        "stock_groups",
        "stock_group_products",
        "stock_group_candidates",
        "stock_group_policies",
        "stock_group_shopping_items",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS larder_notify_{table} ON {table}")
        op.execute(
            f"CREATE TRIGGER larder_notify_{table} AFTER INSERT OR UPDATE OR DELETE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION larder_notify_change()"
        )


def downgrade() -> None:
    for table in (
        "stock_group_shopping_items",
        "stock_group_policies",
        "stock_group_candidates",
        "stock_group_products",
        "stock_groups",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS larder_notify_{table} ON {table}")
        op.drop_table(table)
