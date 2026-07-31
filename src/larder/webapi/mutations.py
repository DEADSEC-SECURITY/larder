from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from larder.config import Settings
from larder.domain.names import standardize_name
from larder.models import (
    EnrichmentJob,
    FoodCategory,
    GroceryListItem,
    InventoryItem,
    Product,
    ProductCategory,
    ProductStockPolicy,
    ProductStoreObservation,
    StockGroupPolicy,
    StockGroupProduct,
    StorageLocation,
    Store,
)
from larder.models.enums import (
    EnrichmentStatus,
    InventoryEventType,
    InventoryStatus,
)
from larder.services.brands import sync_product_brands
from larder.services.expiration import resolve_expiration
from larder.services.stock_matching import match_product_to_stock_groups
from larder.services.stock_policy import (
    recalculate_stock_group_policy,
    recalculate_stock_policy,
)
from larder.services.storage import default_storage_location_for_product
from larder.services.store_routing import rule_assignment_for_product
from larder.services.tax import (
    PriceBreakdown,
    apply_price,
    item_price_payload,
    price_breakdown,
)
from larder.webapi.common import (  # noqa: I001
    MANUAL_BARCODE_FORMAT,
    add_inventory_event,
    create_web_scan_event,
    expiration_kind,
    get_app_settings,
    get_session,
    household_settings,
    in_stock_count,
    new_manual_barcode,
    require_item,
    require_product,
    utcnow,
    web_api_router,
)
from larder.webapi.serializers import unit_row
from larder.webapi.stores import get_or_create_store

