from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from larder.domain.names import standardize_name
from larder.models import (
    InventoryItem,
    Product,
    StockGroup,
    StockGroupCandidate,
    StockGroupPolicy,
    StockGroupProduct,
)
from larder.models.enums import InventoryStatus
from larder.services.stock_matching import (
    accept_product_match,
    auto_match_all_products,
    create_stock_group_from_product,
    match_product_to_stock_groups,
    product_profile,
    reject_product_match,
    score_profile,
)
from larder.services.stock_policy import recalculate_stock_group_policy
from larder.webapi.common import get_session, web_api_router

router = web_api_router(tags=["stock-groups"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class StockGroupCreateRequest(CamelModel):
    seed_barcode: str
    name: str | None = Field(default=None, min_length=1, max_length=200)
    auto_match_threshold: float = Field(default=0.90, ge=0, le=1)
    review_threshold: float = Field(default=0.65, ge=0, le=1)
    reorder_point: int | None = Field(default=None, ge=0, le=999)
    target_stock: int | None = Field(default=None, ge=0, le=999)

    @model_validator(mode="after")
    def validate_thresholds_and_policy(self) -> StockGroupCreateRequest:
        if self.auto_match_threshold < self.review_threshold:
            raise ValueError("autoMatchThreshold must be at least reviewThreshold")
        if (self.reorder_point is None) != (self.target_stock is None):
            raise ValueError("reorderPoint and targetStock must be provided together")
        if (
            self.reorder_point is not None
            and self.target_stock is not None
            and self.target_stock < self.reorder_point
        ):
            raise ValueError("targetStock must be at least reorderPoint")
        return self


class StockGroupUpdateRequest(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    required_terms: list[str] | None = Field(default=None, max_length=30)
    excluded_terms: list[str] | None = Field(default=None, max_length=30)
    allowed_forms: list[str] | None = Field(default=None, max_length=20)
    category_ids: list[str] | None = Field(default=None, max_length=100)
    auto_match_threshold: float | None = Field(default=None, ge=0, le=1)
    review_threshold: float | None = Field(default=None, ge=0, le=1)
    active: bool | None = None


class MemberRequest(CamelModel):
    barcode: str
    learn: bool = True
    migrate_policy: bool = True


class CandidateDecisionRequest(CamelModel):
    accepted: bool
    learn: bool = True
    migrate_policy: bool = True


class StockGroupPolicyRequest(CamelModel):
    reorder_point: int = Field(ge=0, le=999)
    target_stock: int = Field(ge=0, le=999)

    @model_validator(mode="after")
    def target_covers_reorder(self) -> StockGroupPolicyRequest:
        if self.target_stock < self.reorder_point:
            raise ValueError("targetStock must be at least reorderPoint")
        return self


async def _require_group(
    session: AsyncSession, group_id: UUID, *, for_update: bool = False
) -> StockGroup:
    group = await session.get(StockGroup, group_id, with_for_update=for_update)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "stock group not found")
    return group


async def _group_counts(session: AsyncSession, group_id: UUID) -> tuple[int, int, int]:
    members = await session.scalar(
        select(func.count())
        .select_from(StockGroupProduct)
        .where(StockGroupProduct.stock_group_id == group_id)
    )
    on_hand = await session.scalar(
        select(func.count())
        .select_from(InventoryItem)
        .join(
            StockGroupProduct,
            StockGroupProduct.product_barcode == InventoryItem.product_barcode,
        )
        .where(
            StockGroupProduct.stock_group_id == group_id,
            InventoryItem.status == InventoryStatus.IN_STOCK,
        )
    )
    pending = await session.scalar(
        select(func.count())
        .select_from(StockGroupCandidate)
        .where(
            StockGroupCandidate.stock_group_id == group_id,
            StockGroupCandidate.status == "PENDING",
        )
    )
    return members or 0, on_hand or 0, pending or 0


async def _group_payload(session: AsyncSession, group: StockGroup) -> dict:
    member_count, on_hand, pending = await _group_counts(session, group.id)
    policy = await session.get(StockGroupPolicy, group.id)
    return {
        "id": str(group.id),
        "name": group.name,
        "canonicalKey": group.canonical_key,
        "productKind": group.product_kind.lower(),
        "representativeProductBarcode": group.representative_product_barcode,
        "requiredTerms": group.required_terms,
        "excludedTerms": group.excluded_terms,
        "allowedForms": group.allowed_forms,
        "categoryIds": group.category_ids,
        "ignoreBrand": group.ignore_brand,
        "ignorePackageSize": group.ignore_package_size,
        "autoMatchThreshold": float(group.auto_match_threshold),
        "reviewThreshold": float(group.review_threshold),
        "active": group.active,
        "memberCount": member_count,
        "inStock": on_hand,
        "pendingCandidateCount": pending,
        "policy": None
        if policy is None or not policy.enabled
        else {
            "reorderPoint": policy.reorder_point,
            "targetStock": policy.target_stock,
        },
    }


@router.post("/stock-groups/auto-match")
async def auto_match(session: SessionDep) -> dict:
    return await auto_match_all_products(session)


@router.get("/stock-groups")
async def list_stock_groups(
    session: SessionDep, include_archived: bool = Query(default=False)
) -> dict:
    statement = select(StockGroup).order_by(StockGroup.name, StockGroup.id)
    if not include_archived:
        statement = statement.where(StockGroup.active.is_(True))
    groups = (await session.scalars(statement)).all()
    return {"stockGroups": [await _group_payload(session, group) for group in groups]}


@router.post("/stock-groups", status_code=status.HTTP_201_CREATED)
async def create_stock_group(
    payload: StockGroupCreateRequest, session: SessionDep
) -> dict:
    product = await session.get(Product, payload.seed_barcode)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "seed product not found")
    try:
        group, created = await create_stock_group_from_product(
            session,
            product,
            name=payload.name,
            auto_match_threshold=payload.auto_match_threshold,
            review_threshold=payload.review_threshold,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if payload.reorder_point is not None and payload.target_stock is not None:
        policy = await session.get(StockGroupPolicy, group.id, with_for_update=True)
        if policy is None:
            policy = StockGroupPolicy(
                stock_group_id=group.id,
                reorder_point=payload.reorder_point,
                target_stock=payload.target_stock,
                enabled=True,
            )
            session.add(policy)
        else:
            policy.reorder_point = payload.reorder_point
            policy.target_stock = payload.target_stock
            policy.enabled = True
        await session.flush()
        await recalculate_stock_group_policy(session, group.id)
    return {"created": created, "stockGroup": await _group_payload(session, group)}


@router.get("/stock-groups/{group_id}")
async def stock_group_detail(group_id: UUID, session: SessionDep) -> dict:
    group = await _require_group(session, group_id)
    memberships = (
        await session.execute(
            select(StockGroupProduct, Product)
            .join(Product, Product.barcode == StockGroupProduct.product_barcode)
            .where(StockGroupProduct.stock_group_id == group_id)
            .order_by(Product.name, Product.barcode)
        )
    ).all()
    candidates = (
        await session.execute(
            select(StockGroupCandidate, Product)
            .join(Product, Product.barcode == StockGroupCandidate.product_barcode)
            .where(StockGroupCandidate.stock_group_id == group_id)
            .order_by(
                StockGroupCandidate.status,
                StockGroupCandidate.confidence.desc(),
                Product.name,
            )
        )
    ).all()
    return {
        **(await _group_payload(session, group)),
        "members": [
            {
                "barcode": product.barcode,
                "name": product.name,
                "brand": product.brand,
                "imageUrl": product.image_url,
                "packageSize": product.package_size,
                "productKind": product.product_kind.lower(),
                "source": member.source.lower(),
                "confidence": float(member.confidence)
                if member.confidence is not None
                else None,
                "evidence": member.evidence,
            }
            for member, product in memberships
        ],
        "candidates": [
            {
                "barcode": product.barcode,
                "name": product.name,
                "brand": product.brand,
                "imageUrl": product.image_url,
                "packageSize": product.package_size,
                "productKind": product.product_kind.lower(),
                "confidence": float(candidate.confidence),
                "status": candidate.status.lower(),
                "evidence": candidate.evidence,
            }
            for candidate, product in candidates
        ],
    }


@router.patch("/stock-groups/{group_id}")
async def update_stock_group(
    group_id: UUID, payload: StockGroupUpdateRequest, session: SessionDep
) -> dict:
    group = await _require_group(session, group_id, for_update=True)
    fields = payload.model_fields_set
    if payload.name is not None:
        group.name = standardize_name(payload.name)
    for field_name in (
        "required_terms",
        "excluded_terms",
        "allowed_forms",
        "category_ids",
    ):
        if field_name in fields:
            values = getattr(payload, field_name) or []
            normalized = {
                value.casefold().strip() for value in values if value.strip()
            }
            setattr(group, field_name, sorted(normalized))
    for field_name in ("active",):
        if field_name in fields:
            setattr(group, field_name, getattr(payload, field_name))
    auto_threshold = (
        payload.auto_match_threshold
        if "auto_match_threshold" in fields
        else float(group.auto_match_threshold)
    )
    review_threshold = (
        payload.review_threshold
        if "review_threshold" in fields
        else float(group.review_threshold)
    )
    if auto_threshold < review_threshold:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "autoMatchThreshold must be at least reviewThreshold",
        )
    group.auto_match_threshold = auto_threshold
    group.review_threshold = review_threshold
    await session.flush()
    await auto_match_all_products(session, stock_group_id=group.id)
    await recalculate_stock_group_policy(session, group.id)
    return await _group_payload(session, group)


@router.post("/stock-groups/{group_id}/members")
async def add_stock_group_member(
    group_id: UUID, payload: MemberRequest, session: SessionDep
) -> dict:
    group = await _require_group(session, group_id, for_update=True)
    product = await session.get(Product, payload.barcode)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product not found")
    await accept_product_match(
        session,
        group,
        product,
        source="USER",
        confidence=1,
        evidence={"manual": True},
        learn=payload.learn,
        migrate_policy=payload.migrate_policy,
    )
    return await _group_payload(session, group)


@router.delete("/stock-groups/{group_id}/members/{barcode}")
async def remove_stock_group_member(
    group_id: UUID,
    barcode: str,
    session: SessionDep,
    remember: bool = Query(default=True),
) -> dict:
    group = await _require_group(session, group_id, for_update=True)
    member = await session.get(StockGroupProduct, (group_id, barcode))
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "stock group member not found")
    product = await session.get(Product, barcode)
    await session.delete(member)
    await session.flush()
    if group.representative_product_barcode == barcode:
        group.representative_product_barcode = await session.scalar(
            select(StockGroupProduct.product_barcode)
            .where(StockGroupProduct.stock_group_id == group_id)
            .order_by(StockGroupProduct.created_at, StockGroupProduct.product_barcode)
            .limit(1)
        )
    if remember and product is not None:
        await reject_product_match(session, group, product, learn=False)
    await recalculate_stock_group_policy(session, group_id)
    return await _group_payload(session, group)


