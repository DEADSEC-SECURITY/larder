from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.routing import APIRoute
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from larder.config import Settings
from larder.models import (
    FoodCategory,
    HouseholdSettings,
    InventoryEvent,
    InventoryItem,
    Product,
    ProductCategory,
    ScanEvent,
)
from larder.models.enums import (
    EnrichmentStatus,
    InventoryEventType,
    InventoryStatus,
    ScanKind,
    ScanResult,
)
from larder.models.settings import HOUSEHOLD_SETTINGS_ID

WEB_ACTOR = "web-admin"
MANUAL_BARCODE_PREFIX = "MANUAL-"
MANUAL_BARCODE_FORMAT = "MANUAL"


class TransactionalRoute(APIRoute):
    """Commit the request's session BEFORE the response is sent.

    FastAPI runs yield-dependency teardown after the response, so a client that
    writes and immediately reads could otherwise observe pre-commit state.
    """

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            response = await original(request)
            session = getattr(request.state, "db_session", None)
            if session is not None and session.in_transaction():
                await session.commit()
            return response

        return handler


def web_api_router(**kwargs) -> APIRouter:
    return APIRouter(route_class=TransactionalRoute, **kwargs)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = request.app.state.session_factory
    async with factory() as session:
        request.state.db_session = session
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            # Normally already committed by TransactionalRoute; this covers
            # any path that bypassed it.
            if session.in_transaction():
                await session.commit()


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


async def household_settings(
    session: AsyncSession, *, for_update: bool = False
) -> HouseholdSettings:
    row = await session.get(HouseholdSettings, HOUSEHOLD_SETTINGS_ID, with_for_update=for_update)
    if row is None:
        row = HouseholdSettings(id=HOUSEHOLD_SETTINGS_ID)
        session.add(row)
        await session.flush()
    return row


def utcnow() -> datetime:
    return datetime.now(UTC)


def local_today(settings: Settings) -> date:
    return utcnow().astimezone(ZoneInfo(settings.household_timezone)).date()


def new_manual_barcode() -> str:
    return MANUAL_BARCODE_PREFIX + uuid4().hex[:10].upper()


def slug_code(name: str, *, max_length: int = 32) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", name).upper()
    return (cleaned or "STORE")[:max_length]


def id_status(product: Product) -> str:
    """Collapse enrichment lifecycle onto the UI's identified/identifying/failed axis."""
    if product.enrichment_status == EnrichmentStatus.SUCCEEDED or product.name:
        return "ok"
    if product.enrichment_status in (EnrichmentStatus.FAILED, EnrichmentStatus.NOT_FOUND):
        return "failed"
    return "pending"


def expiration_kind(item: InventoryItem) -> str:
    if item.expiration_date is None:
        return "unknown"
    if item.expiration_is_estimated:
        return "estimated"
    return "confirmed"


async def create_web_scan_event(
    session: AsyncSession,
    *,
    barcode: str | None,
    result: ScanResult = ScanResult.APPLIED,
) -> ScanEvent:
    """Inventory rows require scan-event provenance; web actions synthesize one."""
    event_id = uuid4()
    now = utcnow()
    event = ScanEvent(
        id=event_id,
        idempotency_key=f"web:{event_id}",
        received_at=now,
        raw_frame=f"WEB:{event_id}",
        payload_raw=barcode,
        barcode=barcode,
        kind=ScanKind.PRODUCT,
        result=result,
        processed_at=now,
        processing_attempts=1,
    )
    session.add(event)
    await session.flush()
    return event


def add_inventory_event(
    session: AsyncSession,
    *,
    item_id: UUID,
    event_type: InventoryEventType,
    scan_event_id: UUID | None,
    before_state: dict | None = None,
    after_state: dict | None = None,
    reason: str | None = None,
    actor_type: str = "USER",
    actor_id: str = WEB_ACTOR,
    occurred_at: datetime | None = None,
) -> None:
    session.add(
        InventoryEvent(
            inventory_item_id=item_id,
            event_type=event_type,
            occurred_at=occurred_at or utcnow(),
            scan_event_id=scan_event_id,
            actor_type=actor_type,
            actor_id=actor_id,
            before_state=before_state,
            after_state=after_state,
            reason=reason,
        )
    )


async def require_item(session: AsyncSession, item_id: UUID) -> InventoryItem:
    item = await session.get(InventoryItem, item_id, with_for_update=True)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "inventory item not found")
    return item


async def require_product(session: AsyncSession, barcode: str) -> Product:
    product = await session.get(Product, barcode)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product not found")
    return product


async def effective_rule(
    session: AsyncSession, barcode: str, fallback_category_id: str
) -> tuple[str, str, int] | None:
    """Mirror services.expiration.resolve_expiration: (category_id, display_name, days)."""
    statement = (
        select(FoodCategory.id, FoodCategory.display_name, FoodCategory.default_expiration_days)
        .join(ProductCategory, ProductCategory.category_id == FoodCategory.id)
        .where(
            ProductCategory.product_barcode == barcode,
            FoodCategory.active.is_(True),
            FoodCategory.default_expiration_days.is_not(None),
        )
        .order_by(ProductCategory.source_rank.desc().nulls_last(), FoodCategory.id)
        .limit(1)
    )
    rule = (await session.execute(statement)).one_or_none()
    if rule is None:
        rule = (
            await session.execute(
                select(
                    FoodCategory.id,
                    FoodCategory.display_name,
                    FoodCategory.default_expiration_days,
                ).where(
                    FoodCategory.id == fallback_category_id,
                    FoodCategory.active.is_(True),
                    FoodCategory.default_expiration_days.is_not(None),
                )
            )
        ).one_or_none()
    if rule is None:
        return None
    return rule.id, rule.display_name, rule.default_expiration_days


async def primary_category(
    session: AsyncSession, barcode: str
) -> tuple[str, str, str | None] | None:
    """Best display category for a product: (id, display_name, parent_display_name)."""
    parent = FoodCategory.__table__.alias("parent")
    statement = (
        select(
            FoodCategory.id,
            FoodCategory.display_name,
            parent.c.display_name.label("parent_name"),
            ProductCategory.is_primary,
            ProductCategory.source_rank,
        )
        .join(ProductCategory, ProductCategory.category_id == FoodCategory.id)
        .outerjoin(parent, parent.c.id == FoodCategory.parent_id)
        .where(ProductCategory.product_barcode == barcode, FoodCategory.active.is_(True))
        .order_by(
            ProductCategory.is_primary.desc(),
            ProductCategory.source_rank.desc().nulls_last(),
            FoodCategory.id,
        )
        .limit(1)
    )
    row = (await session.execute(statement)).one_or_none()
    if row is None:
        return None
    return row.id, row.display_name, row.parent_name


def removal_order_key(policy: str):
    """SQL ordering for taking one unit under the configured removal policy."""
    if policy == "FIFO":
        return (InventoryItem.date_added.asc(), InventoryItem.id.asc())
    if policy == "LIFO":
        return (InventoryItem.date_added.desc(), InventoryItem.id.asc())
    if policy == "LEFO":
        return (
            InventoryItem.expiration_date.desc().nulls_first(),
            InventoryItem.date_added.desc(),
            InventoryItem.id.asc(),
        )
    return (
        InventoryItem.expiration_date.asc().nulls_last(),
        InventoryItem.date_added.asc(),
        InventoryItem.id.asc(),
    )


async def in_stock_count(session: AsyncSession, barcode: str) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(InventoryItem)
            .where(
                InventoryItem.product_barcode == barcode,
                InventoryItem.status == InventoryStatus.IN_STOCK,
            )
        )
    ) or 0


def as_float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)
