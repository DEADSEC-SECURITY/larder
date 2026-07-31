from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from larder.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from larder.models.enums import EnrichmentStatus
from larder.models.types import enrichment_status_type


class EnrichmentJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "enrichment_jobs"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint(
            "(status = 'RUNNING' AND locked_at IS NOT NULL AND locked_by IS NOT NULL) OR "
            "(status <> 'RUNNING')",
            name="running_has_lease",
        ),
    )

    product_barcode: Mapped[str] = mapped_column(
        ForeignKey("products.barcode", ondelete="CASCADE"), unique=True, nullable=False
    )
    status: Mapped[EnrichmentStatus] = mapped_column(
        enrichment_status_type,
        default=EnrichmentStatus.PENDING,
        server_default=EnrichmentStatus.PENDING.value,
        nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(Text)
    last_http_status: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