router = web_api_router(tags=["mutations"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class QuickAddRequest(CamelModel):
    barcode: str | None = None
    name: str | None = Field(default=None, max_length=200)
    brand: str | None = Field(default=None, max_length=200)
    product_kind: Literal[
        "FOOD", "HOUSEHOLD", "PERSONAL_CARE", "MEDICAL", "PET", "OFFICE", "OTHER"
    ] = "FOOD"
    description: str | None = Field(default=None, max_length=2000)
    manufacturer: str | None = Field(default=None, max_length=200)
    package_size: str | None = Field(default=None, max_length=100)
    material: str | None = Field(default=None, max_length=200)
    variant: str | None = Field(default=None, max_length=200)
    quantity: int = Field(default=1, ge=1, le=99)
    store_id: UUID | None = None
    new_store_name: str | None = Field(default=None, max_length=100)
    price_total: float | None = Field(default=None, ge=0)
    # True = priceTotal already includes tax; subtotals are backed out per unit.
    price_includes_tax: bool = False
    tax_rate_id: UUID | None = None
    added_on: date | None = None
    expiration_date: date | None = None
    storage_location_id: UUID | None = None

    @model_validator(mode="after")
    def product_reference_required(self) -> QuickAddRequest:
        if not self.barcode and not (self.name and self.name.strip()):
            raise ValueError("either barcode or name is required")
        if "tax_rate_id" in self.model_fields_set and self.price_total is None:
            raise ValueError("priceTotal is required when taxRateId is provided")
        return self


class UnitUpdateRequest(CamelModel):
    store_id: UUID | None = None
    storage_location_id: UUID | None = None
    expiration_date: date | None = None
    # Unit price paid; explicit null clears it back to "no price".
    price: float | None = Field(default=None, ge=0, le=100_000)
    # True = price is the final total; the subtotal is backed out of it.
    price_includes_tax: bool = False
    tax_rate_id: UUID | None = None

    @model_validator(mode="after")
    def price_required_for_tax(self) -> UnitUpdateRequest:
        if "tax_rate_id" in self.model_fields_set and "price" not in self.model_fields_set:
            raise ValueError("price is required when taxRateId is provided")
        return self


class BulkUnitsRequest(CamelModel):
    item_ids: list[UUID] | None = Field(default=None, max_length=500)
    barcodes: list[str] | None = Field(default=None, max_length=100)
    action: Literal["update", "remove", "waste"]
    set_store: bool = False
    store_id: UUID | None = None
    set_storage_location: bool = False
    storage_location_id: UUID | None = None
    expiration: Literal["date", "estimate"] | None = None
    expiration_date: date | None = None
    set_price: bool = False
    price: float | None = Field(default=None, ge=0, le=100_000)
    # True = price is the final total; the subtotal is backed out of it.
    price_includes_tax: bool = False
    tax_rate_id: UUID | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> BulkUnitsRequest:
        if not self.item_ids and not self.barcodes:
            raise ValueError("itemIds or barcodes is required")
        if self.action == "update":
            if (
                not self.set_store
                and not self.set_storage_location
                and self.expiration is None
                and not self.set_price
            ):
                raise ValueError(
                    "an update needs setStore, setStorageLocation, expiration, and/or setPrice"
                )
            if self.expiration == "date" and self.expiration_date is None:
                raise ValueError("expirationDate is required when expiration is 'date'")
            if "tax_rate_id" in self.model_fields_set and not self.set_price:
                raise ValueError("setPrice must be true when taxRateId is provided")
        return self


class StockPolicyRequest(CamelModel):
    reorder_point: int = Field(ge=0, le=999)
    target_stock: int = Field(ge=0, le=999)

    @model_validator(mode="after")
    def target_covers_reorder(self) -> StockPolicyRequest:
        if self.target_stock < self.reorder_point:
            raise ValueError("targetStock must be at least reorderPoint")
        return self


class ProductUpdateRequest(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    brand: str | None = Field(default=None, max_length=200)
    product_kind: (
        Literal["FOOD", "HOUSEHOLD", "PERSONAL_CARE", "MEDICAL", "PET", "OFFICE", "OTHER"] | None
    ) = None
    description: str | None = Field(default=None, max_length=2000)
    manufacturer: str | None = Field(default=None, max_length=200)
    package_size: str | None = Field(default=None, max_length=100)
    material: str | None = Field(default=None, max_length=200)
    variant: str | None = Field(default=None, max_length=200)
    category_id: str | None = None
    default_storage_location_id: UUID | None = None


def _clean_optional(value: str | None, *, standardize: bool = False) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split()).strip()
    if not value:
        return None
    return standardize_name(value) if standardize else value


@router.patch("/products/{barcode}")
async def update_product(barcode: str, payload: ProductUpdateRequest, session: SessionDep) -> dict:
    product = await require_product(session, barcode)
    fields = payload.model_fields_set
    field_sources = dict(product.field_sources or {})
    text_fields = {
        "name": True,
        "brand": True,
        "description": False,
        "manufacturer": True,
        "package_size": False,
        "material": True,
        "variant": True,
    }
    for field_name, standardize in text_fields.items():
        if field_name in fields:
            setattr(
                product,
                field_name,
                _clean_optional(getattr(payload, field_name), standardize=standardize),
            )
            field_sources[field_name] = "USER"
    if payload.product_kind is not None:
        product.product_kind = payload.product_kind
        field_sources["product_kind"] = "USER"
    if "default_storage_location_id" in fields:
        await _resolve_storage_location(session, payload.default_storage_location_id)
        product.default_storage_location_id = payload.default_storage_location_id
        field_sources["default_storage_location_id"] = "USER"
    if "category_id" in fields:
        category_id = payload.category_id or "local:uncategorized"
        category = await session.get(FoodCategory, category_id)
        if category is None or not category.active:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "category not found")
        await session.execute(
            update(ProductCategory)
            .where(ProductCategory.product_barcode == barcode)
            .values(is_primary=False)
        )
        association = await session.get(ProductCategory, (barcode, category_id))
        if association is None:
            association = ProductCategory(
                product_barcode=barcode,
                category_id=category_id,
                source_rank=999,
                is_primary=True,
            )
            session.add(association)
        else:
            association.is_primary = True
            association.source_rank = 999
    product.field_sources = field_sources
    if "brand" in fields:
        await sync_product_brands(session, product, product.brand, source="MANUAL")
    await session.flush()
    match = await match_product_to_stock_groups(session, product)
    return {
        "barcode": product.barcode,
        "name": product.name,
        "brand": product.brand,
        "productKind": product.product_kind.lower(),
        "description": product.description,
        "manufacturer": product.manufacturer,
        "packageSize": product.package_size,
        "material": product.material,
        "variant": product.variant,
        "defaultStorageLocationId": str(product.default_storage_location_id)
        if product.default_storage_location_id
        else None,
        "fieldSources": product.field_sources,
        "stockGroupMatchStatus": match.status.lower(),
        "stockGroupId": str(match.stock_group_id) if match.stock_group_id else None,
    }


def _local_noon(settings: Settings, day: date) -> datetime:
    return datetime.combine(day, time(12, 0), tzinfo=ZoneInfo(settings.household_timezone))


@router.post("/quick-add", status_code=status.HTTP_201_CREATED)
async def quick_add(payload: QuickAddRequest, session: SessionDep, settings: SettingsDep) -> dict:
    prefs = await household_settings(session)

    store: Store | None = None
    if payload.store_id is not None:
        store = await session.get(Store, payload.store_id)
        if store is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "store not found")
    elif payload.new_store_name and payload.new_store_name.strip():
        store = await get_or_create_store(session, payload.new_store_name.strip())
    if payload.barcode:
        product = await require_product(session, payload.barcode)
        # Adding stock to an archived product revives it.
        product.archived_at = None
    else:
        product = Product(
            barcode=new_manual_barcode(),
            barcode_format=MANUAL_BARCODE_FORMAT,
            name=standardize_name(payload.name),
            brand=standardize_name(payload.brand) if payload.brand else None,
            product_kind=payload.product_kind,
            description=payload.description.strip() if payload.description else None,
            manufacturer=standardize_name(payload.manufacturer) if payload.manufacturer else None,
            package_size=payload.package_size.strip() if payload.package_size else None,
            material=standardize_name(payload.material) if payload.material else None,
            variant=standardize_name(payload.variant) if payload.variant else None,
            identification_source="MANUAL",
            field_sources={
                key: "USER"
                for key, value in {
                    "name": payload.name,
                    "brand": payload.brand,
                    "product_kind": payload.product_kind,
                    "description": payload.description,
                    "manufacturer": payload.manufacturer,
                    "package_size": payload.package_size,
                    "material": payload.material,
                    "variant": payload.variant,
                }.items()
                if value is not None
            },
            enrichment_status=EnrichmentStatus.SUCCEEDED,
            enriched_at=utcnow(),
        )
        session.add(product)
        await session.flush()
        await sync_product_brands(session, product, payload.brand, source="MANUAL")

    storage_location: StorageLocation | None = None
    if payload.storage_location_id is not None:
        storage_location = await session.get(StorageLocation, payload.storage_location_id)
        if storage_location is None or not storage_location.active:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "storage location not found")
    else:
        default_location_id = await default_storage_location_for_product(session, product.barcode)
        if default_location_id is not None:
            storage_location = await session.get(StorageLocation, default_location_id)

    if store is None:
        assignment = await rule_assignment_for_product(session, product.barcode)
        if assignment.store_id is not None:
            store = await session.get(Store, assignment.store_id)
    if store is None and prefs.require_store_on_manual_add:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "a store is required")

    added_at = _local_noon(settings, payload.added_on) if payload.added_on else utcnow()
    snapshot = None
    if payload.expiration_date is None:
        snapshot = await resolve_expiration(
            session,
            product.barcode,
            added_at,
            settings.household_timezone,
            settings.fallback_category_id,
        )

    unit_breakdown: PriceBreakdown | None = None
    if payload.price_total is not None:
        unit_subtotal = Decimal(str(payload.price_total)) / payload.quantity
        selected_rate_id = (
            payload.tax_rate_id
            if "tax_rate_id" in payload.model_fields_set
            else prefs.default_tax_rate_id
        )
        unit_breakdown = await price_breakdown(
            session, unit_subtotal, selected_rate_id, tax_included=payload.price_includes_tax
        )

    created_ids: list[str] = []
    for _ in range(payload.quantity):
        event = await create_web_scan_event(session, barcode=product.barcode)
        item = InventoryItem(
            product_barcode=product.barcode,
            status=InventoryStatus.IN_STOCK,
            date_added=added_at,
            expiration_date=payload.expiration_date
            or (snapshot.expiration_date if snapshot else None),
            expiration_is_estimated=payload.expiration_date is None and snapshot is not None,
            expiration_category_id=snapshot.category_id if snapshot else None,
            expiration_offset_days=snapshot.offset_days if snapshot else None,
            store_id=store.id if store else None,
            storage_location_id=storage_location.id if storage_location else None,
            add_scan_event_id=event.id,
        )
        apply_price(item, unit_breakdown, settings.currency_code)
        session.add(item)
        await session.flush()
        add_inventory_event(
            session,
            item_id=item.id,
            event_type=InventoryEventType.ADDED,
            scan_event_id=event.id,
            after_state={
                "status": InventoryStatus.IN_STOCK.value,
                "product_barcode": product.barcode,
                "expiration_date": item.expiration_date.isoformat()
                if item.expiration_date
                else None,
                "store_id": str(store.id) if store else None,
                "storage_location_id": str(storage_location.id) if storage_location else None,
            },
            occurred_at=added_at,
        )
        created_ids.append(str(item.id))

    if unit_breakdown is not None and store is not None:
        session.add(
            ProductStoreObservation(
                product_barcode=product.barcode,
                store_id=store.id,
                observed_at=added_at,
                unit_price=unit_breakdown.total,
                subtotal_price=unit_breakdown.subtotal,
                tax_rate=unit_breakdown.tax_rate,
                tax_amount=unit_breakdown.tax_amount,
                tax_rate_id=unit_breakdown.tax_rate_id,
                tax_location_id=unit_breakdown.tax_location_id,
                tax_class=unit_breakdown.tax_class,
                tax_source=unit_breakdown.tax_source,
                currency_code=settings.currency_code,
                source="WEB",
            )
        )
    await session.flush()
    await match_product_to_stock_groups(session, product)
    await recalculate_stock_policy(session, product.barcode)
    return {"barcode": product.barcode, "created": len(created_ids), "itemIds": created_ids}


