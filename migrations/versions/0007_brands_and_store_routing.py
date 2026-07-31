"""Canonical brands and store-routing rules.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

import re
import unicodedata
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

SEPARATOR = re.compile(r"[,;|]+")
NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


def _clean(value: str) -> str:
    without_marks = value.translate(str.maketrans("", "", "™®©"))
    return " ".join(unicodedata.normalize("NFKC", without_marks).split()).strip()


def _normalize(value: str) -> str:
    return " ".join(NON_WORD.sub(" ", _clean(value).casefold()).split())


def _parts(value: str) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for part in SEPARATOR.split(value):
        name = _clean(part)
        normalized = _normalize(name)
        if name and normalized and normalized not in seen:
            output.append(name)
            seen.add(normalized)
    return output


def _install_notifications() -> None:
    for table, events in {
        "brands": "INSERT OR UPDATE",
        "brand_aliases": "INSERT OR UPDATE OR DELETE",
        "product_brands": "INSERT OR UPDATE OR DELETE",
        "store_routing_rules": "INSERT OR UPDATE OR DELETE",
    }.items():
        op.execute(f"DROP TRIGGER IF EXISTS larder_notify_{table} ON {table}")
        op.execute(
            f"CREATE TRIGGER larder_notify_{table} AFTER {events} ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION larder_notify_change()"
        )


def upgrade() -> None:
    # Revision 0001 intentionally calls current Base.metadata.create_all(). On a
    # brand-new database that means these tables already exist before Alembic
    # reaches this revision; an upgrade from 0006 still needs them created here.
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    feature_tables = {"brands", "brand_aliases", "product_brands", "store_routing_rules"}
    if feature_tables.issubset(existing_tables):
        _install_notifications()
        return

    op.create_table(
        "brands",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brands")),
        sa.UniqueConstraint("normalized_name", name=op.f("uq_brands_normalized_name")),
    )
    op.create_table(
        "brand_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["brand_id"],
            ["brands.id"],
            name=op.f("fk_brand_aliases_brand_id_brands"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brand_aliases")),
    )
    op.create_index(
        "uq_brand_aliases_normalized_name", "brand_aliases", ["normalized_name"], unique=True
    )
    op.create_table(
        "product_brands",
        sa.Column("product_barcode", sa.Text(), nullable=False),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_rank", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["brand_id"],
            ["brands.id"],
            name=op.f("fk_product_brands_brand_id_brands"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_barcode"],
            ["products.barcode"],
            name=op.f("fk_product_brands_product_barcode_products"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("product_barcode", "brand_id", name=op.f("pk_product_brands")),
    )
    op.create_index(
        "uq_product_brands_primary",
        "product_brands",
        ["product_barcode"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )
    op.create_table(
        "store_routing_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("match_type", sa.Text(), nullable=False),
        sa.Column("product_barcode", sa.Text(), nullable=True),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("category_id", sa.Text(), nullable=True),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("behavior", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "match_type IN ('PRODUCT', 'BRAND', 'CATEGORY')",
            name=op.f("ck_store_routing_rules_store_routing_match_type_supported"),
        ),
        sa.CheckConstraint(
            "behavior IN ('PREFER', 'REQUIRE')",
            name=op.f("ck_store_routing_rules_store_routing_behavior_supported"),
        ),
        sa.CheckConstraint(
            "priority BETWEEN -1000 AND 1000",
            name=op.f("ck_store_routing_rules_store_routing_priority_range"),
        ),
        sa.CheckConstraint(
            "(match_type = 'PRODUCT' AND product_barcode IS NOT NULL "
            "AND brand_id IS NULL AND category_id IS NULL) OR "
            "(match_type = 'BRAND' AND product_barcode IS NULL "
            "AND brand_id IS NOT NULL AND category_id IS NULL) OR "
            "(match_type = 'CATEGORY' AND product_barcode IS NULL "
            "AND brand_id IS NULL AND category_id IS NOT NULL)",
            name=op.f("ck_store_routing_rules_store_routing_exactly_one_matcher"),
        ),
        sa.ForeignKeyConstraint(
            ["brand_id"],
            ["brands.id"],
            name=op.f("fk_store_routing_rules_brand_id_brands"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["food_categories.id"],
            name=op.f("fk_store_routing_rules_category_id_food_categories"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_barcode"],
            ["products.barcode"],
            name=op.f("fk_store_routing_rules_product_barcode_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name=op.f("fk_store_routing_rules_store_id_stores"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_store_routing_rules")),
    )
    op.create_index(
        "uq_store_routing_product",
        "store_routing_rules",
        ["product_barcode"],
        unique=True,
        postgresql_where=sa.text("match_type = 'PRODUCT'"),
    )
    op.create_index(
        "uq_store_routing_brand",
        "store_routing_rules",
        ["brand_id"],
        unique=True,
        postgresql_where=sa.text("match_type = 'BRAND'"),
    )
    op.create_index(
        "uq_store_routing_category",
        "store_routing_rules",
        ["category_id"],
        unique=True,
        postgresql_where=sa.text("match_type = 'CATEGORY'"),
    )
    op.create_index(
        "ix_store_routing_rules_enabled_priority",
        "store_routing_rules",
        ["enabled", "priority"],
        unique=False,
    )

    bind = op.get_bind()
    brand_ids: dict[str, object] = {}
    rows = bind.execute(
        sa.text(
            "SELECT barcode, brand FROM products WHERE brand IS NOT NULL AND btrim(brand) <> ''"
        )
    ).mappings()
    for row in rows:
        for rank, name in enumerate(_parts(row["brand"])):
            normalized = _normalize(name)
            brand_id = brand_ids.get(normalized)
            if brand_id is None:
                brand_id = uuid4()
                brand_ids[normalized] = brand_id
                bind.execute(
                    sa.text(
                        "INSERT INTO brands "
                        "(id, name, normalized_name, active, created_at, updated_at) "
                        "VALUES (:id, :name, :normalized, true, now(), now())"
                    ),
                    {"id": brand_id, "name": name, "normalized": normalized},
                )
            bind.execute(
                sa.text(
                    "INSERT INTO product_brands "
                    "(product_barcode, brand_id, is_primary, source, source_rank) "
                    "VALUES (:barcode, :brand_id, :primary, 'MIGRATION', :rank)"
                ),
                {
                    "barcode": row["barcode"],
                    "brand_id": brand_id,
                    "primary": rank == 0,
                    "rank": rank,
                },
            )

    _install_notifications()


def downgrade() -> None:
    for table in ("store_routing_rules", "product_brands", "brand_aliases", "brands"):
        op.execute(f"DROP TRIGGER IF EXISTS larder_notify_{table} ON {table}")
    op.drop_table("store_routing_rules")
    op.drop_table("product_brands")
    op.drop_table("brand_aliases")
    op.drop_table("brands")
