from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from larder.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PrintJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "print_jobs"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('PRODUCT', 'CONTROL', 'GENERIC')",
            name="print_job_kind_supported",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'PRINTING', 'PRINTED', 'FAILED')",
            name="print_job_status_supported",
        ),
        CheckConstraint("copies BETWEEN 1 AND 20", name="print_job_copies_range"),
        CheckConstraint("attempt_count >= 0", name="print_job_attempts_nonnegative"),
        Index("ix_print_jobs_claim", "status", "available_at", "created_at"),
    )

    kind: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    subtitle: Mapped[str | None] = mapped_column(Text)
    copies: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    status: Mapped[str] = mapped_column(
        Text, default="PENDING", server_default="PENDING", nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    claimed_by: Mapped[str | None] = mapped_column(Text)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
