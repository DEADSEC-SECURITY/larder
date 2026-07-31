from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from larder.models.base import Base

HOUSEHOLD_SETTINGS_ID = "household"
REMOVAL_POLICIES = ("FEFO", "FIFO", "LIFO", "LEFO")

INTAKE_POPUP_TIMEOUT_SECONDS_DEFAULT = 30
INTAKE_SCAN_GROUP_TIMEOUT_SECONDS_DEFAULT = 30
SCANNER_MODE_TIMEOUT_SECONDS_DEFAULT = 30
SCANNER_CONTEXT_TIMEOUT_SECONDS_DEFAULT = 1800
SCANNER_OFFLINE_AFTER_SECONDS_DEFAULT = 172800
TOAST_DURATION_SECONDS_DEFAULT = 3
CONFIRMATION_TIMEOUT_SECONDS_DEFAULT = 3
LIVE_UPDATE_DEBOUNCE_MS_DEFAULT = 300
FALLBACK_REFRESH_SECONDS_DEFAULT = 60


class HouseholdSettings(Base):
    """Single-row table holding web-configurable household behavior."""

    __tablename__ = "household_settings"
    __table_args__ = (
        CheckConstraint("id = 'household'", name="single_row"),
        CheckConstraint(
            "removal_policy IN ('FEFO', 'FIFO', 'LIFO', 'LEFO')",
            name="removal_policy_valid",
        ),
        CheckConstraint(
            "expiring_window_days BETWEEN 1 AND 30", name="expiring_window_days_bounded"
        ),
        CheckConstraint(
            "intake_popup_timeout_seconds BETWEEN 0 AND 3600",
            name="intake_popup_timeout_seconds_bounded",
        ),
        CheckConstraint(
            "intake_scan_group_timeout_seconds BETWEEN 1 AND 3600",
            name="intake_scan_group_timeout_seconds_bounded",
        ),
        CheckConstraint(
            "scanner_mode_timeout_seconds BETWEEN 5 AND 3600",
            name="scanner_mode_timeout_seconds_bounded",
        ),
        CheckConstraint(
            "scanner_context_timeout_seconds BETWEEN 30 AND 86400",
            name="scanner_context_timeout_seconds_bounded",
        ),
        CheckConstraint(
            "scanner_offline_after_seconds BETWEEN 60 AND 604800",
            name="scanner_offline_after_seconds_bounded",
        ),
        CheckConstraint(
            "toast_duration_seconds BETWEEN 1 AND 60",
            name="toast_duration_seconds_bounded",
        ),
        CheckConstraint(
            "confirmation_timeout_seconds BETWEEN 1 AND 60",
            name="confirmation_timeout_seconds_bounded",
        ),
        CheckConstraint(
            "live_update_debounce_ms BETWEEN 50 AND 5000",
            name="live_update_debounce_ms_bounded",
        ),
        CheckConstraint(
            "fallback_refresh_seconds BETWEEN 5 AND 3600",
            name="fallback_refresh_seconds_bounded",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=HOUSEHOLD_SETTINGS_ID)
    removal_policy: Mapped[str] = mapped_column(
        Text, default="FEFO", server_default="FEFO", nullable=False
    )
    expiring_window_days: Mapped[int] = mapped_column(
        Integer, default=4, server_default="4", nullable=False
    )
    auto_grocery: Mapped[bool] = mapped_column(default=True, server_default="true", nullable=False)
    require_store_on_manual_add: Mapped[bool] = mapped_column(
        default=False, server_default="false", nullable=False
    )
    badge_expiring_nav: Mapped[bool] = mapped_column(
        default=True, server_default="true", nullable=False
    )
    retry_failed_lookups: Mapped[bool] = mapped_column(
        default=True, server_default="true", nullable=False
    )
    audible_scan_confirmation: Mapped[bool] = mapped_column(
        default=False, server_default="false", nullable=False
    )
    intake_popup_timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        default=INTAKE_POPUP_TIMEOUT_SECONDS_DEFAULT,
        server_default=str(INTAKE_POPUP_TIMEOUT_SECONDS_DEFAULT),
        nullable=False,
    )
    intake_scan_group_timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        default=INTAKE_SCAN_GROUP_TIMEOUT_SECONDS_DEFAULT,
        server_default=str(INTAKE_SCAN_GROUP_TIMEOUT_SECONDS_DEFAULT),
        nullable=False,
    )
    scanner_mode_timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        default=SCANNER_MODE_TIMEOUT_SECONDS_DEFAULT,
        server_default=str(SCANNER_MODE_TIMEOUT_SECONDS_DEFAULT),
        nullable=False,
    )
    scanner_context_timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        default=SCANNER_CONTEXT_TIMEOUT_SECONDS_DEFAULT,
        server_default=str(SCANNER_CONTEXT_TIMEOUT_SECONDS_DEFAULT),
        nullable=False,
    )
    scanner_offline_after_seconds: Mapped[int] = mapped_column(
        Integer,
        default=SCANNER_OFFLINE_AFTER_SECONDS_DEFAULT,
        server_default=str(SCANNER_OFFLINE_AFTER_SECONDS_DEFAULT),
        nullable=False,
    )
    toast_duration_seconds: Mapped[int] = mapped_column(
        Integer,
        default=TOAST_DURATION_SECONDS_DEFAULT,
        server_default=str(TOAST_DURATION_SECONDS_DEFAULT),
        nullable=False,
    )
    confirmation_timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        default=CONFIRMATION_TIMEOUT_SECONDS_DEFAULT,
        server_default=str(CONFIRMATION_TIMEOUT_SECONDS_DEFAULT),
        nullable=False,
    )
    live_update_debounce_ms: Mapped[int] = mapped_column(
        Integer,
        default=LIVE_UPDATE_DEBOUNCE_MS_DEFAULT,
        server_default=str(LIVE_UPDATE_DEBOUNCE_MS_DEFAULT),
        nullable=False,
    )
    fallback_refresh_seconds: Mapped[int] = mapped_column(
        Integer,
        default=FALLBACK_REFRESH_SECONDS_DEFAULT,
        server_default=str(FALLBACK_REFRESH_SECONDS_DEFAULT),
        nullable=False,
    )
    default_tax_rate_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tax_rates.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
