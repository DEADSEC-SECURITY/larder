from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from larder.domain.enums import ScannerMode


@dataclass(frozen=True, slots=True)
class ContextValues[StoreId]:
    mode: ScannerMode | None
    mode_expires_at: datetime | None
    store_id: StoreId | None
    store_expires_at: datetime | None
    storage_location_id: StoreId | None = None
    storage_location_expires_at: datetime | None = None


def expire_context[StoreId](
    values: ContextValues[StoreId], now: datetime
) -> ContextValues[StoreId]:
    mode_expired = values.mode_expires_at is not None and values.mode_expires_at <= now
    store_expired = values.store_expires_at is not None and values.store_expires_at <= now
    location_expired = (
        values.storage_location_expires_at is not None
        and values.storage_location_expires_at <= now
    )
    return ContextValues(
        mode=None if mode_expired else values.mode,
        mode_expires_at=None if mode_expired else values.mode_expires_at,
        store_id=None if store_expired else values.store_id,
        store_expires_at=None if store_expired else values.store_expires_at,
        storage_location_id=None if location_expired else values.storage_location_id,
        storage_location_expires_at=None
        if location_expired
        else values.storage_location_expires_at,
    )
