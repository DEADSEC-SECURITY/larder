from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from larder.domain.names import standardize_name
from larder.models import (
    ControlBarcode,
    InventoryItem,
    ProductStoreObservation,
    Store,
)
from larder.models.enums import InventoryStatus
from larder.webapi.common import get_session, slug_code, web_api_router

router = web_api_router(tags=["stores"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class StoreCreateRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")
    name: str = Field(min_length=1, max_length=100)


class StoreUpdateRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")
    active: bool | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)


def control_payload_for(code: str) -> str:
    return f"CMD:STORE:{code}"


async def ensure_control_barcode(session: AsyncSession, store: Store) -> ControlBarcode:
    payload = control_payload_for(store.code)
    control = await session.get(ControlBarcode, payload)
    if control is None:
        control = ControlBarcode(
            payload=payload,
            command_type="SET_STORE",
            store_id=store.id,
            enabled=True,
            description=f"Set active store to {store.name}",
        )
        session.add(control)
        await session.flush()
    return control


async def get_or_create_store(session: AsyncSession, name: str) -> Store:
    display_name = standardize_name(name)
    store = await session.scalar(
        select(Store).where(func.lower(Store.name) == display_name.lower())
    )
    if store is not None:
        return store
    base = slug_code(display_name)
    code = base
    suffix = 2
    while await session.scalar(select(Store.id).where(Store.code == code)) is not None:
        code = f"{base[: 32 - len(str(suffix))]}{suffix}"
        suffix += 1
    store = Store(code=code, name=display_name, active=True)
    session.add(store)
    await session.flush()
    await ensure_control_barcode(session, store)
    return store


@router.get("/stores")
async def list_stores(session: SessionDep) -> dict:
    stores = (await session.scalars(select(Store).order_by(Store.name))).all()

    observation_rows = (
        await session.execute(
            select(
                ProductStoreObservation.store_id,
                func.count().label("records"),
                func.count(func.distinct(ProductStoreObservation.product_barcode)).label(
                    "products"
                ),
                func.max(ProductStoreObservation.observed_at).label("last_observed"),
            ).group_by(ProductStoreObservation.store_id)
        )
    ).all()
    stats = {row.store_id: row for row in observation_rows}

    stocked_barcodes = set(
        (
            await session.scalars(
                select(InventoryItem.product_barcode)
                .where(InventoryItem.status == InventoryStatus.IN_STOCK)
                .distinct()
            )
        ).all()
    )
    covered_rows = (
        await session.execute(
            select(
                ProductStoreObservation.store_id,
                ProductStoreObservation.product_barcode,
            ).distinct()
        )
    ).all()
    covered: dict[UUID, set[str]] = {}
    for row in covered_rows:
        covered.setdefault(row.store_id, set()).add(row.product_barcode)

    controls = {
        c.store_id: c.payload
        for c in (
            await session.scalars(
                select(ControlBarcode).where(
                    ControlBarcode.command_type == "SET_STORE", ControlBarcode.enabled.is_(True)
                )
            )
        ).all()
    }

    payload = []
    for store in stores:
        row = stats.get(store.id)
        coverage = 0
        if stocked_barcodes:
            coverage = round(
                100 * len(covered.get(store.id, set()) & stocked_barcodes) / len(stocked_barcodes)
            )
        payload.append(
            {
                "id": str(store.id),
                "code": store.code,
                "name": store.name,
                "active": store.active,
                "productCount": row.products if row else 0,
                "recordCount": row.records if row else 0,
                "lastObservedAt": row.last_observed.isoformat()
                if row and row.last_observed
                else None,
                "priceCoveragePct": coverage,
                "controlPayload": controls.get(store.id),
            }
        )
    return {"stores": payload}


@router.post("/stores", status_code=status.HTTP_201_CREATED)
async def create_store(payload: StoreCreateRequest, session: SessionDep) -> dict:
    store = await get_or_create_store(session, payload.name.strip())
    control = await ensure_control_barcode(session, store)
    return {
        "id": str(store.id),
        "code": store.code,
        "name": store.name,
        "active": store.active,
        "controlPayload": control.payload,
    }


@router.patch("/stores/{store_id}")
async def update_store(store_id: UUID, payload: StoreUpdateRequest, session: SessionDep) -> dict:
    store = await session.get(Store, store_id, with_for_update=True)
    if store is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "store not found")
    if payload.active is not None:
        store.active = payload.active
    if payload.name and payload.name.strip():
        store.name = standardize_name(payload.name)
    await session.flush()
    return {"id": str(store.id), "code": store.code, "name": store.name, "active": store.active}


@router.post("/stores/{store_id}/control-barcode")
async def create_store_control_barcode(store_id: UUID, session: SessionDep) -> dict:
    store = await session.get(Store, store_id)
    if store is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "store not found")
    control = await ensure_control_barcode(session, store)
    return {"storeId": str(store.id), "payload": control.payload}