def _change_store(session: AsyncSession, item: InventoryItem, new_store: Store | None) -> bool:
    new_id = new_store.id if new_store else None
    if new_id == item.store_id:
        return False
    before = {"store_id": str(item.store_id) if item.store_id else None}
    item.store_id = new_id
    add_inventory_event(
        session,
        item_id=item.id,
        event_type=InventoryEventType.STORE_CHANGED,
        scan_event_id=None,
        before_state=before,
        after_state={"store_id": str(item.store_id) if item.store_id else None},
    )
    return True


def _change_storage_location(
    session: AsyncSession,
    item: InventoryItem,
    new_location: StorageLocation | None,
) -> bool:
    new_id = new_location.id if new_location else None
    if new_id == item.storage_location_id:
        return False
    before = {
        "storage_location_id": str(item.storage_location_id) if item.storage_location_id else None
    }
    item.storage_location_id = new_id
    add_inventory_event(
        session,
        item_id=item.id,
        event_type=InventoryEventType.STORAGE_CHANGED,
        scan_event_id=None,
        before_state=before,
        after_state={
            "storage_location_id": str(item.storage_location_id)
            if item.storage_location_id
            else None
        },
    )
    return True


async def _change_expiration(
    session: AsyncSession,
    item: InventoryItem,
    settings: Settings,
    confirmed_date: date | None,
) -> None:
    """Set a confirmed date, or re-estimate from the category rule when None."""
    before = {
        "expiration_date": item.expiration_date.isoformat() if item.expiration_date else None,
        "kind": expiration_kind(item),
    }
    if confirmed_date is not None:
        item.expiration_date = confirmed_date
        item.expiration_is_estimated = False
        item.expiration_category_id = None
        item.expiration_offset_days = None
        reason = "confirmed date set"
    else:
        snapshot = await resolve_expiration(
            session,
            item.product_barcode,
            item.date_added,
            settings.household_timezone,
            settings.fallback_category_id,
        )
        item.expiration_date = snapshot.expiration_date if snapshot else None
        item.expiration_is_estimated = snapshot is not None
        item.expiration_category_id = snapshot.category_id if snapshot else None
        item.expiration_offset_days = snapshot.offset_days if snapshot else None
        reason = "reverted to category estimate"
    add_inventory_event(
        session,
        item_id=item.id,
        event_type=InventoryEventType.EXPIRATION_CHANGED,
        scan_event_id=None,
        before_state=before,
        after_state={
            "expiration_date": item.expiration_date.isoformat() if item.expiration_date else None,
            "kind": expiration_kind(item),
        },
        reason=reason,
    )


