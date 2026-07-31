from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from difflib import SequenceMatcher
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from larder.domain.names import standardize_name
from larder.models import (
    GroceryListItem,
    Product,
    ProductCategory,
    ProductStockPolicy,
    StockGroup,
    StockGroupCandidate,
    StockGroupPolicy,
    StockGroupProduct,
    StockGroupShoppingItem,
)

FORM_TERMS = {
    "fresh",
    "frozen",
    "dried",
    "canned",
    "jarred",
    "powdered",
    "liquid",
    "whole",
    "sliced",
    "chopped",
    "shredded",
    "ground",
    "decaf",
    "regular",
    "unsweetened",
    "sweetened",
    "scented",
    "unscented",
    "disposable",
    "reusable",
}

MARKETING_TERMS = {
    "organic",
    "premium",
    "classic",
    "natural",
    "original",
    "signature",
    "select",
    "quality",
    "value",
    "family",
    "size",
}

PACKAGE_TERMS = {
    "pack",
    "package",
    "count",
    "ct",
    "oz",
    "ounce",
    "ounces",
    "lb",
    "lbs",
    "pound",
    "pounds",
    "g",
    "gram",
    "grams",
    "kg",
    "ml",
    "liter",
    "liters",
    "fl",
}

FRESH_CATEGORY_TERMS = {"produce", "fruit", "vegetable", "berry"}
NON_INFORMATIVE_CATEGORIES = {"local:uncategorized"}


