"""Generalize the catalog from groceries to household inventory.

Revision ID: 0022
Revises: 0021
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

KINDS = "'FOOD', 'HOUSEHOLD', 'PERSONAL_CARE', 'MEDICAL', 'PET', 'OFFICE', 'OTHER'"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE inventory_event_type ADD VALUE IF NOT EXISTS 'STORAGE_CHANGED'")
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "storage_locations" not in tables:
        op.create_table(
            "storage_locations",
            sa.Column("code", sa.Text(), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "created_at", sa.DateTime(timezone=True),
                server_default=sa.func.now(), nullable=False,
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True),
                server_default=sa.func.now(), nullable=False,
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_storage_locations")),
            sa.UniqueConstraint("code", name=op.f("uq_storage_locations_code")),
            sa.UniqueConstraint("name", name=op.f("uq_storage_locations_name")),
        )
    op.execute(
        """
        INSERT INTO storage_locations (id, code, name) VALUES
          ('10000000-0000-0000-0000-000000000001', 'pantry', 'Pantry'),
          ('10000000-0000-0000-0000-000000000002', 'refrigerator', 'Refrigerator'),
          ('10000000-0000-0000-0000-000000000003', 'freezer', 'Freezer'),
          ('10000000-0000-0000-0000-000000000004', 'bathroom', 'Bathroom'),
          ('10000000-0000-0000-0000-000000000005', 'medicine-cabinet', 'Medicine Cabinet'),
          ('10000000-0000-0000-0000-000000000006', 'laundry-room', 'Laundry Room'),
          ('10000000-0000-0000-0000-000000000007', 'garage', 'Garage'),
          ('10000000-0000-0000-0000-000000000008', 'office', 'Office')
        ON CONFLICT (id) DO NOTHING
        """
    )

    product_columns = {column["name"] for column in inspector.get_columns("products")}
    for name, column_type in (
        ("product_kind", sa.Text()),
        ("description", sa.Text()),
        ("manufacturer", sa.Text()),
        ("package_size", sa.Text()),
        ("material", sa.Text()),
        ("variant", sa.Text()),
        ("identification_source", sa.Text()),
        ("field_sources", postgresql.JSONB(astext_type=sa.Text())),
        ("provider_metadata", postgresql.JSONB(astext_type=sa.Text())),
        ("default_storage_location_id", postgresql.UUID(as_uuid=True)),
    ):
        kwargs = {"nullable": True}
        if name == "product_kind":
            kwargs = {"nullable": False, "server_default": "FOOD"}
        elif name in {"field_sources", "provider_metadata"}:
            kwargs = {"nullable": False, "server_default": sa.text("'{}'::jsonb")}
        if name not in product_columns:
            op.add_column("products", sa.Column(name, column_type, **kwargs))
    op.execute("UPDATE products SET package_size = quantity_text WHERE package_size IS NULL")
    op.execute(
        """
        UPDATE products SET identification_source = CASE
          WHEN barcode_format = 'MANUAL' THEN 'MANUAL'
          WHEN raw_source_payload IS NOT NULL THEN 'OPEN_FACTS'
          ELSE NULL END
        """
    )
    product_checks = {row["name"] for row in inspector.get_check_constraints("products")}
    if op.f("ck_products_product_kind_supported") not in product_checks:
        op.create_check_constraint(
            "product_kind_supported", "products", f"product_kind IN ({KINDS})"
        )
    product_fks = inspector.get_foreign_keys("products")
    product_storage_fk = op.f(
        "fk_products_default_storage_location_id_storage_locations"
    )
    if not any(
        row.get("constrained_columns") == ["default_storage_location_id"]
        for row in product_fks
    ):
        op.create_foreign_key(
            product_storage_fk,
            "products", "storage_locations", ["default_storage_location_id"], ["id"],
            ondelete="SET NULL",
        )

    category_columns = {
        column["name"] for column in inspector.get_columns("food_categories")
    }
    if "product_kind" not in category_columns:
        op.add_column(
            "food_categories",
            sa.Column("product_kind", sa.Text(), server_default="FOOD", nullable=False),
        )
    if "default_storage_location_id" not in category_columns:
        op.add_column(
            "food_categories",
            sa.Column(
                "default_storage_location_id", postgresql.UUID(as_uuid=True), nullable=True
            ),
        )
    category_checks = {
        row["name"]: row.get("sqltext", "")
        for row in inspector.get_check_constraints("food_categories")
    }
    parent_check = op.f("ck_food_categories_category_parent_source_supported")
    if "UPCITEMDB" not in category_checks.get(parent_check, ""):
        if parent_check in category_checks:
            op.drop_constraint(parent_check, "food_categories", type_="check")
        op.create_check_constraint(
            "category_parent_source_supported",
            "food_categories",
            "parent_source IS NULL OR parent_source IN "
            "('INFERRED', 'OPEN_FOOD_FACTS_TAXONOMY', 'UPCITEMDB', 'MANUAL')",
        )
    kind_check = op.f("ck_food_categories_category_product_kind_supported")
    if kind_check not in category_checks:
        op.create_check_constraint(
            "category_product_kind_supported",
            "food_categories", f"product_kind IN ({KINDS})",
        )
    category_fks = inspector.get_foreign_keys("food_categories")
    category_storage_fk = op.f(
        "fk_food_categories_default_storage_location_id_storage_locations"
    )
    if not any(
        row.get("constrained_columns") == ["default_storage_location_id"]
        for row in category_fks
    ):
        op.create_foreign_key(
            category_storage_fk,
            "food_categories", "storage_locations",
            ["default_storage_location_id"], ["id"], ondelete="SET NULL",
        )

    inventory_columns = {
        column["name"] for column in inspector.get_columns("inventory_items")
    }
    if "storage_location_id" not in inventory_columns:
        op.add_column(
            "inventory_items",
            sa.Column("storage_location_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            op.f("fk_inventory_items_storage_location_id_storage_locations"),
            "inventory_items", "storage_locations", ["storage_location_id"], ["id"],
            ondelete="SET NULL",
        )

    if "product_provider_records" not in tables:
        op.create_table(
            "product_provider_records",
        sa.Column("product_barcode", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("product_type", sa.Text()),
        sa.Column("disposition", sa.Text(), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("error", sa.Text()),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["product_barcode"], ["products.barcode"],
            name=op.f("fk_product_provider_records_product_barcode_products"),
            ondelete="CASCADE",
        ),
            sa.PrimaryKeyConstraint(
                "product_barcode", "provider",
                name=op.f("pk_product_provider_records"),
            ),
        )

    # Sensible category defaults; users can override these in Settings.
    op.execute(
        """
        UPDATE food_categories SET default_storage_location_id = CASE
          WHEN lower(display_name) ~ 'frozen|ice cream'
            THEN '10000000-0000-0000-0000-000000000003'::uuid
          WHEN lower(display_name) ~
            'dairy|milk|cheese|meat|poultry|seafood|produce|fruit|vegetable'
            THEN '10000000-0000-0000-0000-000000000002'::uuid
          ELSE '10000000-0000-0000-0000-000000000001'::uuid END
        WHERE product_kind = 'FOOD'
        """
    )

    for table in ("storage_locations", "product_provider_records"):
        op.execute(f"DROP TRIGGER IF EXISTS larder_notify_{table} ON {table}")
        op.execute(
            f"CREATE TRIGGER larder_notify_{table} AFTER INSERT OR UPDATE OR DELETE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION larder_notify_change()"
        )


def downgrade() -> None:
    for table in ("storage_locations", "product_provider_records"):
        op.execute(f"DROP TRIGGER IF EXISTS larder_notify_{table} ON {table}")
    op.drop_table("product_provider_records")
    op.drop_constraint(
        op.f("fk_inventory_items_storage_location_id_storage_locations"),
        "inventory_items", type_="foreignkey",
    )
    op.drop_column("inventory_items", "storage_location_id")
    op.drop_constraint(
        op.f("fk_food_categories_default_storage_location_id_storage_locations"),
        "food_categories", type_="foreignkey",
    )
    op.drop_constraint(
        op.f("ck_food_categories_category_product_kind_supported"),
        "food_categories", type_="check",
    )
    op.drop_constraint(
        op.f("ck_food_categories_category_parent_source_supported"),
        "food_categories", type_="check",
    )
    op.create_check_constraint(
        "category_parent_source_supported", "food_categories",
        "parent_source IS NULL OR parent_source IN "
        "('INFERRED', 'OPEN_FOOD_FACTS_TAXONOMY', 'MANUAL')",
    )
    op.drop_column("food_categories", "default_storage_location_id")
    op.drop_column("food_categories", "product_kind")
    op.drop_constraint(op.f("ck_products_product_kind_supported"), "products", type_="check")
    op.drop_constraint(
        op.f("fk_products_default_storage_location_id_storage_locations"),
        "products", type_="foreignkey",
    )
    for name in (
        "default_storage_location_id", "provider_metadata", "field_sources",
        "identification_source", "variant", "material", "package_size",
        "manufacturer", "description", "product_kind",
    ):
        op.drop_column("products", name)
    op.drop_table("storage_locations")
