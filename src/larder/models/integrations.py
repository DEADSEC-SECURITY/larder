from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from larder.models.base import Base


class IntegrationSetting(Base):
    """Per-provider credentials editable from the web UI.

    A row overrides the matching environment variable; a missing row (or null
    key) falls back to the environment so existing deployments keep working.
    """

    __tablename__ = "integration_settings"

    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    api_key: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