async def _change_price(
    session: AsyncSession,
    item: InventoryItem,
    price: float | None,
    tax_rate_id: UUID | None,
    settings: Settings,
    tax_included: bool = False,
) -> tuple[bool, PriceBreakdown | None]:
    breakdown = (
        await price_breakdown(session, price, tax_rate_id, tax_included=tax_included)
        if price is not None
        else None
    )
    before = item_price_payload(item)
    if breakdown is None and item.price_paid is None:
        return False, None
    if breakdown is not None and (
        item.price_subtotal == breakdown.subtotal
        and item.tax_rate == breakdown.tax_rate
        and item.tax_amount == breakdown.tax_amount
        and item.price_paid == breakdown.total
        and item.tax_rate_id == breakdown.tax_rate_id
    ):
        return False, breakdown
    apply_price(item, breakdown, settings.currency_code)
    add_inventory_event(
        session,
        item_id=item.id,
        event_type=InventoryEventType.PRICE_CHANGED,
        scan_event_id=None,
        before_state=before,
        after_state=item_price_payload(item),
        reason="price set" if breakdown is not None else "price cleared",
    )
    return True, breakdown


def _record_price_observation(
    session: AsyncSession,
    *,
    barcode: str,
    store_id: UUID,
    breakdown: PriceBreakdown,
    settings: Settings,
    item_id: UUID | None = None,
) -> None:
    session.add(
        ProductStoreObservation(
            product_barcode=barcode,
            store_id=store_id,
            observed_at=utcnow(),
            unit_price=breakdown.total,
            subtotal_price=breakdown.subtotal,
            tax_rate=breakdown.tax_rate,
            tax_amount=breakdown.tax_amount,
            tax_rate_id=breakdown.tax_rate_id,
            tax_location_id=breakdown.tax_location_id,
            tax_class=breakdown.tax_class,
            tax_source=breakdown.tax_source,
            currency_code=settings.currency_code,
            inventory_item_id=item_id,
            source="WEB",
        )
    )


