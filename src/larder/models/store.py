from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from larder.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Store(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "stores"

    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    logo_url: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(default=True, server_default="true", nullable=False)


class ProductStoreObservation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "product_store_observations"
    __table_args__ = (
        CheckConstraint("unit_price >= 0", name="unit_price_nonnegative"),
        CheckConstraint("currency_code ~ '^[A-Z]{3}$'", name="currency_code_iso_format"),
        Index(
            "ix_store_observation_product_store_date",
            "product_barcode",
            "store_id",
            text("observed_at DESC"),
        ),
    )

    product_barcode: Mapped[str] = mapped_column(
        ForeignKey("products.barcode", ondelete="CASCADE"), nullable=False
    )
    store_id: Mapped[UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    subtotal_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    tax_rate_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tax_rates.id", ondelete="SET NULL")
    )
    tax_location_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tax_locations.id", ondelete="SET NULL")
    )
    tax_class: Mapped[str | None] = mapped_column(String(32))
    tax_source: Mapped[str | None] = mapped_column(String(16))
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    inventory_item_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("inventory_items.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
