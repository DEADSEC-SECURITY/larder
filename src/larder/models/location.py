from __future__ import annotations

from sqlalchemy import Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column

from larder.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class StorageLocation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "storage_locations"

    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
