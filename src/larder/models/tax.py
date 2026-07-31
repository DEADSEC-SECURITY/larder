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
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from larder.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TaxLocation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A saved point-of-sale jurisdiction/address used to group selectable rates."""

    __tablename__ = "tax_locations"
    __table_args__ = (
        CheckConstraint("provider IN ('MANUAL', 'ZIPTAX')", name="provider_supported"),
        CheckConstraint("country_code ~ '^[A-Z]{2,3}$'", name="country_code_format"),
        # A location's identity is its address; labels are renamable display
        # names. Unique among *active* rows only — "removing" deactivates
        # (price history references the rates) and frees the address for
        # re-adding (migration 0015).
        Index(
            "uq_tax_locations_address_active",
            text("lower(coalesce(address_line1, ''))"),
            text("lower(city)"),
            text("lower(state)"),
            "postal_code",
            unique=True,
            postgresql_where=text("active"),
        ),
    )

    label: Mapped[str] = mapped_column(Text, nullable=False)
    address_line1: Mapped[str | None] = mapped_column(Text)
    address_line2: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(3), nullable=False)
    postal_code: Mapped[str] = mapped_column(String(16), nullable=False)
    country_code: Mapped[str] = mapped_column(
        String(3), default="USA", server_default="USA", nullable=False
    )
    normalized_address: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(
        Text, default="MANUAL", server_default="MANUAL", nullable=False
    )
    active: Mapped[bool] = mapped_column(default=True, server_default="true", nullable=False)


class TaxRate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The currently selectable rate for one product class at a saved location."""

    __tablename__ = "tax_rates"
    __table_args__ = (
        UniqueConstraint("location_id", "tax_class", name="location_tax_class"),
        CheckConstraint("rate >= 0 AND rate <= 1", name="rate_fraction"),
        CheckConstraint("source IN ('MANUAL', 'ZIPTAX')", name="source_supported"),
    )

    location_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tax_locations.id", ondelete="CASCADE"), nullable=False
    )
    tax_class: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    taxability_code: Mapped[str | None] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(default=True, server_default="true", nullable=False)
