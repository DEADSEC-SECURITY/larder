from __future__ import annotations

from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from larder.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Brand(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "brands"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )


class BrandAlias(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "brand_aliases"
    __table_args__ = (Index("uq_brand_aliases_normalized_name", "normalized_name", unique=True),)

    brand_id: Mapped[UUID] = mapped_column(
        ForeignKey("brands.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)


class ProductBrand(Base):
    __tablename__ = "product_brands"
    __table_args__ = (
        Index(
            "uq_product_brands_primary",
            "product_barcode",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )

    product_barcode: Mapped[str] = mapped_column(
        ForeignKey("products.barcode", ondelete="CASCADE"), primary_key=True
    )
    brand_id: Mapped[UUID] = mapped_column(
        ForeignKey("brands.id", ondelete="RESTRICT"), primary_key=True
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_rank: Mapped[int | None] = mapped_column(Integer)


class StoreRoutingRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "store_routing_rules"
    __table_args__ = (
        CheckConstraint(
            "match_type IN ('PRODUCT', 'BRAND', 'CATEGORY')",
            name="store_routing_match_type_supported",
        ),
        CheckConstraint(
            "behavior IN ('PREFER', 'REQUIRE')",
            name="store_routing_behavior_supported",
        ),
        CheckConstraint(
            "priority BETWEEN -1000 AND 1000",
            name="store_routing_priority_range",
        ),
        CheckConstraint(
            "(match_type = 'PRODUCT' AND product_barcode IS NOT NULL "
            "AND brand_id IS NULL AND category_id IS NULL) OR "
            "(match_type = 'BRAND' AND product_barcode IS NULL "
            "AND brand_id IS NOT NULL AND category_id IS NULL) OR "
            "(match_type = 'CATEGORY' AND product_barcode IS NULL "
            "AND brand_id IS NULL AND category_id IS NOT NULL)",
            name="store_routing_exactly_one_matcher",
        ),
        Index(
            "uq_store_routing_product",
            "product_barcode",
            unique=True,
            postgresql_where=text("match_type = 'PRODUCT'"),
        ),
        Index(
            "uq_store_routing_brand",
            "brand_id",
            unique=True,
            postgresql_where=text("match_type = 'BRAND'"),
        ),
        Index(
            "uq_store_routing_category",
            "category_id",
            unique=True,
            postgresql_where=text("match_type = 'CATEGORY'"),
        ),
        Index("ix_store_routing_rules_enabled_priority", "enabled", "priority"),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    match_type: Mapped[str] = mapped_column(Text, nullable=False)
    product_barcode: Mapped[str | None] = mapped_column(
        ForeignKey("products.barcode", ondelete="CASCADE")
    )
    brand_id: Mapped[UUID | None] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"))
    category_id: Mapped[str | None] = mapped_column(
        ForeignKey("food_categories.id", ondelete="CASCADE")
    )
    store_id: Mapped[UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False
    )
    behavior: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
