from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from larder.models.base import Base, TimestampMixin
from larder.models.enums import EnrichmentStatus
from larder.models.types import enrichment_status_type


class FoodCategory(TimestampMixin, Base):
    __tablename__ = "food_categories"
    __table_args__ = (
        CheckConstraint(
            "default_expiration_days IS NULL OR default_expiration_days >= 0",
            name="expiration_days_nonnegative",
        ),
        CheckConstraint("expiration_basis = 'DATE_ADDED'", name="expiration_basis_supported"),
        CheckConstraint(
            "parent_source IS NULL OR parent_source IN "
            "('INFERRED', 'OPEN_FOOD_FACTS_TAXONOMY', 'UPCITEMDB', 'MANUAL')",
            name="category_parent_source_supported",
        ),
        CheckConstraint(
            "product_kind IN ('FOOD', 'HOUSEHOLD', 'PERSONAL_CARE', 'MEDICAL', "
            "'PET', 'OFFICE', 'OTHER')",
            name="category_product_kind_supported",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("food_categories.id", ondelete="SET NULL")
    )
    parent_source: Mapped[str | None] = mapped_column(Text)
    product_kind: Mapped[str] = mapped_column(
        Text, default="FOOD", server_default="FOOD", nullable=False
    )
    default_storage_location_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("storage_locations.id", ondelete="SET NULL")
    )
    default_expiration_days: Mapped[int | None] = mapped_column(Integer)
    expiration_basis: Mapped[str] = mapped_column(
        Text, default="DATE_ADDED", server_default="DATE_ADDED", nullable=False
    )
    active: Mapped[bool] = mapped_column(default=True, server_default="true", nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Product(TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint(
            "product_kind IN ('FOOD', 'HOUSEHOLD', 'PERSONAL_CARE', 'MEDICAL', "
            "'PET', 'OFFICE', 'OTHER')",
            name="product_kind_supported",
        ),
    )

    barcode: Mapped[str] = mapped_column(Text, primary_key=True)
    barcode_format: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(Text)
    product_kind: Mapped[str] = mapped_column(
        Text, default="FOOD", server_default="FOOD", nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text)
    manufacturer: Mapped[str | None] = mapped_column(Text)
    package_size: Mapped[str | None] = mapped_column(Text)
    material: Mapped[str | None] = mapped_column(Text)
    variant: Mapped[str | None] = mapped_column(Text)
    identification_source: Mapped[str | None] = mapped_column(Text)
    field_sources: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    provider_metadata: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    default_storage_location_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("storage_locations.id", ondelete="SET NULL")
    )
    ingredients_text: Mapped[str | None] = mapped_column(Text)
    quantity_text: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    image_source: Mapped[str | None] = mapped_column(Text)
    off_product_url: Mapped[str | None] = mapped_column(Text)
    off_last_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enrichment_status: Mapped[EnrichmentStatus] = mapped_column(
        enrichment_status_type,
        default=EnrichmentStatus.PENDING,
        server_default=EnrichmentStatus.PENDING.value,
        nullable=False,
    )
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_quality_notes: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    raw_source_payload: Mapped[dict | None] = mapped_column(JSONB)
    # Products with inventory history cannot be hard-deleted (append-only
    # ledger); the web UI archives them instead.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProductProviderRecord(Base):
    __tablename__ = "product_provider_records"

    product_barcode: Mapped[str] = mapped_column(
        ForeignKey("products.barcode", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    product_type: Mapped[str | None] = mapped_column(Text)
    disposition: Mapped[str] = mapped_column(Text, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProductNutrition(Base):
    __tablename__ = "product_nutrition"
    __table_args__ = (
        CheckConstraint(
            "basis_unit IN ('g', 'ml')",
            name="nutrition_basis_unit_supported",
        ),
        CheckConstraint(
            "serving_amount IS NULL OR serving_amount > 0",
            name="nutrition_serving_amount_positive",
        ),
        CheckConstraint(
            "serving_unit IS NULL OR serving_unit IN ('g', 'ml')",
            name="nutrition_serving_unit_supported",
        ),
    )

    product_barcode: Mapped[str] = mapped_column(
        ForeignKey("products.barcode", ondelete="CASCADE"), primary_key=True
    )
    energy_kcal_100g: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    fat_g_100g: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    saturated_fat_g_100g: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    carbohydrates_g_100g: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    sugars_g_100g: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    fiber_g_100g: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    protein_g_100g: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    salt_g_100g: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    sodium_g_100g: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    basis_unit: Mapped[str] = mapped_column(Text, default="g", server_default="g", nullable=False)
    serving_size_text: Mapped[str | None] = mapped_column(Text)
    serving_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    serving_unit: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


for _column in (
    "energy_kcal_100g",
    "fat_g_100g",
    "saturated_fat_g_100g",
    "carbohydrates_g_100g",
    "sugars_g_100g",
    "fiber_g_100g",
    "protein_g_100g",
    "salt_g_100g",
    "sodium_g_100g",
):
    ProductNutrition.__table__.append_constraint(
        CheckConstraint(f"{_column} IS NULL OR {_column} >= 0", name=f"{_column}_nonnegative")
    )


class ProductCategory(Base):
    __tablename__ = "product_categories"
    __table_args__ = (
        Index(
            "uq_product_categories_primary",
            "product_barcode",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )

    product_barcode: Mapped[str] = mapped_column(
        ForeignKey("products.barcode", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[str] = mapped_column(
        ForeignKey("food_categories.id", ondelete="CASCADE"), primary_key=True
    )
    source_rank: Mapped[int | None] = mapped_column(Integer)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
