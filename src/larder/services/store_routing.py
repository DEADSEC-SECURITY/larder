from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from larder.models import (
    Brand,
    InventoryEvent,
    InventoryItem,
    ProductBrand,
    ProductCategory,
    Store,
    StoreRoutingRule,
)
from larder.models.enums import InventoryEventType, InventoryStatus


@dataclass
class RoutingContext:
    rules: list[StoreRoutingRule] = field(default_factory=list)
    product_brands: dict[str, set[UUID]] = field(default_factory=dict)
    product_categories: dict[str, set[str]] = field(default_factory=dict)
    brand_names: dict[UUID, str] = field(default_factory=dict)
    category_names: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class StoreAssignment:
    store_id: UUID | None
    source: str
    reason: str | None = None
    behavior: str | None = None
    rule_id: UUID | None = None
    warning: str | None = None

    def payload(self, stores: dict[UUID, Store]) -> dict:
        store = stores.get(self.store_id) if self.store_id else None
        return {
            "storeId": str(self.store_id) if self.store_id else None,
            "storeName": store.name if store else None,
            "storeActive": store.active if store else None,
            "source": self.source,
            "reason": self.reason,
            "behavior": self.behavior.lower() if self.behavior else None,
            "ruleId": str(self.rule_id) if self.rule_id else None,
            "warning": self.warning,
        }


async def load_routing_context(
    session: AsyncSession, barcodes: list[str] | set[str] | tuple[str, ...]
) -> RoutingContext:
    context = RoutingContext()
    context.rules = (
        await session.scalars(select(StoreRoutingRule).where(StoreRoutingRule.enabled.is_(True)))
    ).all()

    if barcodes:
        brand_rows = (
            await session.execute(
                select(ProductBrand.product_barcode, ProductBrand.brand_id).where(
                    ProductBrand.product_barcode.in_(barcodes)
                )
            )
        ).all()
        category_rows = (
            await session.execute(
                select(ProductCategory.product_barcode, ProductCategory.category_id).where(
                    ProductCategory.product_barcode.in_(barcodes)
                )
            )
        ).all()
        for barcode, brand_id in brand_rows:
            context.product_brands.setdefault(barcode, set()).add(brand_id)
        for barcode, category_id in category_rows:
            context.product_categories.setdefault(barcode, set()).add(category_id)

    brand_ids = {rule.brand_id for rule in context.rules if rule.brand_id is not None}
    if brand_ids:
        context.brand_names = dict(
            (
                await session.execute(select(Brand.id, Brand.name).where(Brand.id.in_(brand_ids)))
            ).all()
        )

    from larder.models import FoodCategory

    category_ids = {rule.category_id for rule in context.rules if rule.category_id is not None}
    if category_ids:
        context.category_names = dict(
            (
                await session.execute(
                    select(FoodCategory.id, FoodCategory.display_name).where(
                        FoodCategory.id.in_(category_ids)
                    )
                )
            ).all()
        )
    return context


def _matched_rule(barcode: str, context: RoutingContext) -> StoreRoutingRule | None:
    brand_ids = context.product_brands.get(barcode, set())
    category_ids = context.product_categories.get(barcode, set())
    matches: list[tuple[int, int, str, StoreRoutingRule]] = []
    for rule in context.rules:
        specificity = 0
        if rule.match_type == "PRODUCT" and rule.product_barcode == barcode:
            specificity = 3
        elif rule.match_type == "BRAND" and rule.brand_id in brand_ids:
            specificity = 2
        elif rule.match_type == "CATEGORY" and rule.category_id in category_ids:
            specificity = 1
        if specificity:
            matches.append((specificity, rule.priority, str(rule.id), rule))
    if not matches:
        return None
    return max(matches, key=lambda item: (item[0], item[1], item[2]))[3]


def resolve_rule_assignment(
    *,
    barcode: str,
    stores: dict[UUID, Store],
    context: RoutingContext,
) -> StoreAssignment:
    rule = _matched_rule(barcode, context)
    if rule is None:
        return StoreAssignment(store_id=None, source="none")
    store = stores.get(rule.store_id)
    label = barcode
    if rule.match_type == "BRAND":
        label = context.brand_names.get(rule.brand_id, "Brand")
    elif rule.match_type == "CATEGORY":
        label = context.category_names.get(rule.category_id, "Category")
    inactive = store is None or not store.active
    return StoreAssignment(
        store_id=rule.store_id,
        source=f"{rule.match_type.lower()}_rule",
        reason=f"{rule.match_type.title()} rule: {label}",
        behavior=rule.behavior,
        rule_id=rule.id,
        warning="Required store is inactive"
        if inactive and rule.behavior == "REQUIRE"
        else "Preferred store is inactive"
        if inactive
        else None,
    )


