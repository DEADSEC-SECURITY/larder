from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID, uuid4

import structlog
from fastapi import BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from larder.config import Settings
from larder.domain.names import standardize_name
from larder.models import (
    EnrichmentJob,
    GroceryListItem,
    Product,
    ProductStockPolicy,
    StockGroup,
    StockGroupPolicy,
    StockGroupProduct,
    StockGroupShoppingItem,
    Store,
)
from larder.models.enums import EnrichmentStatus
from larder.scanner.parser import BarcodeError, normalize_gtin
from larder.services.stock_matching import match_product_to_stock_groups
from larder.services.stock_policy import recalculate_stock_policy
from larder.services.store_routing import (
    RoutingContext,
    StoreAssignment,
    load_routing_context,
    resolve_rule_assignment,
    resolve_store_assignment,
)
from larder.webapi.common import (  # noqa: I001
    MANUAL_BARCODE_FORMAT,
    MANUAL_BARCODE_PREFIX,
    get_app_settings,
    get_session,
    household_settings,
    new_manual_barcode,
    utcnow,
    web_api_router,
)
from larder.webapi.mutations import QuickAddRequest, quick_add
from larder.webapi.snapshot import CatalogSnapshot, load_snapshot

router = web_api_router(tags=["grocery"])
logger = structlog.get_logger(__name__)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]

AUTO_REASONS = {
    "BELOW_REORDER_POINT": "Below minimum",
}


class GroceryAddRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")
    barcode: str | None = None
    stock_group_id: UUID | None = None
    name: str | None = Field(default=None, max_length=200)
    quantity: int = Field(default=1, ge=1, le=99)
    product_kind: Literal[
        "FOOD", "HOUSEHOLD", "PERSONAL_CARE", "MEDICAL", "PET", "OFFICE", "OTHER"
    ] = "OTHER"

    @model_validator(mode="after")
    def reference_required(self) -> GroceryAddRequest:
        if self.stock_group_id is not None:
            if self.barcode or (self.name and self.name.strip()):
                raise ValueError("stockGroupId cannot be combined with barcode or name")
            return self
        if not self.barcode and not (self.name and self.name.strip()):
            raise ValueError("stockGroupId, barcode, or name is required")
        return self


class GroceryUpdateRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")
    checked: bool | None = None
    quantity: int | None = Field(default=None, ge=0, le=99)
    preferred_store_id: UUID | None = None
    # Price per unit as seen on the shelf. Overrides the lowest-observed
    # estimate; send null to fall back to it again.
    unit_price: Decimal | None = Field(default=None, ge=0, le=100000)


class ShoppingScanRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")
    barcode: str = Field(min_length=1, max_length=64)
    # When nothing on the list matches, register the product and add it as an
    # already-checked row rather than making the shopper remember it.
    add_if_missing: bool = True


class ShoppingCheckoutRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")
    # Blank keeps every row's own resolved store.
    store_id: UUID | None = None


def _effective_price(
    entered: Decimal | None, observed: float | None
) -> tuple[float | None, str | None]:
    """A price typed at the shelf beats the lowest price we have on record."""
    if entered is not None:
        return float(entered), "entered"
    if observed is not None:
        return observed, "observed"
    return None, None


def _demand_reason(
    *,
    manual_quantity: int,
    automatic_quantity: int,
    auto_reason: str | None,
    on_hand: int,
    policy: ProductStockPolicy | StockGroupPolicy | None,
) -> str:
    if manual_quantity > 0 and automatic_quantity > 0:
        return f"{AUTO_REASONS.get(auto_reason, 'Auto')} + added manually"
    if automatic_quantity <= 0:
        return "Added manually"
    reason = AUTO_REASONS.get(auto_reason or "", auto_reason or "Automatic")
    if policy is None:
        return reason
    if on_hand == 0:
        return f"Out of stock · restock to {policy.target_stock}"
    return f"Below minimum · {on_hand} of {policy.reorder_point}"


