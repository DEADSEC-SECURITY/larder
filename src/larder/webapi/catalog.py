from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from larder.config import Settings
from larder.models import (
    EnrichmentJob,
    InventoryItem,
    ProductNutrition,
    ProductProviderRecord,
    ProductStoreObservation,
    Store,
)
from larder.webapi.common import (  # noqa: I001
    get_app_settings,
    get_session,
    household_settings,
    require_product,
    web_api_router,
)
from larder.webapi.serializers import product_row, unit_row
from larder.webapi.snapshot import load_snapshot

router = web_api_router(tags=["catalog"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


@router.get("/inventory")
async def inventory(session: SessionDep, settings: SettingsDep) -> dict:
    prefs = await household_settings(session)
    snapshot = await load_snapshot(session, settings, prefs.expiring_window_days)
    stocked = snapshot.stocked()
    return {
        "windowDays": snapshot.window_days,
        "totalUnits": sum(s.in_stock for s in stocked),
        "products": [product_row(s, snapshot) for s in stocked],
    }


@router.get("/products")
async def products(session: SessionDep, settings: SettingsDep) -> dict:
    prefs = await household_settings(session)
    snapshot = await load_snapshot(session, settings, prefs.expiring_window_days)
    rows = sorted(
        (s for s in snapshot.summaries.values() if s.visible),
        key=lambda s: (s.product.name or "￿", s.product.barcode),
    )
    return {"products": [product_row(s, snapshot) for s in rows]}


@router.get("/products/{barcode}")
async def product_detail(barcode: str, session: SessionDep, settings: SettingsDep) -> dict:
    product = await require_product(session, barcode)
    prefs = await household_settings(session)
    snapshot = await load_snapshot(session, settings, prefs.expiring_window_days)
    summary = snapshot.summaries[product.barcode]

    all_items = (
        await session.scalars(
            select(InventoryItem)
            .where(InventoryItem.product_barcode == barcode)
            .order_by(
                InventoryItem.expiration_date.asc().nulls_last(),
                InventoryItem.date_added.desc(),
            )
        )
    ).all()

    observations = (
        await session.execute(
            select(ProductStoreObservation, Store)
            .join(Store, Store.id == ProductStoreObservation.store_id)
            .where(ProductStoreObservation.product_barcode == barcode)
            .order_by(ProductStoreObservation.observed_at.desc())
            .limit(50)
        )
    ).all()
    prices = [float(obs.unit_price) for obs, _ in observations]

    nutrition = await session.get(ProductNutrition, barcode)
    job = await session.scalar(
        select(EnrichmentJob).where(EnrichmentJob.product_barcode == barcode)
    )

    detail = product_row(summary, snapshot)
    detail.update(
        {
            "barcodeFormat": product.barcode_format,
            "imageUrl": product.image_url,
            "quantityText": product.quantity_text,
            "offProductUrl": product.off_product_url,
            "enrichmentStatus": product.enrichment_status.value,
            "enrichmentError": job.last_error if job else None,
            "units": [
                unit_row(i, snapshot.stores, snapshot.storage_locations)
                for i in all_items
            ],
            "history": [
                {
                    "storeId": str(obs.store_id),
                    "storeName": store.name,
                    "observedAt": obs.observed_at.isoformat(),
                    "price": float(obs.unit_price),
                    "priceSubtotal": float(obs.subtotal_price)
                    if obs.subtotal_price is not None
                    else None,
                    "taxRate": float(obs.tax_rate) if obs.tax_rate is not None else None,
                    "taxAmount": float(obs.tax_amount) if obs.tax_amount is not None else None,
                    "taxRateId": str(obs.tax_rate_id) if obs.tax_rate_id else None,
                    "taxLocationId": str(obs.tax_location_id) if obs.tax_location_id else None,
                    "taxClass": obs.tax_class,
                    "taxSource": obs.tax_source.lower() if obs.tax_source else None,
                    "currency": obs.currency_code,
                }
                for obs, store in observations
            ],
            "priceStats": None
            if not prices
            else {
                "lowest": min(prices),
                "average": round(sum(prices) / len(prices), 2),
                "highest": max(prices),
                "byStore": [
                    {
                        "storeId": str(store_id),
                        "storeName": snapshot.stores[store_id].name,
                        "price": price,
                    }
                    for store_id, price in sorted(
                        summary.store_prices.items(), key=lambda kv: kv[1]
                    )
                    if store_id in snapshot.stores
                ],
            },
            "nutrition": None if nutrition is None else _nutrition_payload(nutrition),
            "providerMetadata": product.provider_metadata,
            "fieldSources": product.field_sources,
            "providers": [
                {
                    "provider": record.provider,
                    "productType": record.product_type,
                    "disposition": record.disposition.lower(),
                    "httpStatus": record.http_status,
                    "error": record.error,
                    "fetchedAt": record.fetched_at.isoformat(),
                }
                for record in (
                    await session.scalars(
                        select(ProductProviderRecord)
                        .where(ProductProviderRecord.product_barcode == barcode)
                        .order_by(ProductProviderRecord.provider)
                    )
                ).all()
            ],
        }
    )
    return detail


def _num(value) -> float | None:
    return None if value is None else float(value)


def _nutrition_values(nutrition: ProductNutrition, factor: Decimal = Decimal("1")) -> dict:
    def scaled(value) -> float | None:
        return None if value is None else round(float(value * factor), 3)

    return {
        "energyKcal": scaled(nutrition.energy_kcal_100g),
        "fatG": scaled(nutrition.fat_g_100g),
        "saturatedFatG": scaled(nutrition.saturated_fat_g_100g),
        "carbohydratesG": scaled(nutrition.carbohydrates_g_100g),
        "sugarsG": scaled(nutrition.sugars_g_100g),
        "fiberG": scaled(nutrition.fiber_g_100g),
        "proteinG": scaled(nutrition.protein_g_100g),
        "saltG": scaled(nutrition.salt_g_100g),
        "sodiumG": scaled(nutrition.sodium_g_100g),
    }


def _nutrition_payload(nutrition: ProductNutrition) -> dict:
    serving_matches_basis = (
        nutrition.serving_amount is not None and nutrition.serving_unit == nutrition.basis_unit
    )
    per_serving = (
        _nutrition_values(nutrition, nutrition.serving_amount / Decimal("100"))
        if serving_matches_basis
        else None
    )
    return {
        **_nutrition_values(nutrition),
        "basisAmount": 100,
        "basisUnit": nutrition.basis_unit,
        "servingSize": nutrition.serving_size_text,
        "servingAmount": _num(nutrition.serving_amount),
        "servingUnit": nutrition.serving_unit,
        "perServing": per_serving,
    }
