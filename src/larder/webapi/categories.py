from __future__ import annotations

import re
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from larder.domain.names import standardize_name
from larder.models import FoodCategory, ProductCategory, StorageLocation
from larder.webapi.common import get_session, web_api_router

router = web_api_router(tags=["categories"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

LOCAL_PREFIX = "local:"
ProductKind = Literal[
    "FOOD", "HOUSEHOLD", "PERSONAL_CARE", "MEDICAL", "PET", "OFFICE", "OTHER"
]


class CategoryCreateRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid"
    )
    name: str = Field(min_length=1, max_length=100)
    parent_id: str | None = None
    days: int | None = Field(default=None, ge=0, le=3650)
    product_kind: ProductKind = "FOOD"
    default_storage_location_id: UUID | None = None


class CategoryUpdateRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid"
    )
    days: int | None = Field(default=None, ge=0, le=3650)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    parent_id: str | None = None
    product_kind: ProductKind | None = None
    default_storage_location_id: UUID | None = None


async def _validate_storage_location(
    session: AsyncSession, location_id: UUID | None
) -> None:
    if location_id is None:
        return
    location = await session.get(StorageLocation, location_id)
    if location is None or not location.active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "storage location not found")


def local_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"{LOCAL_PREFIX}{slug or 'category'}"


def _inherited_days(
    category: FoodCategory, by_id: dict[str, FoodCategory]
) -> int | None:
    """Return the closest ancestor expiration default, guarding against bad cycles."""
    seen = {category.id}
    parent_id = category.parent_id
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        parent = by_id.get(parent_id)
        if parent is None:
            return None
        if parent.default_expiration_days is not None:
            return parent.default_expiration_days
        parent_id = parent.parent_id
    return None


async def _validate_parent(
    session: AsyncSession, category_id: str, parent_id: str | None
) -> None:
    if parent_id is None:
        return
    if parent_id == category_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "category cannot parent itself"
        )
    parent = await session.get(FoodCategory, parent_id)
    if parent is None or not parent.active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "parent category not found")
    seen = {category_id}
    current: FoodCategory | None = parent
    while current is not None:
        if current.id in seen:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "category cycle detected"
            )
        seen.add(current.id)
        current = (
            await session.get(FoodCategory, current.parent_id)
            if current.parent_id
            else None
        )


@router.get("/categories")
async def list_categories(session: SessionDep) -> dict:
    categories = (
        await session.scalars(
            select(FoodCategory)
            .where(FoodCategory.active.is_(True))
            .order_by(FoodCategory.display_name)
        )
    ).all()
    counts = dict(
        (
            await session.execute(
                select(
                    ProductCategory.category_id,
                    func.count(ProductCategory.product_barcode),
                ).group_by(ProductCategory.category_id)
            )
        ).all()
    )
    by_id = {c.id: c for c in categories}
    payload = []
    for category in categories:
        parent = by_id.get(category.parent_id) if category.parent_id else None
        inherited = (
            _inherited_days(category, by_id)
            if category.default_expiration_days is None
            else None
        )
        payload.append(
            {
                "id": category.id,
                "name": category.display_name,
                "parentId": category.parent_id,
                "parentName": parent.display_name if parent else None,
                "parentSource": category.parent_source,
                "productKind": category.product_kind.lower(),
                "defaultStorageLocationId": str(category.default_storage_location_id)
                if category.default_storage_location_id
                else None,
                "days": category.default_expiration_days,
                "inheritedDays": inherited,
                "productCount": counts.get(category.id, 0),
            }
        )
    nodes = {row["id"]: {**row, "children": []} for row in payload}
    roots = []
    for row in payload:
        node = nodes[row["id"]]
        parent = nodes.get(row["parentId"]) if row["parentId"] else None
        if parent is None:
            roots.append(node)
        else:
            parent["children"].append(node)

    def sort_tree(items: list[dict]) -> None:
        items.sort(key=lambda item: (item["name"].casefold(), item["id"]))
        for item in items:
            sort_tree(item["children"])

    sort_tree(roots)
    return {"categories": payload, "tree": roots}


@router.post("/categories", status_code=status.HTTP_201_CREATED)
async def create_category(payload: CategoryCreateRequest, session: SessionDep) -> dict:
    name = standardize_name(payload.name)
    category_id = local_id(name)
    existing = await session.get(FoodCategory, category_id)
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "category already exists")
    await _validate_parent(session, category_id, payload.parent_id)
    await _validate_storage_location(session, payload.default_storage_location_id)
    category = FoodCategory(
        id=category_id,
        display_name=name,
        parent_id=payload.parent_id,
        parent_source="MANUAL",
        product_kind=payload.product_kind,
        default_storage_location_id=payload.default_storage_location_id,
        default_expiration_days=payload.days,
        active=True,
    )
    session.add(category)
    await session.flush()
    return {
        "id": category.id,
        "name": category.display_name,
        "parentId": category.parent_id,
        "parentSource": category.parent_source,
        "productKind": category.product_kind.lower(),
        "defaultStorageLocationId": str(category.default_storage_location_id)
        if category.default_storage_location_id
        else None,
        "days": payload.days,
    }


@router.patch("/categories/{category_id:path}")
async def update_category(
    category_id: str, payload: CategoryUpdateRequest, session: SessionDep
) -> dict:
    category = await session.get(FoodCategory, category_id, with_for_update=True)
    if category is None or not category.active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "category not found")
    fields = payload.model_fields_set
    if "days" in fields:
        category.default_expiration_days = payload.days
    if payload.name and payload.name.strip():
        category.display_name = standardize_name(payload.name)
    if "parent_id" in fields:
        await _validate_parent(session, category.id, payload.parent_id)
        category.parent_id = payload.parent_id
        category.parent_source = "MANUAL"
    if payload.product_kind is not None:
        category.product_kind = payload.product_kind
    if "default_storage_location_id" in fields:
        await _validate_storage_location(session, payload.default_storage_location_id)
        category.default_storage_location_id = payload.default_storage_location_id
    await session.flush()
    return {
        "id": category.id,
        "name": category.display_name,
        "parentId": category.parent_id,
        "parentSource": category.parent_source,
        "productKind": category.product_kind.lower(),
        "defaultStorageLocationId": str(category.default_storage_location_id)
        if category.default_storage_location_id
        else None,
        "days": category.default_expiration_days,
    }
