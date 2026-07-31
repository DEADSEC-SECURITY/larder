from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from larder.models import ControlBarcode, StorageLocation, Store
from larder.webapi.common import get_session, web_api_router

router = web_api_router(tags=["commands"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]

# Mode/clear commands before per-store commands, then per-location commands,
# in scan-workflow order.
_TYPE_ORDER = {
    "SET_MODE": 0,
    "CLEAR_MODE": 1,
    "SET_STORE": 2,
    "CLEAR_STORE": 3,
    "SET_LOCATION": 4,
    "CLEAR_LOCATION": 5,
}


@router.get("/commands")
async def list_commands(session: SessionDep) -> dict:
    controls = (await session.scalars(select(ControlBarcode))).all()
    stores = {s.id: s for s in (await session.scalars(select(Store))).all()}
    locations = {
        loc.id: loc for loc in (await session.scalars(select(StorageLocation))).all()
    }

    payload = []
    for control in controls:
        store = stores.get(control.store_id) if control.store_id else None
        location = (
            locations.get(control.storage_location_id)
            if control.storage_location_id
            else None
        )
        payload.append(
            {
                "payload": control.payload,
                "commandType": control.command_type,
                "mode": control.mode.value if control.mode else None,
                "storeId": str(store.id) if store else None,
                "storeName": store.name if store else None,
                "storeActive": store.active if store else None,
                "storageLocationId": str(location.id) if location else None,
                "storageLocationName": location.name if location else None,
                "storageLocationActive": location.active if location else None,
                "enabled": control.enabled,
                "description": control.description,
            }
        )
    payload.sort(
        key=lambda row: (
            _TYPE_ORDER.get(row["commandType"], 9),
            (row["storeName"] or row["storageLocationName"] or "").lower(),
            row["payload"],
        )
    )
    return {"commands": payload}
