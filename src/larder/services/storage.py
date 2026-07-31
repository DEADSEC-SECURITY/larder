from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from larder.models import FoodCategory, Product, ProductCategory


async def default_storage_location_for_product(
    session: AsyncSession, barcode: str
) -> UUID | None:
    product = await session.get(Product, barcode)
    if product is not None and product.default_storage_location_id is not None:
        return product.default_storage_location_id
    return await session.scalar(
        select(FoodCategory.default_storage_location_id)
        .join(ProductCategory, ProductCategory.category_id == FoodCategory.id)
        .where(
            ProductCategory.product_barcode == barcode,
            FoodCategory.active.is_(True),
            FoodCategory.default_storage_location_id.is_not(None),
        )
        .order_by(
            ProductCategory.is_primary.desc(),
            ProductCategory.source_rank.desc().nulls_last(),
            FoodCategory.id,
        )
        .limit(1)
    )