async def rule_assignment_for_product(session: AsyncSession, barcode: str) -> StoreAssignment:
    stores = {store.id: store for store in (await session.scalars(select(Store))).all()}
    context = await load_routing_context(session, [barcode])
    assignment = resolve_rule_assignment(barcode=barcode, stores=stores, context=context)
    store = stores.get(assignment.store_id) if assignment.store_id else None
    if store is None or not store.active:
        return StoreAssignment(store_id=None, source="none")
    return assignment


async def matching_barcodes_for_rule(session: AsyncSession, rule: StoreRoutingRule) -> list[str]:
    if rule.match_type == "PRODUCT":
        return [rule.product_barcode] if rule.product_barcode else []
    if rule.match_type == "BRAND":
        return list(
            await session.scalars(
                select(ProductBrand.product_barcode).where(ProductBrand.brand_id == rule.brand_id)
            )
        )
    return list(
        await session.scalars(
            select(ProductCategory.product_barcode).where(
                ProductCategory.category_id == rule.category_id
            )
        )
    )


async def apply_default_store_to_unassigned_items(
    session: AsyncSession, barcodes: list[str] | set[str] | tuple[str, ...]
) -> int:
    unique_barcodes = list(dict.fromkeys(barcodes))
    if not unique_barcodes:
        return 0
    stores = {store.id: store for store in (await session.scalars(select(Store))).all()}
    context = await load_routing_context(session, unique_barcodes)
    items = (
        await session.scalars(
            select(InventoryItem)
            .where(
                InventoryItem.product_barcode.in_(unique_barcodes),
                InventoryItem.status == InventoryStatus.IN_STOCK,
                InventoryItem.store_id.is_(None),
            )
            .with_for_update()
        )
    ).all()
    now = datetime.now(UTC)
    updated = 0
    for item in items:
        assignment = resolve_rule_assignment(
            barcode=item.product_barcode,
            stores=stores,
            context=context,
        )
        store = stores.get(assignment.store_id) if assignment.store_id else None
        if store is None or not store.active or assignment.rule_id is None:
            continue
        item.store_id = store.id
        session.add(
            InventoryEvent(
                inventory_item_id=item.id,
                event_type=InventoryEventType.STORE_CHANGED,
                occurred_at=now,
                scan_event_id=None,
                actor_type="SYSTEM",
                actor_id="store-routing",
                before_state={"store_id": None},
                after_state={
                    "store_id": str(store.id),
                    "rule_id": str(assignment.rule_id),
                    "source": assignment.source,
                },
                reason=f"Defaulted from {assignment.reason}",
            )
        )
        updated += 1
    if updated:
        await session.flush()
    return updated


async def apply_default_stores_for_rule(session: AsyncSession, rule: StoreRoutingRule) -> int:
    return await apply_default_store_to_unassigned_items(
        session, await matching_barcodes_for_rule(session, rule)
    )


def resolve_store_assignment(
    *,
    barcode: str,
    preferred_store_id: UUID | None,
    store_prices: dict[UUID, float],
    stores: dict[UUID, Store],
    context: RoutingContext,
) -> StoreAssignment:
    if preferred_store_id is not None:
        store = stores.get(preferred_store_id)
        warning = "Selected store is inactive" if store is not None and not store.active else None
        return StoreAssignment(
            store_id=preferred_store_id,
            source="manual",
            reason="Manually selected store",
            warning=warning,
        )

    rule_assignment = resolve_rule_assignment(
        barcode=barcode,
        stores=stores,
        context=context,
    )
    rule_store = stores.get(rule_assignment.store_id) if rule_assignment.store_id else None
    if rule_store is not None and rule_store.active:
        return rule_assignment
    if rule_assignment.behavior == "REQUIRE" and rule_assignment.store_id is not None:
        return rule_assignment

    active_prices = {
        store_id: price
        for store_id, price in store_prices.items()
        if store_id in stores and stores[store_id].active
    }
    if active_prices:
        store_id = min(active_prices, key=active_prices.get)
        return StoreAssignment(
            store_id=store_id,
            source="price_history",
            reason="Lowest observed price",
        )
    return StoreAssignment(store_id=None, source="none")