def _group_store_assignment(
    *,
    member_barcodes: list[str],
    representative_barcode: str | None,
    preferred_store_id: UUID | None,
    store_prices: dict[UUID, float],
    snapshot: CatalogSnapshot,
    routing: RoutingContext,
) -> StoreAssignment:
    if preferred_store_id is not None:
        return resolve_store_assignment(
            barcode=representative_barcode or "",
            preferred_store_id=preferred_store_id,
            store_prices=store_prices,
            stores=snapshot.stores,
            context=routing,
        )

    rule_assignments = [
        resolve_rule_assignment(
            barcode=barcode,
            stores=snapshot.stores,
            context=routing,
        )
        for barcode in member_barcodes
    ]
    active_rule_assignments = [
        assignment
        for assignment in rule_assignments
        if assignment.store_id in snapshot.stores and snapshot.stores[assignment.store_id].active
    ]
    routed_store_ids = {assignment.store_id for assignment in active_rule_assignments}
    if (
        member_barcodes
        and len(active_rule_assignments) == len(member_barcodes)
        and len(routed_store_ids) == 1
    ):
        store_id = active_rule_assignments[0].store_id
        return StoreAssignment(
            store_id=store_id,
            source="stock_group_rules",
            reason="All compatible products route to this store",
        )

    active_prices = {
        store_id: price
        for store_id, price in store_prices.items()
        if store_id in snapshot.stores and snapshot.stores[store_id].active
    }
    if active_prices:
        store_id = min(active_prices, key=active_prices.get)
        return StoreAssignment(
            store_id=store_id,
            source="price_history",
            reason="Lowest observed compatible-product price",
        )
    return StoreAssignment(store_id=None, source="none")


