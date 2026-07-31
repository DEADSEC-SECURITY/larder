from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from larder.config import Settings
from larder.models import TaxLocation, TaxRate
from larder.services.integrations import resolve_api_key
from larder.services.tax import TAX_CLASSES, lookup_ziptax
from larder.webapi.common import get_app_settings, get_session, web_api_router

router = web_api_router(tags=["taxes"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class AddressFields(CamelModel):
    label: str = Field(min_length=1, max_length=100)
    address_line1: str | None = Field(default=None, max_length=200)
    address_line2: str | None = Field(default=None, max_length=100)
    city: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=2, max_length=3)
    postal_code: str = Field(min_length=5, max_length=16)
    country_code: str = Field(default="USA", min_length=2, max_length=3)

    @field_validator("state", "country_code")
    @classmethod
    def uppercase_code(cls, value: str) -> str:
        return value.strip().upper()


class ManualRate(CamelModel):
    tax_class: str
    label: str | None = Field(default=None, max_length=100)
    rate: Decimal = Field(ge=0, le=1)

    @field_validator("tax_class")
    @classmethod
    def supported_class(cls, value: str) -> str:
        clean = value.strip().upper()
        if clean not in TAX_CLASSES:
            raise ValueError(f"unsupported tax class: {clean}")
        return clean


class ManualLocationRequest(AddressFields):
    rates: list[ManualRate] = Field(min_length=1, max_length=len(TAX_CLASSES))


class LookupLocationRequest(AddressFields):
    tax_classes: list[str] = Field(default_factory=lambda: ["GENERAL"], max_length=6)

    @field_validator("tax_classes")
    @classmethod
    def supported_classes(cls, values: list[str]) -> list[str]:
        clean = list(dict.fromkeys(value.strip().upper() for value in values))
        unsupported = [value for value in clean if value not in TAX_CLASSES]
        if unsupported:
            raise ValueError(f"unsupported tax classes: {', '.join(unsupported)}")
        return clean


class TaxLocationUpdateRequest(CamelModel):
    label: str | None = Field(default=None, min_length=1, max_length=100)
    active: bool | None = None


class TaxRateUpdateRequest(CamelModel):
    label: str | None = Field(default=None, min_length=1, max_length=100)
    rate: Decimal | None = Field(default=None, ge=0, le=1)
    active: bool | None = None


def _address_text(payload: AddressFields) -> str:
    parts = [
        payload.address_line1,
        payload.address_line2,
        payload.city,
        payload.state,
        payload.postal_code,
    ]
    return ", ".join(part.strip() for part in parts if part and part.strip())


def _rate_payload(rate: TaxRate) -> dict:
    return {
        "id": str(rate.id),
        "taxClass": rate.tax_class,
        "label": rate.label,
        "rate": float(rate.rate),
        "percentage": round(float(rate.rate) * 100, 4),
        "taxabilityCode": rate.taxability_code,
        "source": rate.source.lower(),
        "fetchedAt": rate.fetched_at.isoformat() if rate.fetched_at else None,
        "active": rate.active,
    }


async def _location_payload(session: AsyncSession, location: TaxLocation) -> dict:
    rates = (
        await session.scalars(
            select(TaxRate).where(TaxRate.location_id == location.id).order_by(TaxRate.tax_class)
        )
    ).all()
    return {
        "id": str(location.id),
        "label": location.label,
        "addressLine1": location.address_line1,
        "addressLine2": location.address_line2,
        "city": location.city,
        "state": location.state,
        "postalCode": location.postal_code,
        "countryCode": location.country_code,
        "normalizedAddress": location.normalized_address,
        "provider": location.provider.lower(),
        "active": location.active,
        "rates": [_rate_payload(rate) for rate in rates],
    }


async def _active_address_conflict(
    session: AsyncSession,
    address_line1: str | None,
    city: str,
    state: str,
    postal_code: str,
    exclude_id: UUID | None = None,
) -> TaxLocation | None:
    """Active location already covering this address (labels are not unique)."""
    statement = select(TaxLocation).where(
        TaxLocation.active.is_(True),
        func.lower(func.coalesce(TaxLocation.address_line1, ""))
        == (address_line1 or "").strip().lower(),
        func.lower(TaxLocation.city) == city.strip().lower(),
        func.lower(TaxLocation.state) == state.strip().lower(),
        TaxLocation.postal_code == postal_code.strip(),
    )
    if exclude_id is not None:
        statement = statement.where(TaxLocation.id != exclude_id)
    return await session.scalar(statement)


async def _new_location(
    session: AsyncSession, payload: AddressFields, provider: str
) -> TaxLocation:
    # Identity is the address; "removed" locations are deactivated, not deleted
    # (price history references their rates), and don't block re-adding.
    duplicate = await _active_address_conflict(
        session, payload.address_line1, payload.city, payload.state, payload.postal_code
    )
    if duplicate is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"that address is already saved as “{duplicate.label}”",
        )
    location = TaxLocation(
        label=payload.label.strip(),
        address_line1=payload.address_line1.strip() if payload.address_line1 else None,
        address_line2=payload.address_line2.strip() if payload.address_line2 else None,
        city=payload.city.strip(),
        state=payload.state,
        postal_code=payload.postal_code.strip(),
        country_code=payload.country_code,
        provider=provider,
    )
    session.add(location)
    await session.flush()
    return location