async def _resolve_store(session: AsyncSession, store_id: UUID | None) -> Store | None:
    if store_id is None:
        return None
    store = await session.get(Store, store_id)
    if store is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "store not found")
    return store


async def _resolve_storage_location(
    session: AsyncSession, location_id: UUID | None
) -> StorageLocation | None:
    if location_id is None:
        return None
    location = await session.get(StorageLocation, location_id)
    if location is None or not location.active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "storage location not found")
    return location


@router.patch("/units/{item_id}")
async def update_unit(
    item_id: UUID,
    payload: UnitUpdateRequest,
    session: SessionDep,
    settings: SettingsDep,
) -> dict:
    item = await require_item(session, item_id)
    prefs = await household_settings(session)
    fields = payload.model_fields_set

    if "store_id" in fields:
        _change_store(session, item, await _resolve_store(session, payload.store_id))
    if "storage_location_id" in fields:
        _change_storage_location(
            session,
            item,
            await _resolve_storage_location(session, payload.storage_location_id),
        )
    if "expiration_date" in fields:
        await _change_expiration(session, item, settings, payload.expiration_date)
    if "price" in fields:
        selected_rate_id = (
            payload.tax_rate_id if "tax_rate_id" in fields else prefs.default_tax_rate_id
        )
        changed, breakdown = await _change_price(
            session,
            item,
            payload.price,
            selected_rate_id,
            settings,
            tax_included=payload.price_includes_tax,
        )
        if changed and breakdown is not None and item.store_id is not None:
            _record_price_observation(
                session,
                barcode=item.product_barcode,
                store_id=item.store_id,
                breakdown=breakdown,
                settings=settings,
                item_id=item.id,
            )

    await session.flush()
    stores = {s.id: s for s in (await session.scalars(select(Store))).all()}
    locations = {
        location.id: location for location in (await session.scalars(select(StorageLocation))).all()
    }
    return unit_row(item, stores, locations)


