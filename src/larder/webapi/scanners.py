from __future__ import annotations

from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from larder.models import Product, ScanEvent, Scanner, ScannerContext, Store
from larder.models.enums import ScannerMode, ScanResult
from larder.webapi.common import get_session, household_settings, utcnow, web_api_router

router = web_api_router(tags=["scanners"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

FEED_TAGS = {
    ScanResult.APPLIED: "OK",
    ScanResult.IGNORED_NO_MODE: "IGN",
    ScanResult.IGNORED_NO_STOCK: "IGN",
    ScanResult.REJECTED_BAD_FRAME: "ERR",
    ScanResult.REJECTED_BAD_BARCODE: "ERR",
    ScanResult.REJECTED_UNKNOWN_COMMAND: "ERR",
    ScanResult.FAILED_RETRYABLE: "ERR",
    ScanResult.FAILED_FINAL: "ERR",
    ScanResult.RECEIVED: "…",
}


class ScannerModeRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")
    mode: ScannerMode | None = None


class ScannerStoreRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")
    store_id: UUID | None = None


async def _context_for_update(session: AsyncSession, scanner_id: UUID) -> ScannerContext:
    await session.execute(
        insert(ScannerContext)
        .values(scanner_id=scanner_id)
        .on_conflict_do_nothing(index_elements=[ScannerContext.scanner_id])
    )
    context = await session.scalar(
        select(ScannerContext).where(ScannerContext.scanner_id == scanner_id).with_for_update()
    )
    assert context is not None
    return context


@router.get("/scanners")
async def list_scanners(session: SessionDep) -> dict:
    now = utcnow()
    prefs = await household_settings(session)
    offline_after = timedelta(seconds=prefs.scanner_offline_after_seconds)
    scanners = (await session.scalars(select(Scanner).order_by(Scanner.name))).all()
    contexts = {c.scanner_id: c for c in (await session.scalars(select(ScannerContext))).all()}
    stores = {s.id: s for s in (await session.scalars(select(Store))).all()}
    last_seen_rows = dict(
        (
            await session.execute(
                select(ScanEvent.scanner_id, func.max(ScanEvent.received_at))
                .where(ScanEvent.scanner_id.is_not(None))
                .group_by(ScanEvent.scanner_id)
            )
        ).all()
    )

    payload = []
    for scanner in scanners:
        context = contexts.get(scanner.id)
        mode = None
        mode_expires = None
        store = None
        store_expires = None
        if context is not None:
            if context.mode is not None and (
                context.mode_expires_at is None or context.mode_expires_at > now
            ):
                mode = context.mode.value
                mode_expires = context.mode_expires_at
            if context.store_id is not None and (
                context.store_expires_at is None or context.store_expires_at > now
            ):
                store = stores.get(context.store_id)
                store_expires = context.store_expires_at

        feed_rows = (
            await session.execute(
                select(ScanEvent, Product.name)
                .outerjoin(Product, Product.barcode == ScanEvent.barcode)
                .where(ScanEvent.scanner_id == scanner.id)
                .order_by(ScanEvent.received_at.desc())
                .limit(8)
            )
        ).all()

        last_seen = last_seen_rows.get(scanner.id)
        payload.append(
            {
                "id": str(scanner.id),
                "code": scanner.code,
                "name": scanner.name,
                "enabled": scanner.enabled,
                "modeTimeoutSeconds": scanner.mode_timeout_seconds,
                "storeTimeoutSeconds": scanner.store_timeout_seconds,
                "mode": mode,
                "modeExpiresAt": mode_expires.isoformat() if mode_expires else None,
                "storeId": str(store.id) if store else None,
                "storeName": store.name if store else None,
                "storeExpiresAt": store_expires.isoformat() if store_expires else None,
                "lastSeenAt": last_seen.isoformat() if last_seen else None,
                "online": bool(last_seen and now - last_seen < offline_after),
                "feed": [
                    {
                        "at": event.received_at.isoformat(),
                        "tag": FEED_TAGS.get(event.result, "…"),
                        "ok": event.result == ScanResult.APPLIED,
                        "label": product_name
                        or event.barcode
                        or event.payload_raw
                        or event.result.value.lower(),
                        "mode": event.mode_at_processing.value
                        if event.mode_at_processing
                        else None,
                    }
                    for event, product_name in feed_rows
                ],
            }
        )
    return {"scanners": payload, "serverTime": now.isoformat()}


@router.post("/scanners/{scanner_id}/mode")
async def set_scanner_mode(
    scanner_id: UUID, payload: ScannerModeRequest, session: SessionDep
) -> dict:
    scanner = await session.get(Scanner, scanner_id)
    if scanner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scanner not found")
    context = await _context_for_update(session, scanner.id)
    now = utcnow()
    if payload.mode is None:
        context.mode = None
        context.mode_expires_at = None
    else:
        context.mode = payload.mode
        context.mode_expires_at = now + timedelta(seconds=scanner.mode_timeout_seconds)
    context.updated_at = now
    context.version += 1
    await session.flush()
    return {
        "id": str(scanner.id),
        "mode": context.mode.value if context.mode else None,
        "modeExpiresAt": context.mode_expires_at.isoformat() if context.mode_expires_at else None,
    }


@router.post("/scanners/{scanner_id}/store")
async def set_scanner_store(
    scanner_id: UUID, payload: ScannerStoreRequest, session: SessionDep
) -> dict:
    scanner = await session.get(Scanner, scanner_id)
    if scanner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scanner not found")
    context = await _context_for_update(session, scanner.id)
    now = utcnow()
    if payload.store_id is None:
        context.store_id = None
        context.store_expires_at = None
    else:
        store = await session.get(Store, payload.store_id)
        if store is None or not store.active:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "store not found or inactive")
        context.store_id = store.id
        context.store_expires_at = now + timedelta(seconds=scanner.store_timeout_seconds)
    context.updated_at = now
    context.version += 1
    await session.flush()
    return {
        "id": str(scanner.id),
        "storeId": str(context.store_id) if context.store_id else None,
        "storeExpiresAt": context.store_expires_at.isoformat()
        if context.store_expires_at
        else None,
    }
