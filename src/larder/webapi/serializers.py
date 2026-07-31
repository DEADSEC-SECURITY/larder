from __future__ import annotations

from uuid import UUID

from larder.models import InventoryItem, StorageLocation, Store
from larder.models.enums import InventoryStatus
from larder.services.tax import item_price_payload
from larder.webapi.common import as_float, expiration_kind, id_status
from larder.webapi.snapshot import CatalogSnapshot, ProductSummary

ITEM_STATUS = {
    InventoryStatus.IN_STOCK: "in_stock",
    InventoryStatus.REMOVED: "removed",
    InventoryStatus.TRASHED: "trashed",
}


def category_payload(summary: ProductSummary) -> dict | None:
    if summary.category is None:
        return None
    category_id, name, parent_name = summary.category
    return {"id": category_id, "name": name, "parentName": parent_name}


def rule_payload(summary: ProductSummary) -> dict | None:
    if summary.rule is None:
        return None
    category_id, name, days = summary.rule
    return {"categoryId": category_id, "categoryName": name, "days": days}


def policy_payload(summary: ProductSummary) -> dict | None:
    if (
        summary.stock_group is not None
        and summary.stock_group.active
        and summary.stock_group_policy is not None
        and summary.stock_group_policy.enabled
    ):
        return {
            "reorderPoint": summary.stock_group_policy.reorder_point,
            "targetStock": summary.stock_group_policy.target_stock,
            "scope": "stock_group",
            "stockGroupId": str(summary.stock_group_policy.stock_group_id),
        }
    if summary.policy is None or not summary.policy.enabled:
        return None
    return {
        "reorderPoint": summary.policy.reorder_point,
        "targetStock": summary.policy.target_stock,
    }


def product_row(summary: ProductSummary, snapshot: CatalogSnapshot) -> dict:
    product = summary.product
    primary_brand = summary.brands[0] if summary.brands else None
    soonest = summary.soonest()
    last_added = max((i.date_added for i in summary.items), default=None)
    return {
        "barcode": product.barcode,
        "name": product.name,
        "productKind": product.product_kind.lower(),
        "description": product.description,
        "manufacturer": product.manufacturer,
        "packageSize": product.package_size,
        "material": product.material,
        "variant": product.variant,
        "identificationSource": product.identification_source,
        "defaultStorageLocationId": str(product.default_storage_location_id)
        if product.default_storage_location_id
        else None,
        "brand": primary_brand.name if primary_brand else product.brand,
        "brandRef": None
        if primary_brand is None
        else {"id": str(primary_brand.id), "name": primary_brand.name},
        "brands": [{"id": str(brand.id), "name": brand.name} for brand in summary.brands],
        "archived": product.archived_at is not None,
        "idStatus": id_status(product),
        "category": category_payload(summary),
        "rule": rule_payload(summary),
        "policy": policy_payload(summary),
        "stockGroup": None
        if summary.stock_group is None
        else {
            "id": str(summary.stock_group.id),
            "name": summary.stock_group.name,
            "active": summary.stock_group.active,
            "source": summary.stock_group_membership.source.lower()
            if summary.stock_group_membership
            else None,
            "confidence": float(summary.stock_group_membership.confidence)
            if summary.stock_group_membership
            and summary.stock_group_membership.confidence is not None
            else None,
            "scopeLabel": "Any compatible brand or size",
            "memberCount": summary.stock_group_member_count,
            "inStock": summary.stock_group_in_stock,
            "policy": None
            if summary.stock_group_policy is None
            or not summary.stock_group_policy.enabled
            else {
                "reorderPoint": summary.stock_group_policy.reorder_point,
                "targetStock": summary.stock_group_policy.target_stock,
            },
        },
        "inStock": summary.in_stock,
        "soonestExpiration": None
        if soonest is None
        else {
            "date": soonest.expiration_date.isoformat(),
            "kind": expiration_kind(soonest),
            "daysLeft": summary.days_left(snapshot.today),
        },
        "hasUnknownDate": summary.has_unknown_date(),
        "lowStock": summary.low_stock(),
        "needsInfo": summary.needs_info(),
        "lowestPrice": summary.lowest_price,
        "lastAddedAt": last_added.isoformat() if last_added else None,
    }


def unit_row(
    item: InventoryItem,
    stores: dict[UUID, Store],
    storage_locations: dict[UUID, StorageLocation] | None = None,
) -> dict:
    store = stores.get(item.store_id) if item.store_id else None
    locations = storage_locations or {}
    storage = locations.get(item.storage_location_id) if item.storage_location_id else None
    return {
        "id": str(item.id),
        "status": ITEM_STATUS[item.status],
        "dateAdded": item.date_added.isoformat(),
        "removedAt": item.removed_at.isoformat() if item.removed_at else None,
        "expirationDate": item.expiration_date.isoformat() if item.expiration_date else None,
        "expirationKind": expiration_kind(item),
        "storeId": str(item.store_id) if item.store_id else None,
        "storeName": store.name if store else None,
        "storageLocationId": str(item.storage_location_id)
        if item.storage_location_id
        else None,
        "storageLocationName": storage.name if storage else None,
        "price": as_float(item.price_paid),
        "priceSubtotal": as_float(item.price_subtotal),
        "taxRate": as_float(item.tax_rate),
        "taxAmount": as_float(item.tax_amount),
        "taxRateId": str(item.tax_rate_id) if item.tax_rate_id else None,
        "taxLocationId": str(item.tax_location_id) if item.tax_location_id else None,
        "taxClass": item.tax_class,
        "taxSource": item.tax_source.lower() if item.tax_source else None,
        "priceBreakdown": item_price_payload(item),
        "currency": item.currency_code,
    }


def store_ref(store: Store | None) -> dict | None:
    if store is None:
        return None
    return {"id": str(store.id), "code": store.code, "name": store.name}
