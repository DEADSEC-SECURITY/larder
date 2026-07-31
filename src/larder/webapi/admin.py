from __future__ import annotations

import csv
import io
from datetime import timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from larder.config import Settings
from larder.models import (
    FoodCategory,
    InventoryItem,
    Product,
    Scanner,
    ScannerContext,
    Store,
    TaxRate,
)
from larder.models.enums import InventoryEventType, InventoryStatus
from larder.services.expiration import resolve_expiration
from larder.services.stock_policy import recalculate_stock_policy
from larder.webapi.common import (  # noqa: I001
    add_inventory_event,
    create_web_scan_event,
    expiration_kind,
    get_app_settings,
    get_session,
    household_settings,
    utcnow,
    web_api_router,
)

router = web_api_router(tags=["admin"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


class SettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")
    removal_policy: Literal["FEFO", "FIFO", "LIFO", "LEFO"] | None = None
    expiring_window_days: int | None = Field(default=None, ge=1, le=30)
    auto_grocery: bool | None = None
    require_store_on_manual_add: bool | None = None
    badge_expiring_nav: bool | None = None
    retry_failed_lookups: bool | None = None
    audible_scan_confirmation: bool | None = None
    default_tax_rate_id: UUID | None = None
    intake_popup_timeout_seconds: int | None = Field(default=None, ge=0, le=3600)
    intake_scan_group_timeout_seconds: int | None = Field(default=None, ge=1, le=3600)
    scanner_mode_timeout_seconds: int | None = Field(default=None, ge=5, le=3600)
    scanner_context_timeout_seconds: int | None = Field(default=None, ge=30, le=86400)
    scanner_offline_after_seconds: int | None = Field(default=None, ge=60, le=604800)
    toast_duration_seconds: int | None = Field(default=None, ge=1, le=60)
    confirmation_timeout_seconds: int | None = Field(default=None, ge=1, le=60)
    live_update_debounce_ms: int | None = Field(default=None, ge=50, le=5000)
    fallback_refresh_seconds: int | None = Field(default=None, ge=5, le=3600)


def _settings_payload(prefs) -> dict:
    return {
        "removalPolicy": prefs.removal_policy,
        "expiringWindowDays": prefs.expiring_window_days,
        "autoGrocery": prefs.auto_grocery,
        "requireStoreOnManualAdd": prefs.require_store_on_manual_add,
        "badgeExpiringNav": prefs.badge_expiring_nav,
        "retryFailedLookups": prefs.retry_failed_lookups,
        "audibleScanConfirmation": prefs.audible_scan_confirmation,
        "defaultTaxRateId": str(prefs.default_tax_rate_id) if prefs.default_tax_rate_id else None,
        "intakePopupTimeoutSeconds": prefs.intake_popup_timeout_seconds,
        "intakeScanGroupTimeoutSeconds": prefs.intake_scan_group_timeout_seconds,
        "scannerModeTimeoutSeconds": prefs.scanner_mode_timeout_seconds,
        "scannerContextTimeoutSeconds": prefs.scanner_context_timeout_seconds,
        "scannerOfflineAfterSeconds": prefs.scanner_offline_after_seconds,
        "toastDurationSeconds": prefs.toast_duration_seconds,
        "confirmationTimeoutSeconds": prefs.confirmation_timeout_seconds,
        "liveUpdateDebounceMs": prefs.live_update_debounce_ms,
        "fallbackRefreshSeconds": prefs.fallback_refresh_seconds,
    }


@router.get("/settings")
async def get_household_settings(session: SessionDep) -> dict:
    return _settings_payload(await household_settings(session))


@router.put("/settings")
async def update_household_settings(payload: SettingsUpdateRequest, session: SessionDep) -> dict:
    prefs = await household_settings(session, for_update=True)
    mode_timeout_changed = (
        "scanner_mode_timeout_seconds" in payload.model_fields_set
        and payload.scanner_mode_timeout_seconds is not None
    )
    context_timeout_changed = (
        "scanner_context_timeout_seconds" in payload.model_fields_set
        and payload.scanner_context_timeout_seconds is not None
    )
    for field in payload.model_fields_set:
        value = getattr(payload, field)
        if field == "default_tax_rate_id":
            if value is not None:
                rate = await session.get(TaxRate, value)
                if rate is None or not rate.active:
                    raise HTTPException(
                        status.HTTP_422_UNPROCESSABLE_ENTITY,
                        "default tax rate not found or inactive",
                    )
            setattr(prefs, field, value)
        elif value is not None:
            setattr(prefs, field, value)

    if mode_timeout_changed or context_timeout_changed:
        scanners = (await session.scalars(select(Scanner).with_for_update())).all()
        for scanner in scanners:
            if mode_timeout_changed:
                scanner.mode_timeout_seconds = prefs.scanner_mode_timeout_seconds
            if context_timeout_changed:
                scanner.store_timeout_seconds = prefs.scanner_context_timeout_seconds

        now = utcnow()
        contexts = (await session.scalars(select(ScannerContext).with_for_update())).all()
        for context in contexts:
            changed = False
            if (
                mode_timeout_changed
                and context.mode is not None
                and context.mode_expires_at is not None
                and context.mode_expires_at > now
            ):
                context.mode_expires_at = now + timedelta(
                    seconds=prefs.scanner_mode_timeout_seconds
                )
                changed = True
            if context_timeout_changed:
                if (
                    context.store_id is not None
                    and context.store_expires_at is not None
                    and context.store_expires_at > now
                ):
                    context.store_expires_at = now + timedelta(
                        seconds=prefs.scanner_context_timeout_seconds
                    )
                    changed = True
                if (
                    context.storage_location_id is not None
                    and context.storage_location_expires_at is not None
                    and context.storage_location_expires_at > now
                ):
                    context.storage_location_expires_at = now + timedelta(
                        seconds=prefs.scanner_context_timeout_seconds
                    )
                    changed = True
            if changed:
                context.updated_at = now
                context.version += 1
    await session.flush()
    return _settings_payload(prefs)


@router.get("/export/inventory.csv", response_class=PlainTextResponse)
async def export_inventory_csv(session: SessionDep) -> PlainTextResponse:
    rows = (
        await session.execute(
            select(InventoryItem, Product, Store.name, FoodCategory.display_name)
            .join(Product, Product.barcode == InventoryItem.product_barcode)
            .outerjoin(Store, Store.id == InventoryItem.store_id)
            .outerjoin(FoodCategory, FoodCategory.id == InventoryItem.expiration_category_id)
            .where(InventoryItem.status == InventoryStatus.IN_STOCK)
            .order_by(
                Product.name.asc().nulls_last(),
                InventoryItem.expiration_date.asc().nulls_last(),
            )
        )
    ).all()
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["product", "brand", "barcode", "expires", "type", "store", "price", "added"])
    for item, product, store_name, _category_name in rows:
        writer.writerow(
            [
                product.name or "Unidentified item",
                product.brand or "",
                product.barcode,
                item.expiration_date.isoformat() if item.expiration_date else "",
                expiration_kind(item),
                store_name or "",
                f"{item.price_paid:.2f}" if item.price_paid is not None else "",
                item.date_added.date().isoformat(),
            ]
        )
    return PlainTextResponse(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=larder-inventory.csv"},
    )


@router.post("/maintenance/recalculate-estimates")
async def recalculate_estimates(session: SessionDep, settings: SettingsDep) -> dict:
    items = (
        await session.scalars(
            select(InventoryItem)
            .where(
                InventoryItem.status == InventoryStatus.IN_STOCK,
                InventoryItem.expiration_is_estimated.is_(True)
                | InventoryItem.expiration_date.is_(None),
            )
            .with_for_update()
        )
    ).all()
    updated = 0
    now = utcnow()
    for item in items:
        snapshot = await resolve_expiration(
            session,
            item.product_barcode,
            item.date_added,
            settings.household_timezone,
            settings.fallback_category_id,
        )
        new_date = snapshot.expiration_date if snapshot else None
        estimated_unchanged = (snapshot is not None) == item.expiration_is_estimated
        if new_date == item.expiration_date and estimated_unchanged:
            continue
        before = {
            "expiration_date": item.expiration_date.isoformat() if item.expiration_date else None,
        }
        item.expiration_date = new_date
        item.expiration_is_estimated = snapshot is not None
        item.expiration_category_id = snapshot.category_id if snapshot else None
        item.expiration_offset_days = snapshot.offset_days if snapshot else None
        add_inventory_event(
            session,
            item_id=item.id,
            event_type=InventoryEventType.EXPIRATION_CHANGED,
            scan_event_id=None,
            before_state=before,
            after_state={
                "expiration_date": new_date.isoformat() if new_date else None,
                "kind": expiration_kind(item),
            },
            reason="recalculated from category rules",
            actor_type="SYSTEM",
            actor_id="web-maintenance",
            occurred_at=now,
        )
        updated += 1
    await session.flush()
    return {"updated": updated}


@router.post("/maintenance/reset-inventory")
async def reset_inventory(session: SessionDep) -> dict:
    items = (
        await session.scalars(
            select(InventoryItem)
            .where(InventoryItem.status == InventoryStatus.IN_STOCK)
            .with_for_update()
        )
    ).all()
    now = utcnow()
    barcodes = set()
    for item in items:
        event = await create_web_scan_event(session, barcode=item.product_barcode)
        item.status = InventoryStatus.REMOVED
        item.removed_at = now
        item.terminal_scan_event_id = event.id
        add_inventory_event(
            session,
            item_id=item.id,
            event_type=InventoryEventType.REMOVED,
            scan_event_id=event.id,
            before_state={"status": InventoryStatus.IN_STOCK.value},
            after_state={
                "status": InventoryStatus.REMOVED.value,
                "removed_at": now.isoformat(),
            },
            reason="inventory reset",
            occurred_at=now,
        )
        barcodes.add(item.product_barcode)
    await session.flush()
    for barcode in barcodes:
        await recalculate_stock_policy(session, barcode)
    return {"removed": len(items)}
