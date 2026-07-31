from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from larder.models import FoodCategory, ProductCategory


@dataclass(frozen=True, slots=True)
class ExpirationSnapshot:
    expiration_date: date
    category_id: str
    offset_days: int


async def resolve_expiration(
    session: AsyncSession,
    product_barcode: str,
    added_at: datetime,
    household_timezone: str,
    fallback_category_id: str,
) -> ExpirationSnapshot | None:
    statement = (
        select(FoodCategory.id, FoodCategory.default_expiration_days)
        .join(ProductCategory, ProductCategory.category_id == FoodCategory.id)
        .where(
            ProductCategory.product_barcode == product_barcode,
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
                select(FoodCategory.id, FoodCategory.default_expiration_days).where(
                    FoodCategory.id == fallback_category_id,
                    FoodCategory.active.is_(True),
                    FoodCategory.default_expiration_days.is_not(None),
                )
            )
        ).one_or_none()
    if rule is None:
        return None
    local_date = added_at.astimezone(ZoneInfo(household_timezone)).date()
    return ExpirationSnapshot(
        expiration_date=local_date + timedelta(days=rule.default_expiration_days),
        category_id=rule.id,
        offset_days=rule.default_expiration_days,
    )