@router.get("/shopping")
@router.get("/grocery")
async def grocery_list(session: SessionDep, settings: SettingsDep) -> dict:
    prefs = await household_settings(session)
    snapshot = await load_snapshot(session, settings, prefs.expiring_window_days)
    # Rows the shopper deleted stay hidden until their product genuinely drops
    # below the reorder point again (see services.stock_policy).
    product_rows = (
        await session.scalars(select(GroceryListItem).where(GroceryListItem.dismissed_at.is_(None)))
    ).all()
    group_rows = (
        await session.execute(
            select(StockGroupShoppingItem, StockGroup, StockGroupPolicy)
            .join(StockGroup, StockGroup.id == StockGroupShoppingItem.stock_group_id)
            .outerjoin(
                StockGroupPolicy,
                StockGroupPolicy.stock_group_id == StockGroupShoppingItem.stock_group_id,
            )
            .where(StockGroupShoppingItem.dismissed_at.is_(None))
        )
    ).all()
    group_ids = [group.id for _, group, _ in group_rows]
    membership_rows = (
        (
            await session.execute(
                select(
                    StockGroupProduct.stock_group_id,
                    StockGroupProduct.product_barcode,
                ).where(StockGroupProduct.stock_group_id.in_(group_ids))
            )
        ).all()
        if group_ids
        else []
    )
    member_barcodes: dict[UUID, list[str]] = defaultdict(list)
    for group_id, barcode in membership_rows:
        member_barcodes[group_id].append(barcode)
    routed_barcodes = {row.product_barcode for row in product_rows} | {
        barcode for _, barcode in membership_rows
    }
    routing = await load_routing_context(session, routed_barcodes)

    items = []
    for row in product_rows:
        quantity = row.automatic_quantity + row.manual_quantity
        if quantity <= 0:
            continue
        summary = snapshot.summaries.get(row.product_barcode)
        product = summary.product if summary else None
        assignment = resolve_store_assignment(
            barcode=row.product_barcode,
            preferred_store_id=row.preferred_store_id,
            store_prices=summary.store_prices if summary else {},
            stores=snapshot.stores,
            context=routing,
        )
        store_id = assignment.store_id
        observed = summary.store_prices.get(store_id) if summary and store_id else None
        price, price_source = _effective_price(row.unit_price, observed)
        store = snapshot.stores.get(store_id) if store_id else None
        items.append(
            {
                "itemType": "product",
                "barcode": row.product_barcode,
                "stockGroupId": None,
                "name": product.name if product and product.name else "Unidentified item",
                "brand": summary.brands[0].name
                if summary and summary.brands
                else product.brand
                if product
                else None,
                "productKind": product.product_kind.lower() if product else "other",
                "quantity": quantity,
                "checked": row.checked_quantity >= quantity,
                "reason": _demand_reason(
                    manual_quantity=row.manual_quantity,
                    automatic_quantity=row.automatic_quantity,
                    auto_reason=row.auto_reason,
                    on_hand=summary.in_stock if summary else 0,
                    policy=summary.policy if summary else None,
                ),
                "storeId": str(store.id) if store else None,
                "storeName": store.name if store else None,
                "unitPrice": price,
                "priceKnown": price is not None,
                "priceSource": price_source,
                "checkedQuantity": min(row.checked_quantity, quantity),
                "scannedBarcodes": list(row.scanned_barcodes or []),
                "storeAssignment": assignment.payload(snapshot.stores),
            }
        )

    for row, group, policy in group_rows:
        quantity = row.automatic_quantity + row.manual_quantity
        if quantity <= 0:
            continue
        barcodes = sorted(member_barcodes[group.id])
        summaries = [
            snapshot.summaries[barcode] for barcode in barcodes if barcode in snapshot.summaries
        ]
        store_prices: dict[UUID, float] = {}
        for summary in summaries:
            for store_id, price in summary.store_prices.items():
                current = store_prices.get(store_id)
                store_prices[store_id] = price if current is None else min(current, price)
        assignment = _group_store_assignment(
            member_barcodes=barcodes,
            representative_barcode=group.representative_product_barcode,
            preferred_store_id=row.preferred_store_id,
            store_prices=store_prices,
            snapshot=snapshot,
            routing=routing,
        )
        store_id = assignment.store_id
        observed = store_prices.get(store_id) if store_id else None
        price, price_source = _effective_price(row.unit_price, observed)
        store = snapshot.stores.get(store_id) if store_id else None
        on_hand = sum(summary.in_stock for summary in summaries)
        items.append(
            {
                "itemType": "stock_group",
                "barcode": f"group:{group.id}",
                "stockGroupId": str(group.id),
                "name": group.name,
                "brand": None,
                "scopeLabel": "Any compatible brand or size",
                "productKind": group.product_kind.lower(),
                "quantity": quantity,
                "checked": row.checked_quantity >= quantity,
                "reason": _demand_reason(
                    manual_quantity=row.manual_quantity,
                    automatic_quantity=row.automatic_quantity,
                    auto_reason=row.auto_reason,
                    on_hand=on_hand,
                    policy=policy,
                ),
                "memberBarcodes": barcodes,
                "memberCount": len(barcodes),
                "inStock": on_hand,
                "storeId": str(store.id) if store else None,
                "storeName": store.name if store else None,
                "unitPrice": price,
                "priceKnown": price is not None,
                "priceSource": price_source,
                "checkedQuantity": min(row.checked_quantity, quantity),
                "scannedBarcodes": list(row.scanned_barcodes or []),
                "storeAssignment": assignment.payload(snapshot.stores),
            }
        )

    items.sort(key=lambda item: (item["storeName"] or "\uffff", item["name"] or ""))
    unchecked_total = sum(
        (item["unitPrice"] or 0) * item["quantity"] for item in items if not item["checked"]
    )
    checked_total = sum(
        (item["unitPrice"] or 0) * item["quantity"] for item in items if item["checked"]
    )
    return {
        "items": items,
        "estimatedUncheckedTotal": round(unchecked_total, 2),
        "checkedTotal": round(checked_total, 2),
        "currency": settings.currency_code,
    }