def _ascii(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return normalized.encode("ascii", "ignore").decode().casefold()


def _tokens(value: str | None) -> list[str]:
    return re.findall(r"[a-z0-9]+", _ascii(value))


def _singular(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 4 and token.endswith("oes"):
        return token[:-2]
    if len(token) > 4 and token.endswith("ses") and not token.endswith("sses"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us")):
        return token[:-1]
    return token


def _brand_tokens(product: Product) -> set[str]:
    return set(_tokens(" ".join(filter(None, [product.brand, product.manufacturer]))))


def _category_terms(category_ids: set[str]) -> set[str]:
    terms: set[str] = set()
    for category_id in category_ids:
        terms.update(
            _singular(token)
            for token in _tokens(category_id.split(":", 1)[-1].replace("-", " "))
        )
    return terms


@dataclass(frozen=True)
class ProductMatchProfile:
    barcode: str
    name: str
    product_kind: str
    canonical_key: str
    terms: frozenset[str]
    forms: frozenset[str]
    category_ids: frozenset[str]


@dataclass(frozen=True)
class MatchScore:
    confidence: float
    evidence: dict


@dataclass(frozen=True)
class MatchOutcome:
    status: str
    stock_group_id: UUID | None = None
    confidence: float | None = None


def profile_from_product(product: Product, category_ids: set[str]) -> ProductMatchProfile | None:
    if not product.name:
        return None
    brands = _brand_tokens(product)
    package_tokens = set(_tokens(product.package_size or product.quantity_text))
    raw = _tokens(product.name)
    forms = {token for token in raw if token in FORM_TERMS}
    category_terms = _category_terms(category_ids)
    forms.update(category_terms & FORM_TERMS)
    if (
        product.product_kind == "FOOD"
        and category_terms & FRESH_CATEGORY_TERMS
        and not forms & {"frozen", "dried", "canned", "jarred"}
    ):
        forms.add("fresh")
    identity = []
    for token in raw:
        if (
            token in brands
            or token in package_tokens
            or token in MARKETING_TERMS
            or token in PACKAGE_TERMS
            or token in FORM_TERMS
            or token.isdigit()
        ):
            continue
        identity.append(_singular(token))
    if not identity:
        return None
    terms = frozenset(identity)
    return ProductMatchProfile(
        barcode=product.barcode,
        name=product.name,
        product_kind=product.product_kind,
        canonical_key=" ".join(sorted(terms)),
        terms=terms,
        forms=frozenset(forms),
        category_ids=frozenset(category_ids),
    )


async def product_profile(
    session: AsyncSession, product: Product
) -> ProductMatchProfile | None:
    categories = set(
        await session.scalars(
            select(ProductCategory.category_id).where(
                ProductCategory.product_barcode == product.barcode
            )
        )
    ) - NON_INFORMATIVE_CATEGORIES
    return profile_from_product(product, categories)


def generic_group_name(product: Product) -> str:
    brands = _brand_tokens(product)
    package_tokens = set(_tokens(product.package_size or product.quantity_text))
    kept = [
        token
        for token in _tokens(product.name)
        if token not in brands
        and token not in package_tokens
        and token not in MARKETING_TERMS
        and token not in PACKAGE_TERMS
        and not token.isdigit()
    ]
    return standardize_name(" ".join(kept)) if kept else standardize_name(product.name)


def score_profile(group: StockGroup, profile: ProductMatchProfile) -> MatchScore:
    evidence: dict = {
        "requiredTerms": list(group.required_terms or []),
        "productTerms": sorted(profile.terms),
        "productForms": sorted(profile.forms),
        "categoryOverlap": [],
    }
    if profile.product_kind != group.product_kind:
        return MatchScore(0, {**evidence, "rejectedBy": "product_kind"})
    excluded = set(group.excluded_terms or [])
    blocked = sorted((profile.terms | profile.forms) & excluded)
    if blocked:
        return MatchScore(
            0, {**evidence, "rejectedBy": "excluded_terms", "blockedTerms": blocked}
        )

    required = set(group.required_terms or _tokens(group.canonical_key))
    intersection = required & set(profile.terms)
    coverage = len(intersection) / len(required) if required else 0
    precision = len(intersection) / len(profile.terms) if profile.terms else 0
    lexical = (coverage * 0.65) + (precision * 0.35)
    sequence = SequenceMatcher(None, group.canonical_key, profile.canonical_key).ratio()
    name_score = max(lexical, sequence * 0.9)

    group_categories = set(group.category_ids or [])
    overlap = sorted(group_categories & set(profile.category_ids))
    group_category_terms = _category_terms(group_categories)
    product_category_terms = _category_terms(set(profile.category_ids))
    category_term_overlap = sorted(
        group_category_terms & product_category_terms
    )
    evidence.update(
        {
            "requiredCoverage": round(coverage, 4),
            "termPrecision": round(precision, 4),
            "nameSimilarity": round(name_score, 4),
            "categoryOverlap": overlap,
            "categoryTermOverlap": category_term_overlap,
        }
    )
    if overlap:
        category_score = 1.0
    elif category_term_overlap:
        category_score = 0.85
    elif not group_categories or not profile.category_ids:
        category_score = 0.5
    else:
        category_score = 0.0

    allowed_forms = set(group.allowed_forms or [])
    form_conflict = bool(
        allowed_forms and profile.forms and not (allowed_forms & set(profile.forms))
    )
    form_score = (
        1.0
        if not allowed_forms or allowed_forms & set(profile.forms)
        else 0.5
        if not profile.forms
        else 0.0
    )
    evidence["formConflict"] = form_conflict
    confidence = (name_score * 0.74) + (category_score * 0.16) + (form_score * 0.05) + 0.05
    if group.canonical_key == profile.canonical_key:
        # Exact generic identities should still auto-match when a provider did
        # not return categories. Category/form conflicts below remain hard caps.
        confidence = max(confidence + 0.08, 0.95)
    if (
        group_categories
        and profile.category_ids
        and not overlap
        and not category_term_overlap
    ):
        evidence["categoryConflict"] = True
        confidence = min(confidence, 0.64)
    if form_conflict:
        confidence = min(confidence, 0.79)
    return MatchScore(max(0, min(round(confidence, 4), 1)), evidence)


async def _migrate_product_demand(
    session: AsyncSession, barcode: str, group_id: UUID, *, migrate_policy: bool
) -> None:
    product_row = await session.get(GroceryListItem, barcode, with_for_update=True)
    group_row = await session.get(StockGroupShoppingItem, group_id, with_for_update=True)
    if product_row is not None:
        if group_row is None:
            group_row = StockGroupShoppingItem(
                stock_group_id=group_id,
                automatic_quantity=0,
                manual_quantity=0,
                checked_quantity=0,
            )
            session.add(group_row)
        group_row.manual_quantity += product_row.manual_quantity
        group_row.checked_quantity += product_row.checked_quantity
        if group_row.preferred_store_id is None:
            group_row.preferred_store_id = product_row.preferred_store_id
        await session.delete(product_row)

    if migrate_policy:
        product_policy = await session.get(ProductStockPolicy, barcode, with_for_update=True)
        if product_policy is not None:
            group_policy = await session.get(StockGroupPolicy, group_id, with_for_update=True)
            if group_policy is None:
                session.add(
                    StockGroupPolicy(
                        stock_group_id=group_id,
                        reorder_point=product_policy.reorder_point,
                        target_stock=product_policy.target_stock,
                        enabled=product_policy.enabled,
                    )
                )
            await session.delete(product_policy)


async def accept_product_match(
    session: AsyncSession,
    group: StockGroup,
    product: Product,
    *,
    source: str,
    confidence: float | None,
    evidence: dict | None = None,
    learn: bool = True,
    migrate_policy: bool = True,
) -> StockGroupProduct:
    previous_group_id: UUID | None = None
    existing = await session.scalar(
        select(StockGroupProduct)
        .where(StockGroupProduct.product_barcode == product.barcode)
        .with_for_update()
    )
    if existing is not None and existing.stock_group_id != group.id:
        previous_group_id = existing.stock_group_id
        await session.delete(existing)
        await session.flush()
        previous_group = await session.get(StockGroup, previous_group_id)
        if (
            previous_group is not None
            and previous_group.representative_product_barcode == product.barcode
        ):
            previous_group.representative_product_barcode = await session.scalar(
                select(StockGroupProduct.product_barcode)
                .where(StockGroupProduct.stock_group_id == previous_group_id)
                .order_by(StockGroupProduct.created_at, StockGroupProduct.product_barcode)
                .limit(1)
            )
        existing = None
    if existing is None:
        existing = StockGroupProduct(
            stock_group_id=group.id,
            product_barcode=product.barcode,
            source=source,
            confidence=Decimal(str(confidence)) if confidence is not None else None,
            evidence=evidence or {},
        )
        session.add(existing)
    else:
        existing.source = source
        existing.confidence = Decimal(str(confidence)) if confidence is not None else None
        existing.evidence = evidence or {}

    profile = await product_profile(session, product)
    if learn and profile is not None:
        if profile.forms:
            group.allowed_forms = sorted(set(group.allowed_forms or []) | set(profile.forms))
        group.category_ids = sorted(set(group.category_ids or []) | set(profile.category_ids))
    await session.execute(
        delete(StockGroupCandidate).where(
            StockGroupCandidate.product_barcode == product.barcode
        )
    )
    await _migrate_product_demand(
        session, product.barcode, group.id, migrate_policy=migrate_policy
    )
    await session.flush()
    from larder.services.stock_policy import recalculate_stock_group_policy

    await recalculate_stock_group_policy(session, group.id)
    if previous_group_id is not None:
        await recalculate_stock_group_policy(session, previous_group_id)
    return existing


async def reject_product_match(
    session: AsyncSession,
    group: StockGroup,
    product: Product,
    *,
    learn: bool = True,
) -> StockGroupCandidate:
    profile = await product_profile(session, product)
    score = score_profile(group, profile) if profile is not None else MatchScore(0, {})
    candidate = await session.get(
        StockGroupCandidate, (group.id, product.barcode), with_for_update=True
    )
    if candidate is None:
        candidate = StockGroupCandidate(
            stock_group_id=group.id,
            product_barcode=product.barcode,
            confidence=Decimal(str(score.confidence)),
            status="REJECTED",
            evidence=score.evidence,
            decided_at=datetime.now(UTC),
        )
        session.add(candidate)
    else:
        candidate.status = "REJECTED"
        candidate.decided_at = datetime.now(UTC)
    if learn and profile is not None:
        required = set(group.required_terms or [])
        allowed_forms = set(group.allowed_forms or [])
        learned = (set(profile.terms) - required) | (
            set(profile.forms) - allowed_forms
        )
        group.excluded_terms = sorted(set(group.excluded_terms or []) | learned)
    return candidate


async def create_stock_group_from_product(
    session: AsyncSession,
    product: Product,
    *,
    name: str | None = None,
    auto_match_threshold: float = 0.90,
    review_threshold: float = 0.65,
    migrate_policy: bool = True,
) -> tuple[StockGroup, bool]:
    profile = await product_profile(session, product)
    if profile is None:
        raise ValueError("product needs a name before a stock group can be created")
    existing_membership = await session.scalar(
        select(StockGroupProduct).where(
            StockGroupProduct.product_barcode == product.barcode
        )
    )
    if existing_membership is not None:
        existing = await session.get(StockGroup, existing_membership.stock_group_id)
        if existing is not None:
            return existing, False
    group = StockGroup(
        name=standardize_name(name) if name else generic_group_name(product),
        canonical_key=profile.canonical_key,
        product_kind=profile.product_kind,
        representative_product_barcode=product.barcode,
        required_terms=sorted(profile.terms),
        allowed_forms=sorted(profile.forms),
        category_ids=sorted(profile.category_ids),
        auto_match_threshold=Decimal(str(auto_match_threshold)),
        review_threshold=Decimal(str(review_threshold)),
        created_source="USER",
        active=True,
    )
    session.add(group)
    await session.flush()
    await accept_product_match(
        session,
        group,
        product,
        source="USER",
        confidence=1,
        evidence={"seedProduct": True},
        migrate_policy=migrate_policy,
    )
    await auto_match_all_products(session, stock_group_id=group.id)
    return group, True


async def match_product_to_stock_groups(
    session: AsyncSession,
    product: Product,
    *,
    stock_group_id: UUID | None = None,
) -> MatchOutcome:
    membership = await session.scalar(
        select(StockGroupProduct).where(
            StockGroupProduct.product_barcode == product.barcode
        )
    )
    if membership is not None:
        return MatchOutcome("MATCHED", membership.stock_group_id, 1)
    profile = await product_profile(session, product)
    if profile is None:
        return MatchOutcome("NO_PROFILE")
    group_statement = select(StockGroup).where(
        StockGroup.active.is_(True),
        StockGroup.product_kind == profile.product_kind,
    )
    if stock_group_id is not None:
        group_statement = group_statement.where(StockGroup.id == stock_group_id)
    groups = (await session.scalars(group_statement)).all()
    rejected = set(
        await session.scalars(
            select(StockGroupCandidate.stock_group_id).where(
                StockGroupCandidate.product_barcode == product.barcode,
                StockGroupCandidate.status == "REJECTED",
            )
        )
    )
    scored = sorted(
        (
            (group, score_profile(group, profile))
            for group in groups
            if group.id not in rejected
        ),
        key=lambda item: item[1].confidence,
        reverse=True,
    )
    if not scored:
        return MatchOutcome("NO_MATCH")
    group, score = scored[0]
    second = scored[1][1].confidence if len(scored) > 1 else 0
    exact_policy = await session.get(ProductStockPolicy, product.barcode)
    unambiguous = score.confidence - second >= 0.05
    if (
        score.confidence >= float(group.auto_match_threshold)
        and unambiguous
        and exact_policy is None
    ):
        await accept_product_match(
            session,
            group,
            product,
            source="AUTOMATIC",
            confidence=score.confidence,
            evidence=score.evidence,
            learn=False,
            migrate_policy=False,
        )
        return MatchOutcome("MATCHED", group.id, score.confidence)
    if score.confidence >= float(group.review_threshold):
        candidate = await session.get(
            StockGroupCandidate, (group.id, product.barcode), with_for_update=True
        )
        if candidate is None:
            session.add(
                StockGroupCandidate(
                    stock_group_id=group.id,
                    product_barcode=product.barcode,
                    confidence=Decimal(str(score.confidence)),
                    status="PENDING",
                    evidence=score.evidence,
                )
            )
        elif candidate.status != "REJECTED":
            candidate.confidence = Decimal(str(score.confidence))
            candidate.evidence = score.evidence
            candidate.evaluated_at = datetime.now(UTC)
        return MatchOutcome("REVIEW", group.id, score.confidence)
    return MatchOutcome("NO_MATCH", group.id, score.confidence)


async def auto_match_all_products(
    session: AsyncSession, *, stock_group_id: UUID | None = None
) -> dict[str, int]:
    statement = select(Product).where(Product.archived_at.is_(None))
    if stock_group_id is not None:
        group = await session.get(StockGroup, stock_group_id)
        if group is None:
            return {"evaluated": 0, "matched": 0, "review": 0, "unmatched": 0}
        statement = statement.where(Product.product_kind == group.product_kind)
    products = (await session.scalars(statement)).all()
    counts = {"evaluated": 0, "matched": 0, "review": 0, "unmatched": 0}
    for product in products:
        membership = await session.scalar(
            select(StockGroupProduct).where(
                StockGroupProduct.product_barcode == product.barcode
            )
        )
        if membership is not None:
            continue
        outcome = await match_product_to_stock_groups(
            session, product, stock_group_id=stock_group_id
        )
        counts["evaluated"] += 1
        if outcome.status == "MATCHED":
            counts["matched"] += 1
        elif outcome.status == "REVIEW":
            counts["review"] += 1
        else:
            counts["unmatched"] += 1
    return counts
