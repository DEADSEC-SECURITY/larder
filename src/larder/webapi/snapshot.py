from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from larder.config import Settings
from larder.models import (
    Brand,
    FoodCategory,
    InventoryItem,
    Product,
    ProductBrand,
    ProductCategory,
    ProductStockPolicy,
    ProductStoreObservation,
    StockGroup,
    StockGroupPolicy,
    StockGroupProduct,
    StorageLocation,
    Store,
)
from larder.models.enums import InventoryStatus
from larder.webapi.common import id_status, local_today


@dataclass
class ProductSummary:
    product: Product
    brands: list[Brand] = field(default_factory=list)
    items: list[InventoryItem] = field(default_factory=list)
    category: tuple[str, str, str | None] | None = None  # (id, name, parent_name)
    rule: tuple[str, str, int] | None = None  # (category_id, name, days)
    policy: ProductStockPolicy | None = None
    stock_group: StockGroup | None = None
    stock_group_membership: StockGroupProduct | None = None
    stock_group_policy: StockGroupPolicy | None = None
    stock_group_in_stock: int = 0
    stock_group_member_count: int = 0
    store_prices: dict[UUID, float] = field(default_factory=dict)

    @property
    def in_stock(self) -> int:
        return len(self.items)

    @property
    def visible(self) -> bool:
        """Archived products stay hidden unless stock reappears (rescans)."""
        return self.product.archived_at is None or self.in_stock > 0

    @property
    def lowest_price(self) -> float | None:
        return min(self.store_prices.values()) if self.store_prices else None

    def soonest(self) -> InventoryItem | None:
        dated = [i for i in self.items if i.expiration_date is not None]
        return min(dated, key=lambda i: i.expiration_date) if dated else None

    def days_left(self, today: date) -> int | None:
        soonest = self.soonest()
        if soonest is None:
            return None
        return (soonest.expiration_date - today).days

    def low_stock(self) -> bool:
        if (
            self.stock_group is not None
            and self.stock_group.active
            and self.stock_group_policy is not None
            and self.stock_group_policy.enabled
        ):
            return (
                self.stock_group_policy.reorder_point > 0
                and self.stock_group_in_stock < self.stock_group_policy.reorder_point
            )
        return (
            self.policy is not None
            and self.policy.enabled
            and self.policy.reorder_point > 0
            and self.in_stock < self.policy.reorder_point
        )

    def has_unknown_date(self) -> bool:
        return any(i.expiration_date is None for i in self.items)

    def needs_info(self) -> bool:
        return (
            id_status(self.product) != "ok" or self.lowest_price is None or self.has_unknown_date()
        )


@dataclass
class CatalogSnapshot:
    summaries: dict[str, ProductSummary]
    stores: dict[UUID, Store]
    storage_locations: dict[UUID, StorageLocation]
    today: date
    window_days: int

    def stocked(self) -> list[ProductSummary]:
        return [s for s in self.summaries.values() if s.in_stock > 0]

    def expiring(self) -> list[ProductSummary]:
        out = []
        for summary in self.stocked():
            days = summary.days_left(self.today)
            if days is not None and days <= self.window_days:
                out.append(summary)
        out.sort(key=lambda s: s.soonest().expiration_date)
        return out


def _pick_category(rows: list, *, with_days_only: bool) -> tuple | None:
    candidates = [r for r in rows if not with_days_only or r.days is not None]
    if not candidates:
        return None
    candidates.sort(
        key=lambda r: (
            not r.is_primary,
            -(r.source_rank if r.source_rank is not None else -(10**9)),
            r.category_id,
        )
    )
    if with_days_only:
        # Mirror services.expiration ordering: source_rank desc nulls last, id asc.
        candidates.sort(
            key=lambda r: (
                -(r.source_rank if r.source_rank is not None else -(10**9)),
                r.category_id,
            )
        )
    return candidates[0]


