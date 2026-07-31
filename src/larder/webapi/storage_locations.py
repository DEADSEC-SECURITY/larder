from __future__ import annotations

import re
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from larder.domain.names import standardize_name
from larder.models import ControlBarcode, InventoryItem, StorageLocation
from larder.models.enums import InventoryStatus
from larder.webapi.common import get_session, web_api_router

router = web_api_router(tags=["storage-locations"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class StorageLocationCreateRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")
    name: str = Field(min_length=1, max_length=100)


class StorageLocationUpdateRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    active: bool | None = None


def _code(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "location"


def control_payload_for(code: str) -> str:
    return f"CMD:LOC:{code}"


async def ensure_control_barcode(
    session: AsyncSession, location: StorageLocation
) -> ControlBarcode:
    payload = control_payload_for(location.code)
    control = await session.get(ControlBarcode, payload)
    if control is None:
        control = ControlBarcode(
            payload=payload,
            command_type="SET_LOCATION",
            storage_location_id=location.id,
            enabled=True,
            description=f"Store scanned items in {location.name}",
        )
        session.add(control)
        await session.flush()
    return control


def _payload(location: StorageLocation, count: int = 0) -> dict:
    return {
        "id": str(location.id),
        "code": location.code,
        "name": location.name,
        "active": location.active,
        "inStockUnits": count,
    }


@router.get("/storage-locations")
async def list_storage_locations(session: SessionDep) -> dict:
    locations = (
        await session.scalars(select(StorageLocation).order_by(StorageLocation.name))
    ).all()
    counts = dict(
        (
            await session.execute(
                select(InventoryItem.storage_location_id, func.count(InventoryItem.id))
                .where(InventoryItem.status == InventoryStatus.IN_STOCK)
                .group_by(InventoryItem.storage_location_id)
            )
        ).all()
    )
    return {"storageLocations": [_payload(row, counts.get(row.id, 0)) for row in locations]}


@router.post("/storage-locations", status_code=status.HTTP_201_CREATED)
async def create_storage_location(
    payload: StorageLocationCreateRequest, session: SessionDep
) -> dict:
    name = standardize_name(payload.name)
    code = _code(name)
    existing = await session.scalar(
        select(StorageLocation).where(
            (func.lower(StorageLocation.name) == name.casefold())
            | (StorageLocation.code == code)
        )
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "storage location already exists")
    location = StorageLocation(code=code, name=name)
    session.add(location)
    await session.flush()
    return _payload(location)


@router.patch("/storage-locations/{location_id}")
async def update_storage_location(
    location_id: UUID, payload: StorageLocationUpdateRequest, session: SessionDep
) -> dict:
    location = await session.get(StorageLocation, location_id, with_for_update=True)
    if location is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "storage location not found")
    if payload.name is not None:
        location.name = standardize_name(payload.name)
    if payload.active is not None:
        location.active = payload.active
    await session.flush()
    return _payload(location)


@router.post("/storage-locations/{location_id}/control-barcode")
async def create_location_control_barcode(location_id: UUID, session: SessionDep) -> dict:
    location = await session.get(StorageLocation, location_id)
    if location is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "storage location not found")
    control = await ensure_control_barcode(session, location)
    return {"storageLocationId": str(location.id), "payload": control.payload}