async def _add_group_quantity(
    session: AsyncSession, stock_group_id: UUID, quantity: int
) -> StockGroupShoppingItem:
    group = await session.get(StockGroup, stock_group_id)
    if group is None or not group.active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "active stock group not found")
    row = await session.get(StockGroupShoppingItem, stock_group_id, with_for_update=True)
    if row is None:
        row = StockGroupShoppingItem(
            stock_group_id=stock_group_id,
            automatic_quantity=0,
            manual_quantity=quantity,
            checked_quantity=0,
        )
        session.add(row)
    else:
        row.manual_quantity += quantity
    return row


@router.post("/shopping", status_code=status.HTTP_201_CREATED)
@router.post("/grocery", status_code=status.HTTP_201_CREATED)
async def add_grocery_item(payload: GroceryAddRequest, session: SessionDep) -> dict:
    if payload.stock_group_id is not None:
        row = await _add_group_quantity(session, payload.stock_group_id, payload.quantity)
        await session.flush()
        return {
            "itemType": "stock_group",
            "barcode": f"group:{payload.stock_group_id}",
            "stockGroupId": str(payload.stock_group_id),
            "quantity": row.automatic_quantity + row.manual_quantity,
        }

    if payload.barcode:
        product = await session.get(Product, payload.barcode)
        if product is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "product not found")
    else:
        product = Product(
            barcode=new_manual_barcode(),
            barcode_format=MANUAL_BARCODE_FORMAT,
            name=standardize_name(payload.name),
            product_kind=payload.product_kind,
            identification_source="MANUAL",
            field_sources={"name": "USER", "product_kind": "USER"},
            enrichment_status=EnrichmentStatus.SUCCEEDED,
            enriched_at=utcnow(),
        )
        session.add(product)
        await session.flush()

    await match_product_to_stock_groups(session, product)
    membership = await session.scalar(
        select(StockGroupProduct).where(StockGroupProduct.product_barcode == product.barcode)
    )
    if membership is not None:
        group_row = await _add_group_quantity(session, membership.stock_group_id, payload.quantity)
        await session.flush()
        return {
            "itemType": "stock_group",
            "barcode": f"group:{membership.stock_group_id}",
            "stockGroupId": str(membership.stock_group_id),
            "quantity": group_row.automatic_quantity + group_row.manual_quantity,
        }

    row = await session.get(GroceryListItem, product.barcode, with_for_update=True)
    if row is None:
        row = GroceryListItem(
            product_barcode=product.barcode,
            automatic_quantity=0,
            manual_quantity=payload.quantity,
            checked_quantity=0,
        )
        session.add(row)
    else:
        row.manual_quantity += payload.quantity
    await session.flush()
    return {
        "itemType": "product",
        "barcode": product.barcode,
        "stockGroupId": None,
        "quantity": row.automatic_quantity + row.manual_quantity,
    }


def _stock_group_id(identifier: str) -> UUID | None:
    if not identifier.startswith("group:"):
        return None
    try:
        return UUID(identifier.removeprefix("group:"))
    except ValueError:
        return None


async def _apply_row_update(
    session: AsyncSession,
    row: GroceryListItem | StockGroupShoppingItem,
    payload: GroceryUpdateRequest,
) -> tuple[int, bool]:
    if payload.quantity is not None:
        row.manual_quantity = max(0, payload.quantity - row.automatic_quantity)
    if "unit_price" in payload.model_fields_set:
        row.unit_price = payload.unit_price
    if "preferred_store_id" in payload.model_fields_set:
        if payload.preferred_store_id is not None:
            store = await session.get(Store, payload.preferred_store_id)
            if store is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "store not found")
        row.preferred_store_id = payload.preferred_store_id
    total = row.automatic_quantity + row.manual_quantity
    if payload.checked is not None:
        row.checked_quantity = total if payload.checked else 0
    checked = row.checked_quantity >= total and total > 0
    return total, checked