@router.post("/units/bulk")
async def bulk_units(payload: BulkUnitsRequest, session: SessionDep, settings: SettingsDep) -> dict:
    prefs = await household_settings(session)
    items: dict[UUID, InventoryItem] = {}
    if payload.item_ids:
        rows = (
            await session.scalars(
                select(InventoryItem)
                .where(InventoryItem.id.in_(payload.item_ids))
                .with_for_update()
            )
        ).all()
        items.update({row.id: row for row in rows})
    if payload.barcodes:
        rows = (
            await session.scalars(
                select(InventoryItem)
                .where(
                    InventoryItem.product_barcode.in_(payload.barcodes),
                    InventoryItem.status == InventoryStatus.IN_STOCK,
                )
                .with_for_update()
            )
        ).all()
        items.update({row.id: row for row in rows})

    in_stock = [item for item in items.values() if item.status == InventoryStatus.IN_STOCK]
    skipped = len(items) - len(in_stock)

    if payload.action == "update":
        store = await _resolve_store(session, payload.store_id) if payload.set_store else None
        storage_location = (
            await _resolve_storage_location(session, payload.storage_location_id)
            if payload.set_storage_location
            else None
        )
        observed: set[tuple[str, UUID]] = set()
        breakdowns: dict[tuple[str, UUID], PriceBreakdown] = {}
        selected_rate_id = (
            payload.tax_rate_id
            if "tax_rate_id" in payload.model_fields_set
            else prefs.default_tax_rate_id
        )
        for item in in_stock:
            if payload.set_store:
                _change_store(session, item, store)
            if payload.set_storage_location:
                _change_storage_location(session, item, storage_location)
            if payload.expiration == "date":
                await _change_expiration(session, item, settings, payload.expiration_date)
            elif payload.expiration == "estimate":
                await _change_expiration(session, item, settings, None)
            if payload.set_price:
                changed, breakdown = await _change_price(
                    session,
                    item,
                    payload.price,
                    selected_rate_id,
                    settings,
                    tax_included=payload.price_includes_tax,
                )
                if changed and breakdown is not None and item.store_id is not None:
                    # One history observation per product/store, not per unit.
                    observed.add((item.product_barcode, item.store_id))
                    breakdowns[(item.product_barcode, item.store_id)] = breakdown
        for barcode, store_id in observed:
            _record_price_observation(
                session,
                barcode=barcode,
                store_id=store_id,
                breakdown=breakdowns[(barcode, store_id)],
                settings=settings,
            )
        await session.flush()
        return {"matched": len(items), "updated": len(in_stock), "skipped": skipped}

    new_status = InventoryStatus.REMOVED if payload.action == "remove" else InventoryStatus.TRASHED
    now = utcnow()
    touched_barcodes: set[str] = set()
    for item in in_stock:
        event = await create_web_scan_event(session, barcode=item.product_barcode)
        item.status = new_status
        item.removed_at = now
        item.terminal_scan_event_id = event.id
        add_inventory_event(
            session,
            item_id=item.id,
            event_type=InventoryEventType.REMOVED
            if new_status == InventoryStatus.REMOVED
            else InventoryEventType.TRASHED,
            scan_event_id=event.id,
            before_state={"status": InventoryStatus.IN_STOCK.value},
            after_state={"status": new_status.value, "removed_at": now.isoformat()},
            occurred_at=now,
        )
        touched_barcodes.add(item.product_barcode)
    await session.flush()
    for barcode in touched_barcodes:
        await recalculate_stock_policy(session, barcode)
    return {"matched": len(items), "updated": len(in_stock), "skipped": skipped}


