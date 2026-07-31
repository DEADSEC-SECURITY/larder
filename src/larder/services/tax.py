from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

import httpx
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from larder.config import Settings
from larder.models.tax import TaxLocation, TaxRate

CENT = Decimal("0.01")
RATE_QUANTUM = Decimal("0.000001")

TAX_CLASSES: dict[str, tuple[str, str | None]] = {
    "GENERAL": ("General merchandise", None),
    "GROCERY": ("Unprepared groceries", "40030"),
    "CANDY": ("Candy", "40010"),
    "SUPPLEMENT": ("Dietary supplements", "40020"),
    "SOFT_DRINK": ("Soft drinks", "40050"),
    "PREPARED_FOOD": ("Prepared food", "41000"),
}


@dataclass(frozen=True)
class PriceBreakdown:
    subtotal: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    total: Decimal
    tax_rate_id: UUID | None
    tax_location_id: UUID | None
    tax_class: str
    tax_source: str


def money(value: Decimal | float | str) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def calculate_price(
    subtotal: Decimal | float | str, rate: Decimal | float | str
) -> tuple[Decimal, Decimal, Decimal]:
    clean_subtotal = money(subtotal)
    clean_rate = Decimal(str(rate)).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
    tax = (clean_subtotal * clean_rate).quantize(CENT, rounding=ROUND_HALF_UP)
    return clean_subtotal, tax, clean_subtotal + tax


def calculate_price_from_total(
    total: Decimal | float | str, rate: Decimal | float | str
) -> tuple[Decimal, Decimal, Decimal]:
    """Back out the subtotal when the entered price already includes tax."""
    clean_total = money(total)
    clean_rate = Decimal(str(rate)).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
    subtotal = (clean_total / (1 + clean_rate)).quantize(CENT, rounding=ROUND_HALF_UP)
    return subtotal, clean_total - subtotal, clean_total


async def price_breakdown(
    session: AsyncSession,
    price: Decimal | float | str,
    tax_rate_id: UUID | None,
    tax_included: bool = False,
) -> PriceBreakdown:
    """Breakdown for an entered price.

    `price` is the pre-tax subtotal unless tax_included, in which case it is
    the final total and the subtotal is backed out of it.
    """
    if tax_rate_id is None:
        # No rate: subtotal == total either way.
        clean_subtotal, tax, total = calculate_price(price, Decimal("0"))
        return PriceBreakdown(
            subtotal=clean_subtotal,
            tax_rate=Decimal("0"),
            tax_amount=tax,
            total=total,
            tax_rate_id=None,
            tax_location_id=None,
            tax_class="NONE",
            tax_source="MANUAL",
        )

    rate = await session.get(TaxRate, tax_rate_id)
    if rate is None or not rate.active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "tax rate not found or inactive")
    location = await session.get(TaxLocation, rate.location_id)
    if location is None or not location.active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "tax location is inactive")
    calculate = calculate_price_from_total if tax_included else calculate_price
    clean_subtotal, tax, total = calculate(price, rate.rate)
    return PriceBreakdown(
        subtotal=clean_subtotal,
        tax_rate=rate.rate,
        tax_amount=tax,
        total=total,
        tax_rate_id=rate.id,
        tax_location_id=rate.location_id,
        tax_class=rate.tax_class,
        tax_source=rate.source,
    )


def price_payload(breakdown: PriceBreakdown | None) -> dict | None:
    if breakdown is None:
        return None
    return {
        "subtotal": float(breakdown.subtotal),
        "taxRate": float(breakdown.tax_rate),
        "taxAmount": float(breakdown.tax_amount),
        "total": float(breakdown.total),
        "taxRateId": str(breakdown.tax_rate_id) if breakdown.tax_rate_id else None,
        "taxLocationId": str(breakdown.tax_location_id) if breakdown.tax_location_id else None,
        "taxClass": breakdown.tax_class,
        "taxSource": breakdown.tax_source.lower(),
    }


def item_price_payload(item) -> dict | None:
    if item.price_paid is None:
        return None
    return {
        "subtotal": float(item.price_subtotal) if item.price_subtotal is not None else None,
        "taxRate": float(item.tax_rate) if item.tax_rate is not None else None,
        "taxAmount": float(item.tax_amount) if item.tax_amount is not None else None,
        "total": float(item.price_paid),
        "taxRateId": str(item.tax_rate_id) if item.tax_rate_id else None,
        "taxLocationId": str(item.tax_location_id) if item.tax_location_id else None,
        "taxClass": item.tax_class,
        "taxSource": item.tax_source.lower() if item.tax_source else None,
    }


def apply_price(item, breakdown: PriceBreakdown | None, currency_code: str) -> None:
    if breakdown is None:
        item.price_subtotal = None
        item.tax_rate = None
        item.tax_amount = None
        item.price_paid = None
        item.currency_code = None
        item.tax_rate_id = None
        item.tax_location_id = None
        item.tax_class = None
        item.tax_source = None
        item.price_pending = True
        return
    item.price_subtotal = breakdown.subtotal
    item.tax_rate = breakdown.tax_rate
    item.tax_amount = breakdown.tax_amount
    item.price_paid = breakdown.total
    item.currency_code = currency_code
    item.tax_rate_id = breakdown.tax_rate_id
    item.tax_location_id = breakdown.tax_location_id
    item.tax_class = breakdown.tax_class
    item.tax_source = breakdown.tax_source
    item.price_pending = False


def _sales_rate(payload: dict) -> Decimal:
    metadata = payload.get("metadata", {}).get("response", {})
    if metadata.get("code") != 100:
        message = metadata.get("message") or "tax provider rejected the lookup"
        raise ValueError(message)
    for summary in payload.get("taxSummaries", []):
        if summary.get("taxType") == "SALES_TAX":
            return Decimal(str(summary["rate"])).quantize(RATE_QUANTUM)
    raise ValueError("tax provider response did not include a sales tax rate")


async def lookup_ziptax(
    settings: Settings,
    address: str,
    taxability_code: str | None = None,
    api_key: str | None = None,
) -> tuple[Decimal, str | None]:
    effective_key = api_key or settings.ziptax_api_key
    if not effective_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "ZipTax is not configured; add a key under Settings → Integrations "
            "or enter rates manually",
        )
    params = {"address": address, "countryCode": "USA"}
    if taxability_code:
        params["taxabilityCode"] = taxability_code
    try:
        async with httpx.AsyncClient(timeout=settings.tax_lookup_timeout_seconds) as client:
            response = await client.get(
                settings.ziptax_base_url,
                params=params,
                headers={"X-API-Key": effective_key},
            )
            response.raise_for_status()
            payload = response.json()
            rate = _sales_rate(payload)
            normalized = payload.get("addressDetail", {}).get("normalizedAddress")
            return rate, normalized
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, KeyError) as error:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"ZipTax lookup failed: {error}"
        ) from error