@router.post("/stock-groups/{group_id}/candidates/{barcode}/decision")
async def decide_candidate(
    group_id: UUID,
    barcode: str,
    payload: CandidateDecisionRequest,
    session: SessionDep,
) -> dict:
    group = await _require_group(session, group_id, for_update=True)
    product = await session.get(Product, barcode)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product not found")
    if payload.accepted:
        profile = await product_profile(session, product)
        score = score_profile(group, profile) if profile else None
        await accept_product_match(
            session,
            group,
            product,
            source="USER",
            confidence=score.confidence if score else None,
            evidence=score.evidence if score else {"manual": True},
            learn=payload.learn,
            migrate_policy=payload.migrate_policy,
        )
    else:
        await reject_product_match(session, group, product, learn=payload.learn)
    await session.flush()
    return await stock_group_detail(group_id, session)


@router.put("/stock-groups/{group_id}/stock-policy")
async def set_stock_group_policy(
    group_id: UUID, payload: StockGroupPolicyRequest, session: SessionDep
) -> dict:
    await _require_group(session, group_id, for_update=True)
    policy = await session.get(StockGroupPolicy, group_id, with_for_update=True)
    if payload.reorder_point == 0 and payload.target_stock == 0:
        if policy is not None:
            await session.delete(policy)
            await session.flush()
        await recalculate_stock_group_policy(session, group_id)
        return {"stockGroupId": str(group_id), "policy": None}
    if policy is None:
        policy = StockGroupPolicy(
            stock_group_id=group_id,
            reorder_point=payload.reorder_point,
            target_stock=payload.target_stock,
            enabled=True,
        )
        session.add(policy)
    else:
        policy.reorder_point = payload.reorder_point
        policy.target_stock = payload.target_stock
        policy.enabled = True
    await session.flush()
    await recalculate_stock_group_policy(session, group_id)
    return {
        "stockGroupId": str(group_id),
        "policy": {
            "reorderPoint": policy.reorder_point,
            "targetStock": policy.target_stock,
        },
    }