@router.patch("/shopping/{identifier}")
@router.patch("/grocery/{identifier}")
async def update_grocery_item(
    identifier: str, payload: GroceryUpdateRequest, session: SessionDep
) -> dict:
    group_id = _stock_group_id(identifier)
    if identifier.startswith("group:"):
        if group_id is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "stock group item not found")
        group_row = await session.get(StockGroupShoppingItem, group_id, with_for_update=True)
        if group_row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "stock group item not found")
        total, checked = await _apply_row_update(session, group_row, payload)
        if total == 0 and group_row.checked_quantity == 0 and group_row.preferred_store_id is None:
            await session.delete(group_row)
        await session.flush()
        return {
            "itemType": "stock_group",
            "barcode": identifier,
            "stockGroupId": str(group_id),
            "quantity": total,
            "checked": checked,
            "preferredStoreId": str(group_row.preferred_store_id)
            if group_row.preferred_store_id
            else None,
        }

    row = await session.get(GroceryListItem, identifier, with_for_update=True)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "grocery item not found")
    total, checked = await _apply_row_update(session, row, payload)
    if total == 0 and row.checked_quantity == 0 and row.preferred_store_id is None:
        await session.delete(row)
    await session.flush()
    return {
        "itemType": "product",
        "barcode": identifier,
        "stockGroupId": None,
        "quantity": total,
        "checked": checked,
        "preferredStoreId": str(row.preferred_store_id) if row.preferred_store_id else None,
    }


@router.post("/shopping/clear-checks")
@router.post("/grocery/clear-checks")
async def clear_checks(session: SessionDep) -> dict:
    """Remove completed rows while leaving unchecked and partially checked rows alone."""
    removed = 0
    for model in (GroceryListItem, StockGroupShoppingItem):
        rows = (
            await session.scalars(
                select(model)
                .where(
                    model.checked_quantity > 0,
                    model.checked_quantity >= model.automatic_quantity + model.manual_quantity,
                )
                .with_for_update()
            )
        ).all()
        for row in rows:
            await _remove_list_row(session, row)
            removed += 1
    await session.flush()
    return {"ok": True, "removed": removed}


async def _remove_list_row(
    session: AsyncSession, row: GroceryListItem | StockGroupShoppingItem
) -> None:
    """Delete manual demand and dismiss automatic demand so it cannot pop back in."""
    row.manual_quantity = 0
    row.checked_quantity = 0
    row.unit_price = None
    row.scanned_barcodes = []
    if row.automatic_quantity > 0:
        row.dismissed_at = utcnow()
    else:
        await session.delete(row)


@router.delete("/shopping/{identifier}")
@router.delete("/grocery/{identifier}")
async def delete_grocery_item(identifier: str, session: SessionDep) -> dict:
    """Remove a row for good.

    Zeroing the quantity is not enough on its own: an automatic row is
    regenerated the moment stock is recalculated. Marking it dismissed keeps it
    off the list until the product falls below its reorder point again.
    """
    row = await _load_row(session, identifier, for_update=True)
    await _remove_list_row(session, row)
    await session.flush()
    return {"ok": True, "identifier": identifier}


async def _load_row(
    session: AsyncSession, identifier: str, *, for_update: bool = False
) -> GroceryListItem | StockGroupShoppingItem:
    if identifier.startswith("group:"):
        group_id = _stock_group_id(identifier)
        row = (
            await session.get(StockGroupShoppingItem, group_id, with_for_update=for_update)
            if group_id is not None
            else None
        )
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "stock group item not found")
        return row
    row = await session.get(GroceryListItem, identifier, with_for_update=for_update)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "grocery item not found")
    return row


def _row_total(row: GroceryListItem | StockGroupShoppingItem) -> int:
    return row.automatic_quantity + row.manual_quantity


