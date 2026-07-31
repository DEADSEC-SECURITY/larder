"""Resolve a barcode typed or camera-scanned in the web UI.

The hardware scanner path creates unknown products as a side effect of an ADD
scan (services.command_router._add). The web UI has no such side effect: quick-add
requires an existing product, so a barcode the catalogue has never seen would
404. This endpoint is the web equivalent — normalize, look up, and register the
product with a pending enrichment job if it is new.
"""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from larder.config import Settings
from larder.models import EnrichmentJob, Product
from larder.models.enums import EnrichmentStatus
from larder.scanner.parser import BarcodeError, normalize_gtin
from larder.webapi.common import (  # noqa: I001
    MANUAL_BARCODE_PREFIX,
    get_app_settings,
    get_session,
    id_status,
    in_stock_count,
    utcnow,
    web_api_router,
)

router = web_api_router(tags=["barcodes"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


class BarcodeResolveRequest(BaseModel):
    barcode: str = Field(min_length=1, max_length=64)
    # Camera decodes carry their own checksum verification, and a few real-world
    # labels ship a bad digit; let the caller opt out of the strict check the way
    # LARDER_ACCEPT_INVALID_CHECK_DIGIT does for the hardware scanner.
    accept_invalid_check_digit: bool | None = Field(default=None, alias="acceptInvalidCheckDigit")

    model_config = {"populate_by_name": True}


@router.post("/barcodes/resolve")
async def resolve_barcode(
    payload: BarcodeResolveRequest, session: SessionDep, settings: SettingsDep
) -> dict:
    raw = payload.barcode.strip()
    if raw.upper().startswith(MANUAL_BARCODE_PREFIX):
        # MANUAL-* ids are internal handles for products with no real barcode.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"code": "MANUAL_BARCODE", "detail": "manual product ids cannot be scanned"},
        )

    accept_invalid = (
        settings.accept_invalid_check_digit
        if payload.accept_invalid_check_digit is None
        else payload.accept_invalid_check_digit
    )
    try:
        normalized = normalize_gtin(raw, accept_invalid_check_digit=accept_invalid)
    except BarcodeError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"code": exc.code, "detail": exc.detail},
        ) from exc

    now = utcnow()
    result = await session.execute(
        insert(Product)
        .values(
            barcode=normalized.barcode,
            barcode_format=normalized.barcode_format,
            enrichment_status=EnrichmentStatus.PENDING,
        )
        .on_conflict_do_nothing(index_elements=[Product.barcode])
    )
    created = result.rowcount == 1

    if created:
        # Same enqueue contract as an ADD scan: retry a previously failed job,
        # leave a pending/succeeded one alone.
        await session.execute(
            insert(EnrichmentJob)
            .values(
                id=uuid4(),
                product_barcode=normalized.barcode,
                status=EnrichmentStatus.PENDING,
                next_attempt_at=now,
            )
            .on_conflict_do_update(
                index_elements=[EnrichmentJob.product_barcode],
                set_={
                    "status": EnrichmentStatus.PENDING,
                    "next_attempt_at": now,
                    "locked_at": None,
                    "locked_by": None,
                },
                where=EnrichmentJob.status == EnrichmentStatus.FAILED,
            )
        )

    await session.flush()
    product = await session.get(Product, normalized.barcode)
    if product is None:  # pragma: no cover - the upsert above guarantees a row
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "product could not be created")

    return {
        "barcode": product.barcode,
        "raw": normalized.raw,
        "barcodeFormat": product.barcode_format,
        "created": created,
        "name": product.name,
        "brand": product.brand,
        "idStatus": id_status(product),
        "archived": product.archived_at is not None,
        "inStock": await in_stock_count(session, product.barcode),
    }
