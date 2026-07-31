from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from larder.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from larder.models.enums import ScannerMode
from larder.models.types import scanner_mode_type


class Scanner(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "scanners"
    __table_args__ = (
        CheckConstraint("mode_timeout_seconds > 0", name="mode_timeout_positive"),
        CheckConstraint("store_timeout_seconds > 0", name="store_timeout_positive"),
    )

    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    device_path: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(default=True, server_default="true", nullable=False)
    mode_timeout_seconds: Mapped[int] = mapped_column(
        Integer, default=30, server_default="30", nullable=False
    )
    store_timeout_seconds: Mapped[int] = mapped_column(
        Integer, default=1800, server_default="1800", nullable=False
    )


class ScannerContext(Base):
    __tablename__ = "scanner_contexts"
    __table_args__ = (
        CheckConstraint("(mode IS NULL) = (mode_expires_at IS NULL)", name="mode_expiry_paired"),
        CheckConstraint(
            "(store_id IS NULL) = (store_expires_at IS NULL)", name="store_expiry_paired"
        ),
        CheckConstraint(
            "(storage_location_id IS NULL) = (storage_location_expires_at IS NULL)",
            name="storage_location_expiry_paired",
        ),
    )

    scanner_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("scanners.id", ondelete="CASCADE"),
        primary_key=True,
    )
    mode: Mapped[ScannerMode | None] = mapped_column(scanner_mode_type)
    mode_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    store_id: Mapped[UUID | None] = mapped_column(ForeignKey("stores.id", ondelete="RESTRICT"))
    store_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    storage_location_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("storage_locations.id", ondelete="RESTRICT")
    )
    storage_location_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)


class ControlBarcode(Base):
    __tablename__ = "control_barcodes"
    __table_args__ = (
        CheckConstraint(
            "(command_type = 'SET_MODE' AND mode IS NOT NULL AND store_id IS NULL "
            "AND storage_location_id IS NULL) OR "
            "(command_type = 'SET_STORE' AND mode IS NULL AND store_id IS NOT NULL "
            "AND storage_location_id IS NULL) OR "
            "(command_type = 'SET_LOCATION' AND mode IS NULL AND store_id IS NULL "
            "AND storage_location_id IS NOT NULL) OR "
            "(command_type IN ('CLEAR_MODE', 'CLEAR_STORE', 'CLEAR_LOCATION') "
            "AND mode IS NULL AND store_id IS NULL AND storage_location_id IS NULL)",
            name="command_target_valid",
        ),
        CheckConstraint(
            "command_type IN ('SET_MODE', 'CLEAR_MODE', 'SET_STORE', 'CLEAR_STORE', "
            "'SET_LOCATION', 'CLEAR_LOCATION')",
            name="command_type_valid",
        ),
    )

    payload: Mapped[str] = mapped_column(Text, primary_key=True)
    command_type: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[ScannerMode | None] = mapped_column(scanner_mode_type)
    store_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE")
    )
    storage_location_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("storage_locations.id", ondelete="CASCADE")
    )
    enabled: Mapped[bool] = mapped_column(default=True, server_default="true", nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
