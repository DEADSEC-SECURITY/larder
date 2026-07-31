from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from larder.models.base import Base


class ProductStockPolicy(Base):
    __tablename__ = "product_stock_policies"
    __table_args__ = (
        CheckConstraint("reorder_point >= 0", name="reorder_point_nonnegative"),
        CheckConstraint("target_stock >= reorder_point", name="target_at_least_reorder"),
    )

    product_barcode: Mapped[str] = mapped_column(
        ForeignKey("products.barcode", ondelete="CASCADE"), primary_key=True
    )
    reorder_point: Mapped[int] = mapped_column(Integer, nullable=False)
    target_stock: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, server_default="true", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GroceryListItem(Base):
    __tablename__ = "grocery_list_items"
    __table_args__ = (
        CheckConstraint("automatic_quantity >= 0", name="automatic_quantity_nonnegative"),
        CheckConstraint("manual_quantity >= 0", name="manual_quantity_nonnegative"),
        CheckConstraint("checked_quantity >= 0", name="checked_quantity_nonnegative"),
    )

    product_barcode: Mapped[str] = mapped_column(
        ForeignKey("products.barcode", ondelete="CASCADE"), primary_key=True
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
        ForeignKey("stores.id", ondelete="SET NULL")
    )
    auto_reason: Mapped[str | None] = mapped_column(Text)
    # Price captured during the trip. Overrides the lowest-observed estimate and
    # is what checkout records against the units it creates.
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # Set when the row is deleted by hand. Automatic rows would otherwise be
    # regenerated on the next stock recalculation; this suppresses them until
    # the product genuinely drops below its reorder point again.
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Barcodes actually scanned into the cart, in order, so checkout knows what
    # was bought rather than guessing.
    scanned_barcodes: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
