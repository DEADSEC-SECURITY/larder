from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from larder.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from larder.models.enums import (
    InventoryEventType,
    InventoryStatus,
    ScanKind,
    ScannerMode,
    ScanResult,
)
from larder.models.types import (
    inventory_event_type,
    inventory_status_type,
    scan_kind_type,
    scan_result_type,
    scanner_mode_type,
)


class ScanEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "scan_events"
    __table_args__ = (
        CheckConstraint("processing_attempts >= 0", name="processing_attempts_nonnegative"),
        CheckConstraint("length(raw_frame) <= 4096", name="raw_frame_bounded"),
    )

    idempotency_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    scanner_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("scanners.id", ondelete="SET NULL")
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_frame: Mapped[str] = mapped_column(Text, nullable=False)
    payload_raw: Mapped[str | None] = mapped_column(Text)
    barcode: Mapped[str | None] = mapped_column(Text)
    normalization_version: Mapped[int | None] = mapped_column(SmallInteger)
    kind: Mapped[ScanKind] = mapped_column(scan_kind_type, nullable=False)
    mode_at_processing: Mapped[ScannerMode | None] = mapped_column(scanner_mode_type)
    store_id_at_processing: Mapped[UUID | None] = mapped_column(
        ForeignKey("stores.id", ondelete="SET NULL")
    )
    result: Mapped[ScanResult] = mapped_column(
        scan_result_type,
        default=ScanResult.RECEIVED,
        server_default=ScanResult.RECEIVED.value,
        nullable=False,
    )
    error_code: Mapped[str | None] = mapped_column(Text)
    error_detail: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    created_item_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    affected_item_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class InventoryItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "inventory_items"
    __table_args__ = (
        CheckConstraint(
            "(status = 'IN_STOCK' AND removed_at IS NULL AND terminal_scan_event_id IS NULL) OR "
            "(status IN ('REMOVED', 'TRASHED') AND removed_at IS NOT NULL "
            "AND terminal_scan_event_id IS NOT NULL)",
            name="terminal_state_valid",
        ),
        CheckConstraint(
            "NOT expiration_is_estimated OR "
            "(expiration_date IS NOT NULL AND expiration_category_id IS NOT NULL "
            "AND expiration_offset_days IS NOT NULL)",
            name="estimated_expiration_has_snapshot",
        ),
        CheckConstraint(
            "expiration_offset_days IS NULL OR expiration_offset_days >= 0",
            name="expiration_offset_nonnegative",
        ),
        CheckConstraint("price_paid IS NULL OR price_paid >= 0", name="price_paid_nonnegative"),
        CheckConstraint(
            "(price_paid IS NULL AND currency_code IS NULL) OR "
            "(price_paid IS NOT NULL AND currency_code IS NOT NULL)",
            name="price_currency_paired",
        ),
        Index(
            "ix_inventory_fefo",
            "product_barcode",
            text("expiration_date ASC NULLS LAST"),
            "date_added",
            "id",
            postgresql_where=text("status = 'IN_STOCK'"),
        ),
    )

    product_barcode: Mapped[str] = mapped_column(
        ForeignKey("products.barcode", ondelete="RESTRICT"), nullable=False
    )
    intake_batch_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("intake_batches.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[InventoryStatus] = mapped_column(
        inventory_status_type,
        default=InventoryStatus.IN_STOCK,
        server_default=InventoryStatus.IN_STOCK.value,
        nullable=False,
    )
    date_added: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expiration_date: Mapped[date | None] = mapped_column(Date)
    expiration_is_estimated: Mapped[bool] = mapped_column(
        default=False, server_default="false", nullable=False
    )
    expiration_category_id: Mapped[str | None] = mapped_column(
        ForeignKey("food_categories.id", ondelete="RESTRICT")
    )
    expiration_offset_days: Mapped[int | None] = mapped_column(Integer)
    store_id: Mapped[UUID | None] = mapped_column(ForeignKey("stores.id", ondelete="RESTRICT"))
    storage_location_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("storage_locations.id", ondelete="SET NULL")
    )
    price_paid: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    price_subtotal: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
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
    currency_code: Mapped[str | None] = mapped_column(String(3))
    price_pending: Mapped[bool] = mapped_column(default=True, server_default="true", nullable=False)
    add_scan_event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("scan_events.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    terminal_scan_event_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("scan_events.id", ondelete="RESTRICT"),
        unique=True,
    )


class InventoryEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "inventory_events"
    __table_args__ = (
        UniqueConstraint("scan_event_id", "event_type", name="uq_inventory_event_scan_type"),
        CheckConstraint("actor_type IN ('SCANNER', 'USER', 'SYSTEM')", name="actor_type_valid"),
    )

    inventory_item_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_type: Mapped[InventoryEventType] = mapped_column(inventory_event_type, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scan_event_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("scan_events.id", ondelete="RESTRICT")
    )
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str | None] = mapped_column(Text)
    before_state: Mapped[dict | None] = mapped_column(JSONB)
    after_state: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