async def _terminate_unit(
    session: AsyncSession, item_id: UUID, new_status: InventoryStatus
) -> dict:
    item = await require_item(session, item_id)
    if item.status != InventoryStatus.IN_STOCK:
        raise HTTPException(status.HTTP_409_CONFLICT, "unit is no longer in stock")
    now = utcnow()
    event = await create_web_scan_event(session, barcode=item.product_barcode)
    item.status = new_status
    item.removed_at = now
    item.terminal_scan_event_id = event.id
    add_inventory_event(
        session,
        item_id=item.id,
        event_type=InventoryEventType.REMOVED
        if new_status == InventoryStatus.REMOVED
        else InventoryEventType.TRASHED,
        scan_event_id=event.id,
        before_state={"status": InventoryStatus.IN_STOCK.value},
        after_state={"status": new_status.value, "removed_at": now.isoformat()},
        occurred_at=now,
    )
    await session.flush()
    await recalculate_stock_policy(session, item.product_barcode)
    return {"id": str(item.id), "status": new_status.value.lower()}


@router.post("/units/{item_id}/remove")
async def remove_unit(item_id: UUID, session: SessionDep) -> dict:
    return await _terminate_unit(session, item_id, InventoryStatus.REMOVED)


@router.post("/units/{item_id}/waste")
async def waste_unit(item_id: UUID, session: SessionDep) -> dict:
    return await _terminate_unit(session, item_id, InventoryStatus.TRASHED)


class UnitStatusRequest(CamelModel):
    status: Literal["in_stock", "removed", "trashed"]


@router.post("/units/{item_id}/status")
async def set_unit_status(item_id: UUID, payload: UnitStatusRequest, session: SessionDep) -> dict:
    """Correct a unit's status — undo a remove/waste or reclassify one as the other."""
    item = await require_item(session, item_id)
    target = InventoryStatus(payload.status.upper())
    if item.status == target:
        return {"id": str(item.id), "status": payload.status}
    if item.status == InventoryStatus.IN_STOCK:
        return await _terminate_unit(session, item_id, target)

    now = utcnow()
    event = await create_web_scan_event(session, barcode=item.product_barcode)
    before_state = {
        "status": item.status.value,
        "removed_at": item.removed_at.isoformat() if item.removed_at else None,
    }
    if target == InventoryStatus.IN_STOCK:
        event_type = InventoryEventType.RESTORED
        item.status = InventoryStatus.IN_STOCK
        item.removed_at = None
        item.terminal_scan_event_id = None
    else:
        # Removed ↔ trashed reclassification keeps the original removal time.
        event_type = (
            InventoryEventType.REMOVED
            if target == InventoryStatus.REMOVED
            else InventoryEventType.TRASHED
        )
        item.status = target
        item.terminal_scan_event_id = event.id
    add_inventory_event(
        session,
        item_id=item.id,
        event_type=event_type,
        scan_event_id=event.id,
        before_state=before_state,
        after_state={
            "status": target.value,
            "removed_at": item.removed_at.isoformat() if item.removed_at else None,
        },
        reason="correction",
        occurred_at=now,
    )
    await session.flush()
    await recalculate_stock_policy(session, item.product_barcode)
    return {"id": str(item.id), "status": target.value.lower()}


