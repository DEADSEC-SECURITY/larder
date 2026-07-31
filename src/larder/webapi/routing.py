from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from larder.config import Settings
from larder.domain.names import standardize_name
from larder.models import (
    Brand,
    FoodCategory,
    Product,
    ProductBrand,
    ProductCategory,
    Store,
    StoreRoutingRule,
)
from larder.services.store_routing import (
    apply_default_stores_for_rule,
    load_routing_context,
    resolve_store_assignment,
)
from larder.webapi.common import (  # noqa: I001
    get_app_settings,
    get_session,
    household_settings,
    require_product,
    web_api_router,
)
from larder.webapi.snapshot import load_snapshot

router = web_api_router(tags=["store-routing"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class RoutingRuleCreateRequest(CamelModel):
    name: str | None = Field(default=None, max_length=120)
    match_type: Literal["PRODUCT", "BRAND", "CATEGORY"]
    product_barcode: str | None = None
    brand_id: UUID | None = None
    category_id: str | None = None
    store_id: UUID
    behavior: Literal["PREFER", "REQUIRE"] = "PREFER"
    priority: int = Field(default=0, ge=-1000, le=1000)
    enabled: bool = True

    @field_validator("match_type", "behavior", mode="before")
    @classmethod
    def uppercase_enums(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def exactly_one_matcher(self) -> RoutingRuleCreateRequest:
        values = {
            "PRODUCT": self.product_barcode,
            "BRAND": self.brand_id,
            "CATEGORY": self.category_id,
        }
        if (
            values[self.match_type] is None
            or sum(value is not None for value in values.values()) != 1
        ):
            raise ValueError("exactly the matcher matching matchType is required")
        return self


class RoutingRuleUpdateRequest(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    store_id: UUID | None = None
    behavior: Literal["PREFER", "REQUIRE"] | None = None
    priority: int | None = Field(default=None, ge=-1000, le=1000)
    enabled: bool | None = None

    @field_validator("behavior", mode="before")
    @classmethod
    def uppercase_behavior(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None


async def _match_label(session: AsyncSession, rule: StoreRoutingRule) -> str:
    if rule.match_type == "PRODUCT":
        product = await session.get(Product, rule.product_barcode)
        return (product.name or product.barcode) if product else rule.product_barcode or "Product"
    if rule.match_type == "BRAND":
        brand = await session.get(Brand, rule.brand_id)
        return brand.name if brand else "Deleted brand"
    category = await session.get(FoodCategory, rule.category_id)
    return category.display_name if category else "Deleted category"


async def _match_count(session: AsyncSession, rule: StoreRoutingRule) -> int:
    if rule.match_type == "PRODUCT":
        return int(
            await session.scalar(
                select(func.count())
                .select_from(Product)
                .where(Product.barcode == rule.product_barcode)
            )
            or 0
        )
    if rule.match_type == "BRAND":
        return int(
            await session.scalar(
                select(func.count())
                .select_from(ProductBrand)
                .where(ProductBrand.brand_id == rule.brand_id)
            )
            or 0
        )
    return int(
        await session.scalar(
            select(func.count())
            .select_from(ProductCategory)
            .where(ProductCategory.category_id == rule.category_id)
        )
        or 0
    )


async def _rule_payload(session: AsyncSession, rule: StoreRoutingRule) -> dict:
    store = await session.get(Store, rule.store_id)
    target_id = {
        "PRODUCT": rule.product_barcode,
        "BRAND": str(rule.brand_id) if rule.brand_id else None,
        "CATEGORY": rule.category_id,
    }[rule.match_type]
    return {
        "id": str(rule.id),
        "name": rule.name,
        "matchType": rule.match_type.lower(),
        "match": {
            "id": target_id,
            "label": await _match_label(session, rule),
            "productCount": await _match_count(session, rule),
        },
        "store": None
        if store is None
        else {
            "id": str(store.id),
            "code": store.code,
            "name": store.name,
            "active": store.active,
        },
        "behavior": rule.behavior.lower(),
        "priority": rule.priority,
        "enabled": rule.enabled,
        "createdAt": rule.created_at.isoformat(),
        "updatedAt": rule.updated_at.isoformat(),
    }


async def _require_targets(
    session: AsyncSession, payload: RoutingRuleCreateRequest
) -> tuple[str, Store]:
    store = await session.get(Store, payload.store_id)
    if store is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "store not found")
    if payload.match_type == "PRODUCT":
        product = await require_product(session, payload.product_barcode)
        label = product.name or product.barcode
    elif payload.match_type == "BRAND":
        brand = await session.get(Brand, payload.brand_id)
        if brand is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "brand not found")
        label = brand.name
    else:
        category = await session.get(FoodCategory, payload.category_id)
        if category is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "category not found")
        label = category.display_name
    return label, store


async def _existing_rule(
    session: AsyncSession, payload: RoutingRuleCreateRequest
) -> StoreRoutingRule | None:
    statement = select(StoreRoutingRule).where(StoreRoutingRule.match_type == payload.match_type)
    if payload.match_type == "PRODUCT":
        statement = statement.where(StoreRoutingRule.product_barcode == payload.product_barcode)
    elif payload.match_type == "BRAND":
        statement = statement.where(StoreRoutingRule.brand_id == payload.brand_id)
    else:
        statement = statement.where(StoreRoutingRule.category_id == payload.category_id)
    return await session.scalar(statement)


@router.get("/store-routing-rules")
async def list_routing_rules(session: SessionDep) -> dict:
    rules = (
        await session.scalars(
            select(StoreRoutingRule).order_by(
                StoreRoutingRule.enabled.desc(),
                StoreRoutingRule.priority.desc(),
                StoreRoutingRule.name,
            )
        )
    ).all()
    return {
        "rules": [await _rule_payload(session, rule) for rule in rules],
        "precedence": ["manual", "product", "brand", "category", "price_history"],
    }


@router.post("/store-routing-rules", status_code=status.HTTP_201_CREATED)
async def create_routing_rule(payload: RoutingRuleCreateRequest, session: SessionDep) -> dict:
    label, store = await _require_targets(session, payload)
    if await _existing_rule(session, payload) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "a rule already exists for this matcher")
    rule = StoreRoutingRule(
        name=standardize_name(payload.name or f"{label} to {store.name}"),
        match_type=payload.match_type,
        product_barcode=payload.product_barcode,
        brand_id=payload.brand_id,
        category_id=payload.category_id,
        store_id=store.id,
        behavior=payload.behavior,
        priority=payload.priority,
        enabled=payload.enabled,
    )
    session.add(rule)
    await session.flush()
    await apply_default_stores_for_rule(session, rule)
    return await _rule_payload(session, rule)


@router.patch("/store-routing-rules/{rule_id}")
async def update_routing_rule(
    rule_id: UUID, payload: RoutingRuleUpdateRequest, session: SessionDep
) -> dict:
    rule = await session.get(StoreRoutingRule, rule_id, with_for_update=True)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "routing rule not found")
    if payload.store_id is not None:
        if await session.get(Store, payload.store_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "store not found")
        rule.store_id = payload.store_id
    if payload.name is not None:
        rule.name = standardize_name(payload.name)
    if payload.behavior is not None:
        rule.behavior = payload.behavior
    if payload.priority is not None:
        rule.priority = payload.priority
    if payload.enabled is not None:
        rule.enabled = payload.enabled
    await session.flush()
    await apply_default_stores_for_rule(session, rule)
    return await _rule_payload(session, rule)


@router.delete("/store-routing-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_routing_rule(rule_id: UUID, session: SessionDep) -> Response:
    rule = await session.get(StoreRoutingRule, rule_id, with_for_update=True)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "routing rule not found")
    await session.delete(rule)
    await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/products/{barcode}/store-assignment")
async def product_store_assignment(
    barcode: str, session: SessionDep, settings: SettingsDep
) -> dict:
    await require_product(session, barcode)
    prefs = await household_settings(session)
    snapshot = await load_snapshot(session, settings, prefs.expiring_window_days)
    summary = snapshot.summaries[barcode]
    context = await load_routing_context(session, [barcode])
    assignment = resolve_store_assignment(
        barcode=barcode,
        preferred_store_id=None,
        store_prices=summary.store_prices,
        stores=snapshot.stores,
        context=context,
    )
    return {"barcode": barcode, "assignment": assignment.payload(snapshot.stores)}