@router.get("/tax-classes")
async def tax_classes(session: SessionDep, settings: SettingsDep) -> dict:
    key, _source = await resolve_api_key(session, settings, "ziptax")
    return {
        "provider": {
            "name": "ziptax",
            "configured": bool(key),
            "productRatesRequirePaidPlan": True,
        },
        "classes": [
            {
                "id": tax_class,
                "label": label,
                "taxabilityCode": code,
                "providerPlan": "all" if code is None else "pro",
            }
            for tax_class, (label, code) in TAX_CLASSES.items()
        ],
    }


@router.get("/tax-locations")
async def list_tax_locations(session: SessionDep, include_inactive: bool = False) -> dict:
    statement = select(TaxLocation).order_by(TaxLocation.label)
    if not include_inactive:
        statement = statement.where(TaxLocation.active.is_(True))
    locations = (await session.scalars(statement)).all()
    return {"locations": [await _location_payload(session, row) for row in locations]}


@router.post("/tax-locations", status_code=status.HTTP_201_CREATED)
async def create_manual_tax_location(payload: ManualLocationRequest, session: SessionDep) -> dict:
    location = await _new_location(session, payload, "MANUAL")
    seen: set[str] = set()
    for requested in payload.rates:
        if requested.tax_class in seen:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "duplicate tax class")
        seen.add(requested.tax_class)
        class_label, code = TAX_CLASSES[requested.tax_class]
        session.add(
            TaxRate(
                location_id=location.id,
                tax_class=requested.tax_class,
                label=requested.label.strip() if requested.label else class_label,
                rate=requested.rate,
                taxability_code=code,
                source="MANUAL",
            )
        )
    await session.flush()
    return await _location_payload(session, location)


@router.post("/tax-locations/lookup", status_code=status.HTTP_201_CREATED)
async def lookup_tax_location(
    payload: LookupLocationRequest, session: SessionDep, settings: SettingsDep
) -> dict:
    api_key, _source = await resolve_api_key(session, settings, "ziptax")
    location = await _new_location(session, payload, "ZIPTAX")
    normalized: str | None = None
    now = datetime.now(UTC)
    for tax_class in payload.tax_classes:
        label, code = TAX_CLASSES[tax_class]
        rate, returned_address = await lookup_ziptax(
            settings, _address_text(payload), code, api_key=api_key
        )
        normalized = returned_address or normalized
        session.add(
            TaxRate(
                location_id=location.id,
                tax_class=tax_class,
                label=label,
                rate=rate,
                taxability_code=code,
                source="ZIPTAX",
                fetched_at=now,
            )
        )
    location.normalized_address = normalized
    await session.flush()
    return await _location_payload(session, location)


@router.patch("/tax-locations/{location_id}")
async def update_tax_location(
    location_id: UUID, payload: TaxLocationUpdateRequest, session: SessionDep
) -> dict:
    location = await session.get(TaxLocation, location_id, with_for_update=True)
    if location is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tax location not found")
    fields = payload.model_fields_set
    if "label" in fields and payload.label is not None:
        location.label = payload.label.strip()
    if "active" in fields and payload.active is not None:
        if payload.active and not location.active:
            conflict = await _active_address_conflict(
                session,
                location.address_line1,
                location.city,
                location.state,
                location.postal_code,
                exclude_id=location.id,
            )
            if conflict is not None:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"an active location “{conflict.label}” already covers that address",
                )
            location.active = True
            # Restore brings the whole location back, rates included.
            rates = await session.scalars(
                select(TaxRate).where(TaxRate.location_id == location.id)
            )
            for rate in rates:
                rate.active = True
        elif not payload.active and location.active:
            location.active = False
            rates = await session.scalars(
                select(TaxRate).where(TaxRate.location_id == location.id)
            )
            for rate in rates:
                rate.active = False
    await session.flush()
    return await _location_payload(session, location)


@router.patch("/tax-rates/{rate_id}")
async def update_tax_rate(
    rate_id: UUID, payload: TaxRateUpdateRequest, session: SessionDep
) -> dict:
    rate = await session.get(TaxRate, rate_id, with_for_update=True)
    if rate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tax rate not found")
    fields = payload.model_fields_set
    if "label" in fields and payload.label is not None:
        rate.label = payload.label.strip()
    if "rate" in fields and payload.rate is not None:
        rate.rate = payload.rate
        rate.source = "MANUAL"
        rate.fetched_at = None
    if "active" in fields and payload.active is not None:
        rate.active = payload.active
    await session.flush()
    return _rate_payload(rate)


@router.delete("/tax-locations/{location_id}")
async def deactivate_tax_location(location_id: UUID, session: SessionDep) -> dict:
    location = await session.get(TaxLocation, location_id, with_for_update=True)
    if location is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tax location not found")
    location.active = False
    rates = await session.scalars(select(TaxRate).where(TaxRate.location_id == location.id))
    for rate in rates:
        rate.active = False
    await session.flush()
    return {"id": str(location.id), "active": False}