async def load_snapshot(
    session: AsyncSession, settings: Settings, window_days: int
) -> CatalogSnapshot:
    products = (await session.scalars(select(Product))).all()
    summaries = {p.barcode: ProductSummary(product=p) for p in products}

    items = (
        await session.scalars(
            select(InventoryItem)
            .where(InventoryItem.status == InventoryStatus.IN_STOCK)
            .order_by(
                InventoryItem.expiration_date.asc().nulls_last(),
                InventoryItem.date_added.asc(),
            )
        )
    ).all()
    for item in items:
        summary = summaries.get(item.product_barcode)
        if summary is not None:
            summary.items.append(item)

    for policy in (await session.scalars(select(ProductStockPolicy))).all():
        summary = summaries.get(policy.product_barcode)
        if summary is not None:
            summary.policy = policy

    stock_group_rows = (
        await session.execute(
            select(StockGroupProduct, StockGroup, StockGroupPolicy)
            .join(StockGroup, StockGroup.id == StockGroupProduct.stock_group_id)
            .outerjoin(
                StockGroupPolicy,
                StockGroupPolicy.stock_group_id == StockGroupProduct.stock_group_id,
            )
        )
    ).all()
    grouped_summaries: dict[UUID, list[ProductSummary]] = {}
    for membership, group, policy in stock_group_rows:
        summary = summaries.get(membership.product_barcode)
        if summary is None:
            continue
        summary.stock_group = group
        summary.stock_group_membership = membership
        summary.stock_group_policy = policy
        grouped_summaries.setdefault(group.id, []).append(summary)
    for member_summaries in grouped_summaries.values():
        on_hand = sum(summary.in_stock for summary in member_summaries)
        member_count = len(member_summaries)
        for summary in member_summaries:
            summary.stock_group_in_stock = on_hand
            summary.stock_group_member_count = member_count

    brand_rows = (
        await session.execute(
            select(ProductBrand, Brand)
            .join(Brand, Brand.id == ProductBrand.brand_id)
            .where(Brand.active.is_(True))
            .order_by(
                ProductBrand.product_barcode,
                ProductBrand.is_primary.desc(),
                ProductBrand.source_rank.asc().nulls_last(),
                Brand.name,
            )
        )
    ).all()
    for association, brand in brand_rows:
        summary = summaries.get(association.product_barcode)
        if summary is not None:
            summary.brands.append(brand)

    parent = FoodCategory.__table__.alias("parent")
    category_rows = (
        await session.execute(
            select(
                ProductCategory.product_barcode,
                ProductCategory.category_id,
                ProductCategory.is_primary,
                ProductCategory.source_rank,
                FoodCategory.display_name,
                FoodCategory.default_expiration_days.label("days"),
                parent.c.display_name.label("parent_name"),
            )
            .join(FoodCategory, FoodCategory.id == ProductCategory.category_id)
            .outerjoin(parent, parent.c.id == FoodCategory.parent_id)
            .where(FoodCategory.active.is_(True))
        )
    ).all()
    fallback_rule = (
        await session.execute(
            select(
                FoodCategory.id, FoodCategory.display_name, FoodCategory.default_expiration_days
            ).where(
                FoodCategory.id == settings.fallback_category_id,
                FoodCategory.active.is_(True),
                FoodCategory.default_expiration_days.is_not(None),
            )
        )
    ).one_or_none()
    by_product: dict[str, list] = {}
    for row in category_rows:
        by_product.setdefault(row.product_barcode, []).append(row)
    for barcode, rows in by_product.items():
        summary = summaries.get(barcode)
        if summary is None:
            continue
        display = _pick_category(rows, with_days_only=False)
        if display is not None:
            summary.category = (display.category_id, display.display_name, display.parent_name)
        ruled = _pick_category(rows, with_days_only=True)
        if ruled is not None:
            summary.rule = (ruled.category_id, ruled.display_name, ruled.days)
    if fallback_rule is not None:
        for summary in summaries.values():
            if summary.rule is None:
                summary.rule = (
                    fallback_rule.id,
                    fallback_rule.display_name,
                    fallback_rule.default_expiration_days,
                )

    price_rows = (
        await session.execute(
            select(
                ProductStoreObservation.product_barcode,
                ProductStoreObservation.store_id,
                ProductStoreObservation.unit_price,
            )
        )
    ).all()
    for row in price_rows:
        summary = summaries.get(row.product_barcode)
        if summary is None:
            continue
        price = float(row.unit_price)
        existing = summary.store_prices.get(row.store_id)
        if existing is None or price < existing:
            summary.store_prices[row.store_id] = price

    stores = {s.id: s for s in (await session.scalars(select(Store))).all()}
    storage_locations = {
        location.id: location
        for location in (await session.scalars(select(StorageLocation))).all()
    }
    return CatalogSnapshot(
        summaries=summaries,
        stores=stores,
        storage_locations=storage_locations,
        today=local_today(settings),
        window_days=window_days,
    )
