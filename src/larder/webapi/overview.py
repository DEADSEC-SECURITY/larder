from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from larder.config import Settings
from larder.models import GroceryListItem, ScanEvent, Scanner
from larder.services.store_routing import load_routing_context, resolve_store_assignment
from larder.webapi.common import (  # noqa: I001
    expiration_kind,
    get_app_settings,
    get_session,
    household_settings,
    id_status,
    utcnow,
    web_api_router,
)
from larder.webapi.snapshot import load_snapshot

router = web_api_router(tags=["overview"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


@router.get("/overview")
async def overview(session: SessionDep, settings: SettingsDep) -> dict:
    prefs = await household_settings(session)
    offline_after = timedelta(seconds=prefs.scanner_offline_after_seconds)
    snapshot = await load_snapshot(session, settings, prefs.expiring_window_days)
    stocked = snapshot.stocked()

    expired_units = 0
    expiring_units = 0
    for summary in stocked:
        for item in summary.items:
            if item.expiration_date is None:
                continue
            days = (item.expiration_date - snapshot.today).days
            if days < 0:
                expired_units += 1
            if days <= snapshot.window_days:
                expiring_units += 1

    visible = [s for s in snapshot.summaries.values() if s.visible]
    pending = [s for s in visible if id_status(s.product) == "pending"]
    failed = [s for s in visible if id_status(s.product) == "failed"]
    no_price = [s for s in stocked if id_status(s.product) == "ok" and s.lowest_price is None]
    no_rule = [s for s in stocked if s.rule is None and s.has_unknown_date()]

    grocery_rows = (await session.scalars(select(GroceryListItem))).all()
    routing = await load_routing_context(session, [row.product_barcode for row in grocery_rows])
    grocery_items = []
    for row in grocery_rows:
        quantity = row.automatic_quantity + row.manual_quantity
        if quantity <= 0:
            continue
        summary = snapshot.summaries.get(row.product_barcode)
        assignment = resolve_store_assignment(
            barcode=row.product_barcode,
            preferred_store_id=row.preferred_store_id,
            store_prices=summary.store_prices if summary else {},
            stores=snapshot.stores,
            context=routing,
        )
        price = (
            summary.store_prices.get(assignment.store_id)
            if summary is not None and assignment.store_id is not None
            else None
        )
        store = snapshot.stores.get(assignment.store_id) if assignment.store_id else None
        grocery_items.append(
            {
                "name": summary.product.name if summary and summary.product.name else "Item",
                "quantity": quantity,
                "checked": row.checked_quantity >= quantity,
                "unitPrice": price,
                "storeName": store.name if store else None,
                "storeAssignmentSource": assignment.source,
            }
        )
    unchecked = [g for g in grocery_items if not g["checked"]]
    grocery_estimate = round(sum((g["unitPrice"] or 0) * g["quantity"] for g in unchecked), 2)

    now = utcnow()
    scanners = (await session.scalars(select(Scanner).where(Scanner.enabled.is_(True)))).all()
    last_seen = dict(
        (
            await session.execute(
                select(ScanEvent.scanner_id, func.max(ScanEvent.received_at))
                .where(ScanEvent.scanner_id.is_not(None))
                .group_by(ScanEvent.scanner_id)
            )
        ).all()
    )
    offline = [
        s.name
        for s in scanners
        if last_seen.get(s.id) is not None and now - last_seen[s.id] >= offline_after
    ]

    expiring_products = []
    for summary in snapshot.expiring()[:6]:
        soonest = summary.soonest()
        store = snapshot.stores.get(soonest.store_id) if soonest.store_id else None
        expiring_products.append(
            {
                "barcode": summary.product.barcode,
                "name": summary.product.name or "Unidentified item",
                "brand": summary.product.brand,
                "storeName": store.name if store else None,
                "inStock": summary.in_stock,
                "date": soonest.expiration_date.isoformat(),
                "kind": expiration_kind(soonest),
                "daysLeft": summary.days_left(snapshot.today),
            }
        )

    attention_total = len(pending) + len(failed) + len(no_price) + len(no_rule) + len(offline)
    return {
        "windowDays": snapshot.window_days,
        "metrics": {
            "unitsInStock": sum(s.in_stock for s in stocked),
            "stockedProducts": len(stocked),
            "expiringUnits": expiring_units,
            "expiredUnits": expired_units,
            "groceryCount": len(grocery_items),
            "groceryEstimate": grocery_estimate,
            "attentionCount": attention_total,
            "awaitingIdentification": len(pending) + len(failed),
        },
        "expiring": expiring_products,
        "attention": {
            "pendingIdentification": len(pending),
            "failedLookups": len(failed),
            "missingPrices": len(no_price),
            "missingRules": len(no_rule),
            "offlineScanners": offline,
        },
        "groceryPreview": {
            "items": [
                {
                    "name": g["name"],
                    "quantity": g["quantity"],
                    "unitPrice": g["unitPrice"],
                    "storeName": g["storeName"],
                    "storeAssignmentSource": g["storeAssignmentSource"],
                }
                for g in unchecked[:3]
            ],
            "moreCount": max(0, len(unchecked) - 3),
            "totalCount": len(grocery_items),
            "estimate": grocery_estimate,
        },
        "currency": settings.currency_code,
    }