async def _match_scanned_row(
    session: AsyncSession, barcode: str
) -> tuple[GroceryListItem | StockGroupShoppingItem | None, str | None, UUID | None]:
    """Find the list row a scanned barcode satisfies.

    Exact product rows win. Otherwise the barcode's stock group is consulted —
    a group row means "any compatible brand or size", so any accepted member
    satisfies it. Membership is unique per barcode, which is exactly why
    scanning dairy milk can never tick off an oat milk row: they are different
    groups (or oat milk is its own product row).
    """
    product_row = await session.get(GroceryListItem, barcode, with_for_update=True)
    if product_row is not None and product_row.dismissed_at is None and _row_total(product_row) > 0:
        return product_row, "product", None

    membership = (
        await session.scalars(
            select(StockGroupProduct.stock_group_id).where(
                StockGroupProduct.product_barcode == barcode
            )
        )
    ).first()
    if membership is None:
        return None, None, None
    group_row = await session.get(StockGroupShoppingItem, membership, with_for_update=True)
    if group_row is None or group_row.dismissed_at is not None or _row_total(group_row) <= 0:
        return None, None, membership
    return group_row, "stock_group", membership


@router.post("/shopping/scan")
async def scan_shopping_item(
    payload: ShoppingScanRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    session: SessionDep,
) -> dict:
    """Tick one unit off the list for a barcode scanned at the shelf."""
    raw = payload.barcode.strip()
    if raw.upper().startswith(MANUAL_BARCODE_PREFIX):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {
                "code": "MANUAL_BARCODE",
                "detail": "manual product ids cannot be scanned",
            },
        )
    try:
        normalized = normalize_gtin(raw, accept_invalid_check_digit=True)
    except BarcodeError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"code": exc.code, "detail": exc.detail},
        ) from exc
    barcode = normalized.barcode

    row, item_type, group_id = await _match_scanned_row(session, barcode)
    added = False

    if row is None:
        if not payload.add_if_missing:
            product = await session.get(Product, barcode)
            return {
                "matched": False,
                "added": False,
                "barcode": barcode,
                "name": product.name if product and product.name else None,
            }
        # Unplanned purchase: register the product if new, then put it on the
        # list already in the cart.
        _, product_created = await _ensure_product(session, barcode, normalized.barcode_format)
        if product_created:
            enrichment_worker = getattr(request.app.state, "enrichment_worker", None)
            if enrichment_worker is not None:
                background_tasks.add_task(_enrich_scanned_product, enrichment_worker, barcode)
        row = await session.get(GroceryListItem, barcode, with_for_update=True)
        if row is None:
            row = GroceryListItem(
                product_barcode=barcode,
                automatic_quantity=0,
                manual_quantity=1,
                checked_quantity=0,
            )
            session.add(row)
        else:
            row.manual_quantity = max(1, row.manual_quantity)
        row.dismissed_at = None
        item_type = "product"
        added = True

    total = _row_total(row)
    # Each scan is one physical item in the cart, so a x3 row needs three scans.
    row.checked_quantity = min(total, row.checked_quantity + 1)
    row.scanned_barcodes = [*(row.scanned_barcodes or []), barcode]
    await session.flush()

    product = await session.get(Product, barcode)
    identifier = f"group:{group_id}" if item_type == "stock_group" else barcode
    name = product.name if product and product.name else None
    if item_type == "stock_group" and group_id is not None:
        group = await session.get(StockGroup, group_id)
        name = group.name if group else name

    return {
        "matched": True,
        "added": added,
        "itemType": item_type,
        "identifier": identifier,
        "barcode": barcode,
        "stockGroupId": str(group_id) if group_id else None,
        "name": name,
        "quantity": total,
        "checkedQuantity": row.checked_quantity,
        "checked": row.checked_quantity >= total > 0,
    }


