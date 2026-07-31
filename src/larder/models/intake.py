from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from larder.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class IntakeBatch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Consecutive ADD scans presented to the UI as one editable intake."""

    __tablename__ = "intake_batches"
    __table_args__ = (
        CheckConstraint("status IN ('OPEN', 'CONFIRMED', 'DISMISSED')", name="status_supported"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint(
            "(status = 'OPEN' AND finalized_at IS NULL) OR "
            "(status IN ('CONFIRMED', 'DISMISSED') AND finalized_at IS NOT NULL)",
            name="finalized_state_valid",
        ),
        Index(
            "ix_intake_batches_open",
            "scanner_id",
            "last_scan_at",
            postgresql_where=text("status = 'OPEN'"),
        ),
    )

    scanner_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("scanners.id", ondelete="RESTRICT"), nullable=False
    )
    product_barcode: Mapped[str] = mapped_column(
        ForeignKey("products.barcode", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, default="OPEN", server_default="OPEN", nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    last_scan_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
