from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from larder.models import (
    InventoryEvent,
    InventoryItem,
    Product,
    ScanEvent,
    Scanner,
)
from larder.models.enums import InventoryEventType, ScanResult
from larder.webapi.common import get_session, web_api_router

router = web_api_router(tags=["activity"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

EVENT_KIND = {
    InventoryEventType.ADDED: "added",
    InventoryEventType.REMOVED: "removed",
    InventoryEventType.TRASHED: "wasted",
    InventoryEventType.RESTORED: "restored",
    InventoryEventType.EXPIRATION_CHANGED: "changed",
    InventoryEventType.STORE_CHANGED: "changed",
    InventoryEventType.PRICE_CHANGED: "changed",
}

SCAN_FAILURES = {
    ScanResult.IGNORED_NO_MODE: ("ignored", "scan ignored · no operating mode"),
    ScanResult.IGNORED_NO_STOCK: ("ignored", "scan ignored · nothing in stock"),
    ScanResult.REJECTED_BAD_FRAME: ("failed", "rejected · malformed frame"),
    ScanResult.REJECTED_BAD_BARCODE: ("failed", "rejected · invalid barcode"),
    ScanResult.REJECTED_UNKNOWN_COMMAND: ("failed", "rejected · unknown command"),
    ScanResult.FAILED_RETRYABLE: ("failed", "processing failed · will retry"),
    ScanResult.FAILED_FINAL: ("failed", "processing failed"),
}


def _detail_for(event: InventoryEvent) -> str | None:
    if event.event_type == InventoryEventType.EXPIRATION_CHANGED:
        after = (event.after_state or {}).get("expiration_date")
        kind = (event.after_state or {}).get("kind")
        if after:
            return f"expiration set · {kind or 'confirmed'}"
        return "expiration cleared"
    if event.event_type == InventoryEventType.STORE_CHANGED:
        return "store changed"
    if event.event_type == InventoryEventType.PRICE_CHANGED:
        return "price updated"
    return event.reason


@router.get("/activity")
async def activity(session: SessionDep, limit: int = Query(default=100, ge=1, le=500)) -> dict:
    inventory_rows = (
        await session.execute(
            select(InventoryEvent, InventoryItem.product_barcode, Product.name, Scanner.name)
            .join(InventoryItem, InventoryItem.id == InventoryEvent.inventory_item_id)
            .join(Product, Product.barcode == InventoryItem.product_barcode)
            .outerjoin(ScanEvent, ScanEvent.id == InventoryEvent.scan_event_id)
            .outerjoin(Scanner, Scanner.id == ScanEvent.scanner_id)
            .order_by(InventoryEvent.occurred_at.desc())
            .limit(limit)
        )
    ).all()

    scan_rows = (
        await session.execute(
            select(ScanEvent, Scanner.name)
            .outerjoin(Scanner, Scanner.id == ScanEvent.scanner_id)
            .where(ScanEvent.result.in_(list(SCAN_FAILURES)))
            .order_by(ScanEvent.received_at.desc())
            .limit(limit)
        )
    ).all()

    events = []
    for event, barcode, product_name, scanner_name in inventory_rows:
        kind = EVENT_KIND.get(event.event_type, "changed")
        expiration_change = event.event_type == InventoryEventType.EXPIRATION_CHANGED
        if event.actor_type == "SYSTEM" and expiration_change:
            kind = "estimated"
        if event.actor_type == "SCANNER":
            source = scanner_name or event.actor_id or "scanner"
        elif event.actor_type == "SYSTEM":
            source = "system"
        else:
            source = "admin"
        events.append(
            {
                "at": event.occurred_at.isoformat(),
                "kind": kind,
                "product": product_name or "Unidentified item",
                "barcode": barcode,
                "detail": _detail_for(event),
                "source": source,
            }
        )
    for event, scanner_name in scan_rows:
        kind, detail = SCAN_FAILURES[event.result]
        label = event.barcode or event.payload_raw or "scan"
        events.append(
            {
                "at": event.received_at.isoformat(),
                "kind": kind,
                "product": label,
                "barcode": event.barcode,
                "detail": event.error_code.lower().replace("_", " ")
                if event.error_code
                else detail,
                "source": scanner_name or "scanner",
            }
        )

    events.sort(key=lambda e: e["at"], reverse=True)
    return {"events": events[:limit]}
