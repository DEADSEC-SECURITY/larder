from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from larder.config import Settings
from larder.models import (
    IntakeBatch,
    InventoryItem,
    Product,
    ProductStoreObservation,
    Scanner,
    StorageLocation,
    Store,
)
from larder.models.enums import InventoryEventType, InventoryStatus
from larder.services.tax import apply_price, item_price_payload, price_breakdown
from larder.webapi.common import (
    add_inventory_event,
    as_float,
    expiration_kind,
    get_app_settings,
    get_session,
    household_settings,
    utcnow,
    web_api_router,
)

router = web_api_router(tags=["intake"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


class IntakeConfirmRequest(BaseModel):
    """Fields omitted from the request retain their scan-time defaults.

    Explicit null clears store, unit price, or expiration respectively.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    store_id: UUID | None = None
    unit_price: float | None = Field(default=None, ge=0, le=100_000)
    # True = unitPrice is the final total; the subtotal is backed out of it.
    price_includes_tax: bool = False
    tax_rate_id: UUID | None = None
    expiration_date: date | None = None
    storage_location_id: UUID | None = None

    @model_validator(mode="after")
    def price_required_for_tax(self) -> IntakeConfirmRequest:
        if "tax_rate_id" in self.model_fields_set and "unit_price" not in self.model_fields_set:
            raise ValueError("unitPrice is required when taxRateId is provided")
        return self


async def _items_for_batch(
    session: AsyncSession, batch_id: UUID, *, for_update: bool = False
) -> list[InventoryItem]:
    statement = (
        select(InventoryItem)
        .where(InventoryItem.intake_batch_id == batch_id)
        .order_by(InventoryItem.date_added, InventoryItem.id)
    )
    if for_update:
        statement = statement.with_for_update()
    return list((await session.scalars(statement)).all())


async def _batch_payload(session: AsyncSession, batch: IntakeBatch) -> dict:
    scanner = await session.get(Scanner, batch.scanner_id)
    product = await session.get(Product, batch.product_barcode)
    items = await _items_for_batch(session, batch.id)
    editable = [item for item in items if item.status == InventoryStatus.IN_STOCK]
    representative = editable[0] if editable else (items[0] if items else None)
    store = (
        await session.get(Store, representative.store_id)
        if representative is not None and representative.store_id is not None
        else None
    )
    storage_location = (
        await session.get(StorageLocation, representative.storage_location_id)
        if representative and representative.storage_location_id is not None
        else None
    )
    return {
        "id": str(batch.id),
        "status": batch.status.lower(),
        "scannerId": str(batch.scanner_id),
        "scannerCode": scanner.code if scanner else None,
        "scannerName": scanner.name if scanner else None,
        "barcode": batch.product_barcode,
        "productName": product.name if product else None,
        "brand": product.brand if product else None,
        "imageUrl": product.image_url if product else None,
        "enrichmentStatus": product.enrichment_status.value if product else None,
        "quantity": batch.quantity,
        "editableQuantity": len(editable),
        "storeId": str(store.id) if store else None,
        "storeName": store.name if store else None,
        "storageLocationId": str(storage_location.id) if storage_location else None,
        "storageLocationName": storage_location.name if storage_location else None,
        "unitPrice": as_float(representative.price_paid) if representative else None,
        "unitPriceSubtotal": as_float(representative.price_subtotal) if representative else None,
        "taxRate": as_float(representative.tax_rate) if representative else None,
        "taxAmount": as_float(representative.tax_amount) if representative else None,
        "taxRateId": str(representative.tax_rate_id)
        if representative and representative.tax_rate_id
        else None,
        "taxLocationId": str(representative.tax_location_id)
        if representative and representative.tax_location_id
        else None,
        "taxClass": representative.tax_class if representative else None,
        "taxSource": representative.tax_source.lower()
        if representative and representative.tax_source
        else None,
        "priceBreakdown": item_price_payload(representative) if representative else None,
        "currency": representative.currency_code if representative else None,
        "expirationDate": (
            representative.expiration_date.isoformat()
            if representative and representative.expiration_date
            else None
        ),
        "expirationKind": expiration_kind(representative) if representative else "unknown",
        "createdAt": batch.created_at.isoformat(),
        "updatedAt": batch.updated_at.isoformat(),
        "lastScanAt": batch.last_scan_at.isoformat(),
        "finalizedAt": batch.finalized_at.isoformat() if batch.finalized_at else None,
    }


async def _require_batch(
    session: AsyncSession, batch_id: UUID, *, for_update: bool = False
) -> IntakeBatch:
    batch = await session.get(IntakeBatch, batch_id, with_for_update=for_update)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "intake batch not found")
    return batch


@router.get("/intake-batches")
async def list_intake_batches(
    session: SessionDep,
    batch_status: Literal["open", "confirmed", "dismissed", "all"] = Query(
        default="open", alias="status"
    ),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    statement = select(IntakeBatch)
    if batch_status != "all":
        statement = statement.where(IntakeBatch.status == batch_status.upper())
    if batch_status == "open":
        statement = statement.order_by(IntakeBatch.created_at.asc())
    else:
        statement = statement.order_by(IntakeBatch.last_scan_at.desc())
    statement = statement.limit(limit)
    batches = (await session.scalars(statement)).all()
    return {"batches": [await _batch_payload(session, batch) for batch in batches]}


@router.get("/intake-batches/{batch_id}")
async def get_intake_batch(batch_id: UUID, session: SessionDep) -> dict:
    return await _batch_payload(session, await _require_batch(session, batch_id))


@router.post("/intake-batches/{batch_id}/confirm")
async def confirm_intake_batch(
    batch_id: UUID,
    payload: IntakeConfirmRequest,
    session: SessionDep,
    settings: SettingsDep,
) -> dict:
    batch = await _require_batch(session, batch_id, for_update=True)
    prefs = await household_settings(session)
    if batch.status != "OPEN":
        raise HTTPException(status.HTTP_409_CONFLICT, "intake batch is already finalized")

    items = await _items_for_batch(session, batch.id, for_update=True)
    editable = [item for item in items if item.status == InventoryStatus.IN_STOCK]
    fields = payload.model_fields_set
    now = utcnow()

    selected_store: Store | None = None
    if "store_id" in fields and payload.store_id is not None:
        selected_store = await session.get(Store, payload.store_id)
        if selected_store is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "store not found")
        if not selected_store.active:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "store is inactive")

    selected_storage_location: StorageLocation | None = None
    if "storage_location_id" in fields and payload.storage_location_id is not None:
        selected_storage_location = await session.get(
            StorageLocation, payload.storage_location_id
        )
        if selected_storage_location is None or not selected_storage_location.active:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "storage location not found")

    breakdown = None
    if "unit_price" in fields and payload.unit_price is not None:
        selected_rate_id = (
            payload.tax_rate_id if "tax_rate_id" in fields else prefs.default_tax_rate_id
        )
        breakdown = await price_breakdown(
            session, payload.unit_price, selected_rate_id, tax_included=payload.price_includes_tax
        )

    observed_stores: set[UUID] = set()
    for item in editable:
        if "storage_location_id" in fields:
            before_location = item.storage_location_id
            item.storage_location_id = (
                selected_storage_location.id if selected_storage_location else None
            )
            if before_location != item.storage_location_id:
                add_inventory_event(
                    session,
                    item_id=item.id,
                    event_type=InventoryEventType.STORAGE_CHANGED,
                    scan_event_id=None,
                    before_state={
                        "storage_location_id": str(before_location)
                        if before_location
                        else None
                    },
                    after_state={
                        "storage_location_id": str(item.storage_location_id)
                        if item.storage_location_id
                        else None
                    },
                    reason="intake batch confirmed",
                )
        if "store_id" in fields:
            before_store = item.store_id
            item.store_id = selected_store.id if selected_store else None
            if before_store != item.store_id:
                add_inventory_event(
                    session,
                    item_id=item.id,
                    event_type=InventoryEventType.STORE_CHANGED,
                    scan_event_id=None,
                    before_state={"store_id": str(before_store) if before_store else None},
                    after_state={"store_id": str(item.store_id) if item.store_id else None},
                    reason="intake batch confirmed",
                )

        if "unit_price" in fields:
            before_price = item_price_payload(item)
            apply_price(item, breakdown, settings.currency_code)
            if before_price != item_price_payload(item):
                add_inventory_event(
                    session,
                    item_id=item.id,
                    event_type=InventoryEventType.PRICE_CHANGED,
                    scan_event_id=None,
                    before_state=before_price,
                    after_state=item_price_payload(item),
                    reason="intake batch confirmed",
                )

        if "expiration_date" in fields:
            before_expiration = item.expiration_date
            before_kind = expiration_kind(item)
            item.expiration_date = payload.expiration_date
            item.expiration_is_estimated = False
            item.expiration_category_id = None
            item.expiration_offset_days = None
            if before_expiration != item.expiration_date or before_kind != expiration_kind(item):
                add_inventory_event(
                    session,
                    item_id=item.id,
                    event_type=InventoryEventType.EXPIRATION_CHANGED,
                    scan_event_id=None,
                    before_state={
                        "expiration_date": before_expiration.isoformat()
                        if before_expiration
                        else None,
                        "kind": before_kind,
                    },
                    after_state={
                        "expiration_date": item.expiration_date.isoformat()
                        if item.expiration_date
                        else None,
                        "kind": expiration_kind(item),
                    },
                    reason="intake batch confirmed",
                )

        if breakdown is not None and item.store_id is not None:
            observed_stores.add(item.store_id)

    if "unit_price" in fields and breakdown is not None:
        for store_id in observed_stores:
            session.add(
                ProductStoreObservation(
                    product_barcode=batch.product_barcode,
                    store_id=store_id,
                    observed_at=now,
                    unit_price=breakdown.total,
                    subtotal_price=breakdown.subtotal,
                    tax_rate=breakdown.tax_rate,
                    tax_amount=breakdown.tax_amount,
                    tax_rate_id=breakdown.tax_rate_id,
                    tax_location_id=breakdown.tax_location_id,
                    tax_class=breakdown.tax_class,
                    tax_source=breakdown.tax_source,
                    currency_code=settings.currency_code,
                    source="WEB",
                )
            )

    batch.status = "CONFIRMED"
    batch.finalized_at = now
    await session.flush()
    await session.refresh(batch)
    return await _batch_payload(session, batch)


@router.post("/intake-batches/{batch_id}/dismiss")
async def dismiss_intake_batch(batch_id: UUID, session: SessionDep) -> dict:
    """Close the prompt while retaining the safe scan-time inventory defaults."""
    batch = await _require_batch(session, batch_id, for_update=True)
    if batch.status != "OPEN":
        raise HTTPException(status.HTTP_409_CONFLICT, "intake batch is already finalized")
    batch.status = "DISMISSED"
    batch.finalized_at = utcnow()
    await session.flush()
    await session.refresh(batch)
    return await _batch_payload(session, batch)