@router.put("/products/{barcode}/stock-policy")
async def set_stock_policy(barcode: str, payload: StockPolicyRequest, session: SessionDep) -> dict:
    await require_product(session, barcode)
    stock_group_id = await session.scalar(
        select(StockGroupProduct.stock_group_id).where(StockGroupProduct.product_barcode == barcode)
    )
    if stock_group_id is not None:
        group_policy = await session.get(StockGroupPolicy, stock_group_id, with_for_update=True)
        if payload.reorder_point == 0 and payload.target_stock == 0:
            if group_policy is not None:
                await session.delete(group_policy)
                await session.flush()
            await recalculate_stock_group_policy(session, stock_group_id)
            return {
                "barcode": barcode,
                "scope": "stock_group",
                "stockGroupId": str(stock_group_id),
                "policy": None,
            }
        if group_policy is None:
            group_policy = StockGroupPolicy(
                stock_group_id=stock_group_id,
                reorder_point=payload.reorder_point,
                target_stock=payload.target_stock,
                enabled=True,
            )
            session.add(group_policy)
        else:
            group_policy.reorder_point = payload.reorder_point
            group_policy.target_stock = payload.target_stock
            group_policy.enabled = True
        await session.flush()
        await recalculate_stock_group_policy(session, stock_group_id)
        return {
            "barcode": barcode,
            "scope": "stock_group",
            "stockGroupId": str(stock_group_id),
            "policy": {
                "reorderPoint": group_policy.reorder_point,
                "targetStock": group_policy.target_stock,
            },
        }
    policy = await session.get(ProductStockPolicy, barcode, with_for_update=True)
    if payload.reorder_point == 0 and payload.target_stock == 0:
        if policy is not None:
            await session.delete(policy)
            await session.flush()
        await recalculate_stock_policy(session, barcode)
        return {"barcode": barcode, "policy": None}
    if policy is None:
        policy = ProductStockPolicy(
            product_barcode=barcode,
            reorder_point=payload.reorder_point,
            target_stock=payload.target_stock,
            enabled=True,
        )
        session.add(policy)
    else:
        policy.reorder_point = payload.reorder_point
        policy.target_stock = payload.target_stock
        policy.enabled = True
    await session.flush()
    await recalculate_stock_policy(session, barcode)
    return {
        "barcode": barcode,
        "policy": {"reorderPoint": policy.reorder_point, "targetStock": policy.target_stock},
    }


@router.delete("/products/{barcode}")
async def delete_product(barcode: str, session: SessionDep) -> dict:
    """Hard-delete a product without inventory history; archive one that has it.

    Inventory events are append-only, so their items — and any product that owns
    them — can never be hard-deleted. Stock must be cleared first either way.
    """
    product = await require_product(session, barcode)
    if await in_stock_count(session, barcode) > 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "remove or waste the units in stock first")

    grocery = await session.get(GroceryListItem, barcode)
    if grocery is not None:
        await session.delete(grocery)
    policy = await session.get(ProductStockPolicy, barcode)
    if policy is not None:
        await session.delete(policy)
    job = await session.scalar(
        select(EnrichmentJob).where(EnrichmentJob.product_barcode == barcode)
    )
    if job is not None:
        await session.delete(job)

    has_history = (
        await session.scalar(
            select(InventoryItem.id).where(InventoryItem.product_barcode == barcode).limit(1)
        )
        is not None
    )
    if has_history:
        product.archived_at = utcnow()
        await session.flush()
        return {"barcode": barcode, "deleted": False, "archived": True}
    await session.flush()
    await session.delete(product)
    await session.flush()
    return {"barcode": barcode, "deleted": True, "archived": False}


@router.post("/products/{barcode}/restore")
async def restore_product(barcode: str, session: SessionDep) -> dict:
    product = await require_product(session, barcode)
    product.archived_at = None
    await session.flush()
    return {"barcode": barcode, "archived": False}


@router.post("/products/{barcode}/retry-enrichment")
async def retry_enrichment(barcode: str, session: SessionDep) -> dict:
    product = await require_product(session, barcode)
    if product.barcode_format == MANUAL_BARCODE_FORMAT:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "manual products are not enriched"
        )
    now = utcnow()
    job = await session.scalar(
        select(EnrichmentJob).where(EnrichmentJob.product_barcode == barcode).with_for_update()
    )
    if job is None:
        job = EnrichmentJob(
            product_barcode=barcode,
            status=EnrichmentStatus.PENDING,
            next_attempt_at=now,
        )
        session.add(job)
    else:
        job.status = EnrichmentStatus.PENDING
        job.next_attempt_at = now
        job.locked_at = None
        job.locked_by = None
    product.enrichment_status = EnrichmentStatus.PENDING
    await session.flush()
    return {"barcode": barcode, "enrichmentStatus": product.enrichment_status.value}