async def _product_match_payload(session: AsyncSession, product: Product) -> dict:
    barcode = product.barcode
    membership = await session.scalar(
        select(StockGroupProduct).where(StockGroupProduct.product_barcode == barcode)
    )
    if membership is not None:
        group = await session.get(StockGroup, membership.stock_group_id)
        return {
            "status": "matched",
            "stockGroup": await _group_payload(session, group) if group else None,
            "suggestions": [],
        }
    candidates = (
        await session.execute(
            select(StockGroupCandidate, StockGroup)
            .join(StockGroup, StockGroup.id == StockGroupCandidate.stock_group_id)
            .where(
                StockGroupCandidate.product_barcode == barcode,
                StockGroupCandidate.status == "PENDING",
            )
            .order_by(StockGroupCandidate.confidence.desc())
        )
    ).all()
    return {
        "status": "review" if candidates else "none",
        "suggestions": [
            {
                "stockGroupId": str(group.id),
                "stockGroupName": group.name,
                "confidence": float(candidate.confidence),
                "evidence": candidate.evidence,
            }
            for candidate, group in candidates
        ],
    }


@router.get("/products/{barcode}/stock-group-matches")
async def product_stock_group_matches(barcode: str, session: SessionDep) -> dict:
    product = await session.get(Product, barcode)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product not found")
    return await _product_match_payload(session, product)


@router.post("/products/{barcode}/stock-group-matches/refresh")
async def refresh_product_stock_group_matches(
    barcode: str, session: SessionDep
) -> dict:
    product = await session.get(Product, barcode)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product not found")
    outcome = await match_product_to_stock_groups(session, product)
    result = await _product_match_payload(session, product)
    result["evaluation"] = {
        "status": outcome.status.lower(),
        "stockGroupId": str(outcome.stock_group_id)
        if outcome.stock_group_id
        else None,
        "confidence": outcome.confidence,
    }
    return result