async def _enrich_scanned_product(worker, barcode: str) -> None:
    """Resolve an unknown phone scan now without delaying its HTTP response."""
    try:
        await worker.run_once(barcode=barcode)
    except Exception as error:  # noqa: BLE001 - the queued retry remains authoritative
        logger.warning(
            "shopping_scan_immediate_enrichment_error",
            barcode=barcode,
            error=type(error).__name__,
        )


async def _ensure_product(
    session: AsyncSession, barcode: str, barcode_format: str
) -> tuple[Product, bool]:
    """Register a scanned barcode the catalogue has never seen, with enrichment queued."""
    product = await session.get(Product, barcode)
    if product is not None:
        product.archived_at = None
        return product, False
    product = Product(
        barcode=barcode,
        barcode_format=barcode_format,
        enrichment_status=EnrichmentStatus.PENDING,
    )
    session.add(product)
    await session.flush()
    await session.execute(
        pg_insert(EnrichmentJob)
        .values(
            id=uuid4(),
            product_barcode=barcode,
            status=EnrichmentStatus.PENDING,
            next_attempt_at=utcnow(),
        )
        .on_conflict_do_nothing(index_elements=[EnrichmentJob.product_barcode])
    )
    return product, True


@router.post("/shopping/checkout")
async def checkout(
    payload: ShoppingCheckoutRequest, session: SessionDep, settings: SettingsDep
) -> dict:
    """Turn everything in the cart into inventory without re-scanning at home."""
    listing = await grocery_list(session, settings)
    checked = [item for item in listing["items"] if item["checkedQuantity"] > 0]
    if not checked:
        return {"added": 0, "items": [], "skipped": []}

    added_units = 0
    added_items: list[dict] = []
    skipped: list[dict] = []

    for item in checked:
        quantity = item["checkedQuantity"]
        # A group row is satisfied by whichever member actually went in the
        # cart; fall back to its representative product when nothing was
        # scanned, and refuse to guess when there is no representative either.
        barcodes = list(item.get("scannedBarcodes") or [])
        if item["itemType"] == "stock_group":
            if len(barcodes) < quantity:
                group = await session.get(StockGroup, UUID(item["stockGroupId"]))
                fallback = group.representative_product_barcode if group else None
                if fallback is None:
                    skipped.append(
                        {
                            "identifier": item["barcode"],
                            "name": item["name"],
                            "reason": "no scanned product and no representative for the group",
                        }
                    )
                    continue
                barcodes += [fallback] * (quantity - len(barcodes))
        else:
            barcodes = [item["barcode"]] * quantity

        store_id = payload.store_id or (UUID(item["storeId"]) if item["storeId"] else None)
        unit_price = item["unitPrice"]
        counts: dict[str, int] = defaultdict(int)
        for barcode in barcodes[:quantity]:
            counts[barcode] += 1

        created_here = 0
        for barcode, count in counts.items():
            request = QuickAddRequest(
                barcode=barcode,
                quantity=count,
                store_id=store_id,
                price_total=round(unit_price * count, 2) if unit_price is not None else None,
            )
            try:
                result = await quick_add(request, session, settings)
            except HTTPException as error:
                skipped.append(
                    {
                        "identifier": item["barcode"],
                        "name": item["name"],
                        "reason": str(error.detail),
                    }
                )
                continue
            created_here += result["created"]
            added_items.append(
                {"barcode": barcode, "name": item["name"], "created": result["created"]}
            )

        if created_here == 0:
            # Nothing was stocked, so the item was not really bought: leave the
            # row (and its checks) alone for the shopper to fix and retry.
            continue

        added_units += created_here
        row = await _load_row(session, item["barcode"], for_update=True)
        # Bought, so it leaves the list. Stock just rose, so the recalculation
        # below normally clears the automatic demand too.
        row.checked_quantity = 0
        row.manual_quantity = 0
        row.unit_price = None
        row.scanned_barcodes = []
        await session.flush()
        if isinstance(row, GroceryListItem):
            await recalculate_stock_policy(session, row.product_barcode)

    return {"added": added_units, "items": added_items, "skipped": skipped}
