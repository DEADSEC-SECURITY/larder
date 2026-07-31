"""Add saved tax profiles and immutable tax snapshots on prices.

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def _install_notifications() -> None:
    for table in ("tax_locations", "tax_rates"):
        op.execute(f"DROP TRIGGER IF EXISTS larder_notify_{table} ON {table}")
        op.execute(
            f"CREATE TRIGGER larder_notify_{table} AFTER INSERT OR UPDATE OR DELETE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION larder_notify_change()"
        )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "tax_locations" not in tables:
        op.create_table(
            "tax_locations",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("label", sa.Text(), nullable=False),
            sa.Column("address_line1", sa.Text()),
            sa.Column("address_line2", sa.Text()),
            sa.Column("city", sa.Text(), nullable=False),
            sa.Column("state", sa.String(length=3), nullable=False),
            sa.Column("postal_code", sa.String(length=16), nullable=False),
            sa.Column("country_code", sa.String(length=3), server_default="USA", nullable=False),
            sa.Column("normalized_address", sa.Text()),
            sa.Column("provider", sa.Text(), server_default="MANUAL", nullable=False),
            sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
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
                "provider IN ('MANUAL', 'ZIPTAX')", name=op.f("ck_tax_locations_provider_supported")
            ),
            sa.CheckConstraint(
                "country_code ~ '^[A-Z]{2,3}$'", name=op.f("ck_tax_locations_country_code_format")
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_tax_locations")),
            sa.UniqueConstraint("label", name=op.f("uq_tax_locations_label")),
        )

    if "tax_rates" not in tables:
        op.create_table(
            "tax_rates",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tax_class", sa.String(length=32), nullable=False),
            sa.Column("label", sa.Text(), nullable=False),
            sa.Column("rate", sa.Numeric(8, 6), nullable=False),
            sa.Column("taxability_code", sa.String(length=16)),
            sa.Column("source", sa.Text(), nullable=False),
            sa.Column("fetched_at", sa.DateTime(timezone=True)),
            sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
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
            sa.CheckConstraint("rate >= 0 AND rate <= 1", name=op.f("ck_tax_rates_rate_fraction")),
            sa.CheckConstraint(
                "source IN ('MANUAL', 'ZIPTAX')", name=op.f("ck_tax_rates_source_supported")
            ),
            sa.ForeignKeyConstraint(
                ["location_id"],
                ["tax_locations.id"],
                name=op.f("fk_tax_rates_location_id_tax_locations"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_tax_rates")),
            sa.UniqueConstraint(
                "location_id", "tax_class", name=op.f("uq_tax_rates_location_tax_class")
            ),
        )

    inspector = sa.inspect(bind)

    settings_columns = {column["name"] for column in inspector.get_columns("household_settings")}
    if "default_tax_rate_id" not in settings_columns:
        op.add_column(
            "household_settings",
            sa.Column("default_tax_rate_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            op.f("fk_household_settings_default_tax_rate_id_tax_rates"),
            "household_settings",
            "tax_rates",
            ["default_tax_rate_id"],
            ["id"],
            ondelete="SET NULL",
        )

    inventory_columns = {column["name"] for column in inspector.get_columns("inventory_items")}
    inventory_specs = {
        "price_subtotal": sa.Numeric(12, 2),
        "tax_rate": sa.Numeric(8, 6),
        "tax_amount": sa.Numeric(12, 2),
        "tax_rate_id": postgresql.UUID(as_uuid=True),
        "tax_location_id": postgresql.UUID(as_uuid=True),
        "tax_class": sa.String(length=32),
        "tax_source": sa.String(length=16),
    }
    for name, column_type in inventory_specs.items():
        if name not in inventory_columns:
            op.add_column("inventory_items", sa.Column(name, column_type, nullable=True))
    if "tax_rate_id" not in inventory_columns:
        op.create_foreign_key(
            op.f("fk_inventory_items_tax_rate_id_tax_rates"),
            "inventory_items",
            "tax_rates",
            ["tax_rate_id"],
            ["id"],
            ondelete="SET NULL",
        )
    if "tax_location_id" not in inventory_columns:
        op.create_foreign_key(
            op.f("fk_inventory_items_tax_location_id_tax_locations"),
            "inventory_items",
            "tax_locations",
            ["tax_location_id"],
            ["id"],
            ondelete="SET NULL",
        )

    observation_columns = {
        column["name"] for column in inspector.get_columns("product_store_observations")
    }
    observation_specs = {
        "subtotal_price": sa.Numeric(12, 2),
        "tax_rate": sa.Numeric(8, 6),
        "tax_amount": sa.Numeric(12, 2),
        "tax_rate_id": postgresql.UUID(as_uuid=True),
        "tax_location_id": postgresql.UUID(as_uuid=True),
        "tax_class": sa.String(length=32),
        "tax_source": sa.String(length=16),
    }
    for name, column_type in observation_specs.items():
        if name not in observation_columns:
            op.add_column("product_store_observations", sa.Column(name, column_type, nullable=True))
    if "tax_rate_id" not in observation_columns:
        op.create_foreign_key(
            op.f("fk_product_store_observations_tax_rate_id_tax_rates"),
            "product_store_observations",
            "tax_rates",
            ["tax_rate_id"],
            ["id"],
            ondelete="SET NULL",
        )
    if "tax_location_id" not in observation_columns:
        op.create_foreign_key(
            op.f("fk_product_store_observations_tax_location_id_tax_locations"),
            "product_store_observations",
            "tax_locations",
            ["tax_location_id"],
            ["id"],
            ondelete="SET NULL",
        )

    _install_notifications()


def downgrade() -> None:
    for table in ("tax_rates", "tax_locations"):
        op.execute(f"DROP TRIGGER IF EXISTS larder_notify_{table} ON {table}")

    for table in ("product_store_observations", "inventory_items"):
        op.drop_constraint(
            op.f(f"fk_{table}_tax_location_id_tax_locations"), table, type_="foreignkey"
        )
        op.drop_constraint(op.f(f"fk_{table}_tax_rate_id_tax_rates"), table, type_="foreignkey")
        for column in (
            "tax_source",
            "tax_class",
            "tax_location_id",
            "tax_rate_id",
            "tax_amount",
            "tax_rate",
            "subtotal_price" if table == "product_store_observations" else "price_subtotal",
        ):
            op.drop_column(table, column)

    op.drop_constraint(
        op.f("fk_household_settings_default_tax_rate_id_tax_rates"),
        "household_settings",
        type_="foreignkey",
    )
    op.drop_column("household_settings", "default_tax_rate_id")
    op.drop_table("tax_rates")
    op.drop_table("tax_locations")
