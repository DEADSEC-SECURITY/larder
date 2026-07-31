from __future__ import annotations

import re
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from larder.domain.names import standardize_name
from larder.models import Brand, BrandAlias, Product, ProductBrand

BRAND_SEPARATOR = re.compile(r"[,;|]+")
NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


def clean_brand_name(value: str) -> str:
    without_marks = value.translate(str.maketrans("", "", "™®©"))
    return standardize_name(without_marks)


def normalize_brand_name(value: str) -> str:
    cleaned = clean_brand_name(value).casefold()
    return " ".join(NON_WORD.sub(" ", cleaned).split())


def split_brand_names(value: str | None) -> list[str]:
    if not value:
        return []
    names: list[str] = []
    seen: set[str] = set()
    for part in BRAND_SEPARATOR.split(value):
        name = clean_brand_name(part)
        normalized = normalize_brand_name(name)
        if name and normalized and normalized not in seen:
            names.append(name)
            seen.add(normalized)
    return names


async def find_brand_by_name(session: AsyncSession, value: str) -> Brand | None:
    normalized = normalize_brand_name(value)
    if not normalized:
        return None
    brand = await session.scalar(select(Brand).where(Brand.normalized_name == normalized))
    if brand is not None:
        return brand
    return await session.scalar(
        select(Brand)
        .join(BrandAlias, BrandAlias.brand_id == Brand.id)
        .where(BrandAlias.normalized_name == normalized)
    )


async def get_or_create_brand(session: AsyncSession, name: str) -> Brand:
    existing = await find_brand_by_name(session, name)
    if existing is not None:
        return existing
    display_name = clean_brand_name(name)
    normalized = normalize_brand_name(display_name)
    brand_id = uuid4()
    await session.execute(
        insert(Brand)
        .values(
            id=brand_id,
            name=display_name,
            normalized_name=normalized,
            active=True,
        )
        .on_conflict_do_nothing(index_elements=[Brand.normalized_name])
    )
    created = await session.scalar(select(Brand).where(Brand.normalized_name == normalized))
    assert created is not None
    return created


async def sync_product_brands(
    session: AsyncSession,
    product: Product,
    raw_brand: str | None,
    *,
    source: str,
) -> list[Brand]:
    names = split_brand_names(raw_brand)
    brands: list[Brand] = []
    for name in names:
        brand = await get_or_create_brand(session, name)
        if brand not in brands:
            brands.append(brand)

    await session.execute(
        delete(ProductBrand).where(ProductBrand.product_barcode == product.barcode)
    )
    for rank, brand in enumerate(brands):
        session.add(
            ProductBrand(
                product_barcode=product.barcode,
                brand_id=brand.id,
                is_primary=rank == 0,
                source=source,
                source_rank=rank,
            )
        )
    product.brand = brands[0].name if brands else None
    await session.flush()
    return brands


async def replace_brand_aliases(
    session: AsyncSession, brand: Brand, aliases: list[str]
) -> list[BrandAlias]:
    cleaned: list[tuple[str, str]] = []
    seen = {brand.normalized_name}
    for value in aliases:
        name = clean_brand_name(value)
        normalized = normalize_brand_name(name)
        if not name or not normalized or normalized in seen:
            continue
        cleaned.append((name, normalized))
        seen.add(normalized)

    normalized_values = [normalized for _, normalized in cleaned]
    if normalized_values:
        brand_conflict = await session.scalar(
            select(Brand).where(Brand.normalized_name.in_(normalized_values), Brand.id != brand.id)
        )
        alias_conflict = await session.scalar(
            select(BrandAlias).where(
                BrandAlias.normalized_name.in_(normalized_values),
                BrandAlias.brand_id != brand.id,
            )
        )
        if brand_conflict is not None or alias_conflict is not None:
            raise ValueError("brand name or alias is already in use")

    await session.execute(delete(BrandAlias).where(BrandAlias.brand_id == brand.id))
    rows = [
        BrandAlias(brand_id=brand.id, name=name, normalized_name=normalized)
        for name, normalized in cleaned
    ]
    session.add_all(rows)
    await session.flush()
    return rows


async def rename_brand(session: AsyncSession, brand: Brand, name: str) -> None:
    display_name = clean_brand_name(name)
    normalized = normalize_brand_name(display_name)
    if not normalized:
        raise ValueError("brand name cannot be empty")
    brand_conflict = await session.scalar(
        select(Brand.id).where(Brand.normalized_name == normalized, Brand.id != brand.id)
    )
    alias_conflict = await session.scalar(
        select(BrandAlias.brand_id).where(
            BrandAlias.normalized_name == normalized, BrandAlias.brand_id != brand.id
        )
    )
    if brand_conflict is not None or alias_conflict is not None:
        raise ValueError("brand name or alias is already in use")
    await session.execute(
        delete(BrandAlias).where(
            BrandAlias.brand_id == brand.id, BrandAlias.normalized_name == normalized
        )
    )
    brand.name = display_name
    brand.normalized_name = normalized
    await session.execute(
        update(Product)
        .where(
            Product.barcode.in_(
                select(ProductBrand.product_barcode).where(
                    ProductBrand.brand_id == brand.id, ProductBrand.is_primary.is_(True)
                )
            )
        )
        .values(brand=display_name)
    )
