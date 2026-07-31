from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from larder.models import Brand, BrandAlias, ProductBrand, StoreRoutingRule
from larder.services.brands import (
    clean_brand_name,
    find_brand_by_name,
    normalize_brand_name,
    rename_brand,
    replace_brand_aliases,
)
from larder.services.store_routing import apply_default_store_to_unassigned_items
from larder.webapi.common import get_session, require_product, web_api_router

router = web_api_router(tags=["brands"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class BrandCreateRequest(CamelModel):
    name: str = Field(min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list, max_length=50)


class BrandUpdateRequest(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    aliases: list[str] | None = Field(default=None, max_length=50)
    active: bool | None = None


class ProductBrandsUpdateRequest(CamelModel):
    brand_ids: list[UUID] = Field(max_length=20)
    primary_brand_id: UUID | None = None

    @model_validator(mode="after")
    def primary_is_assigned(self) -> ProductBrandsUpdateRequest:
        if self.primary_brand_id is not None and self.primary_brand_id not in self.brand_ids:
            raise ValueError("primaryBrandId must be included in brandIds")
        if self.brand_ids and self.primary_brand_id is None:
            self.primary_brand_id = self.brand_ids[0]
        return self


async def _brand_payloads(session: AsyncSession) -> list[dict]:
    brands = (await session.scalars(select(Brand).order_by(Brand.name))).all()
    aliases = (await session.scalars(select(BrandAlias).order_by(BrandAlias.name))).all()
    counts = dict(
        (
            await session.execute(
                select(ProductBrand.brand_id, func.count(ProductBrand.product_barcode)).group_by(
                    ProductBrand.brand_id
                )
            )
        ).all()
    )
    rule_ids = dict(
        (
            await session.execute(
                select(StoreRoutingRule.brand_id, StoreRoutingRule.id).where(
                    StoreRoutingRule.match_type == "BRAND"
                )
            )
        ).all()
    )
    by_brand: dict[UUID, list[str]] = {}
    for alias in aliases:
        by_brand.setdefault(alias.brand_id, []).append(alias.name)
    return [
        {
            "id": str(brand.id),
            "name": brand.name,
            "normalizedName": brand.normalized_name,
            "aliases": by_brand.get(brand.id, []),
            "active": brand.active,
            "productCount": counts.get(brand.id, 0),
            "routingRuleId": str(rule_ids[brand.id]) if brand.id in rule_ids else None,
        }
        for brand in brands
    ]


@router.get("/brands")
async def list_brands(session: SessionDep) -> dict:
    return {"brands": await _brand_payloads(session)}


@router.post("/brands", status_code=status.HTTP_201_CREATED)
async def create_brand(payload: BrandCreateRequest, session: SessionDep) -> dict:
    name = clean_brand_name(payload.name)
    if not normalize_brand_name(name):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "brand name is invalid")
    if await find_brand_by_name(session, name) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "brand name or alias already exists")
    brand = Brand(name=name, normalized_name=normalize_brand_name(name), active=True)
    session.add(brand)
    await session.flush()
    try:
        await replace_brand_aliases(session, brand, payload.aliases)
    except ValueError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    rows = await _brand_payloads(session)
    return next(row for row in rows if row["id"] == str(brand.id))


@router.patch("/brands/{brand_id}")
async def update_brand(brand_id: UUID, payload: BrandUpdateRequest, session: SessionDep) -> dict:
    brand = await session.get(Brand, brand_id, with_for_update=True)
    if brand is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brand not found")
    try:
        if payload.name is not None:
            await rename_brand(session, brand, payload.name)
        if payload.aliases is not None:
            await replace_brand_aliases(session, brand, payload.aliases)
    except ValueError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    if payload.active is not None:
        brand.active = payload.active
    await session.flush()
    rows = await _brand_payloads(session)
    return next(row for row in rows if row["id"] == str(brand.id))


@router.put("/products/{barcode}/brands")
async def set_product_brands(
    barcode: str, payload: ProductBrandsUpdateRequest, session: SessionDep
) -> dict:
    product = await require_product(session, barcode)
    unique_ids = list(dict.fromkeys(payload.brand_ids))
    brands = (
        await session.scalars(select(Brand).where(Brand.id.in_(unique_ids), Brand.active.is_(True)))
    ).all()
    by_id = {brand.id: brand for brand in brands}
    if len(by_id) != len(unique_ids):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "one or more brands were not found")

    await session.execute(delete(ProductBrand).where(ProductBrand.product_barcode == barcode))
    ordered = sorted(
        unique_ids,
        key=lambda brand_id: (brand_id != payload.primary_brand_id, unique_ids.index(brand_id)),
    )
    for rank, brand_id in enumerate(ordered):
        session.add(
            ProductBrand(
                product_barcode=barcode,
                brand_id=brand_id,
                is_primary=rank == 0,
                source="USER",
                source_rank=rank,
            )
        )
    product.brand = by_id[ordered[0]].name if ordered else None
    await session.flush()
    await apply_default_store_to_unassigned_items(session, [barcode])
    return {
        "barcode": barcode,
        "brand": product.brand,
        "brands": [{"id": str(brand_id), "name": by_id[brand_id].name} for brand_id in ordered],
    }
