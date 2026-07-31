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
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from larder.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

PRODUCT_KINDS = (
    "'FOOD', 'HOUSEHOLD', 'PERSONAL_CARE', 'MEDICAL', 'PET', 'OFFICE', 'OTHER'"
)


class StockGroup(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "stock_groups"
    __table_args__ = (
        Index("ix_stock_groups_kind_canonical_key", "product_kind", "canonical_key"),
        CheckConstraint(f"product_kind IN ({PRODUCT_KINDS})", name="product_kind_supported"),
        CheckConstraint(
            "review_threshold >= 0 AND review_threshold <= 1",
            name="review_threshold_fraction",
        ),
        CheckConstraint(
            "auto_match_threshold >= 0 AND auto_match_threshold <= 1",
            name="auto_threshold_fraction",
        ),
        CheckConstraint(
            "auto_match_threshold >= review_threshold",
            name="threshold_order",
        ),
        CheckConstraint(
            "created_source IN ('USER', 'AUTOMATIC', 'MIGRATION')",
            name="created_source_supported",
        ),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_key: Mapped[str] = mapped_column(Text, nullable=False)
    product_kind: Mapped[str] = mapped_column(Text, nullable=False)
    representative_product_barcode: Mapped[str | None] = mapped_column(
        ForeignKey("products.barcode", ondelete="SET NULL")
    )
    required_terms: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    excluded_terms: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    allowed_forms: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    category_ids: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    ignore_brand: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    ignore_package_size: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    auto_match_threshold: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), default=Decimal("0.90"), server_default="0.90", nullable=False
    )
    review_threshold: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), default=Decimal("0.65"), server_default="0.65", nullable=False
    )
    created_source: Mapped[str] = mapped_column(
        Text, default="USER", server_default="USER", nullable=False
    )
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )


class StockGroupProduct(TimestampMixin, Base):
    __tablename__ = "stock_group_products"
    __table_args__ = (
        UniqueConstraint("product_barcode", name="product_one_group"),
        CheckConstraint(
            "source IN ('USER', 'AUTOMATIC', 'MIGRATION')",
            name="source_supported",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_fraction",
        ),
    )

    stock_group_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("stock_groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    product_barcode: Mapped[str] = mapped_column(
        ForeignKey("products.barcode", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    evidence: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )


class StockGroupCandidate(Base):
    __tablename__ = "stock_group_candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'REJECTED')", name="status_supported"
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="confidence_fraction"
        ),
    )

    stock_group_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("stock_groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    product_barcode: Mapped[str] = mapped_column(
        ForeignKey("products.barcode", ondelete="CASCADE"), primary_key=True
    )
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    status: Mapped[str] = mapped_column(
        Text, default="PENDING", server_default="PENDING", nullable=False
    )
    evidence: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StockGroupPolicy(Base):
    __tablename__ = "stock_group_policies"
    __table_args__ = (
        CheckConstraint("reorder_point >= 0", name="reorder_point_nonnegative"),
        CheckConstraint("target_stock >= reorder_point", name="target_at_least_reorder"),
    )

    stock_group_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("stock_groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    reorder_point: Mapped[int] = mapped_column(Integer, nullable=False)
    target_stock: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class StockGroupShoppingItem(Base):
    __tablename__ = "stock_group_shopping_items"
    __table_args__ = (
        CheckConstraint("automatic_quantity >= 0", name="automatic_quantity_nonnegative"),
        CheckConstraint("manual_quantity >= 0", name="manual_quantity_nonnegative"),
        CheckConstraint("checked_quantity >= 0", name="checked_quantity_nonnegative"),
    )

    stock_group_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("stock_groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    automatic_quantity: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    manual_quantity: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    checked_quantity: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    preferred_store_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("stores.id", ondelete="SET NULL")
    )
    auto_reason: Mapped[str | None] = mapped_column(Text)
    # Mirrors GroceryListItem: trip price, manual dismissal, and the member
    # barcodes actually scanned — a group row can be satisfied by any of them,
    # so checkout has to be told which one ended up in the cart.
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scanned_barcodes: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
