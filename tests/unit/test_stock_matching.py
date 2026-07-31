from decimal import Decimal

from larder.models import Product, StockGroup
from larder.services.stock_matching import (
    generic_group_name,
    profile_from_product,
    score_profile,
)


def product(
    barcode: str,
    name: str,
    *,
    brand: str | None = None,
    package_size: str | None = None,
) -> Product:
    return Product(
        barcode=barcode,
        barcode_format="GTIN_13",
        name=name,
        brand=brand,
        package_size=package_size,
        product_kind="FOOD",
    )


def strawberry_group() -> StockGroup:
    return StockGroup(
        name="Strawberries",
        canonical_key="strawberry",
        product_kind="FOOD",
        required_terms=["strawberry"],
        excluded_terms=[],
        allowed_forms=["fresh"],
        category_ids=["en:berries", "en:fruits", "en:strawberries"],
        auto_match_threshold=Decimal("0.90"),
        review_threshold=Decimal("0.65"),
    )


def test_brand_size_and_plural_are_removed_from_generic_identity():
    seed = product(
        "0715756200023",
        "Driscoll's Organic Strawberries 16 Oz",
        brand="Driscoll's",
        package_size="16 Oz",
    )
    profile = profile_from_product(seed, {"en:fruits", "en:berries"})

    assert profile is not None
    assert profile.canonical_key == "strawberry"
    assert profile.forms == frozenset({"fresh"})
    assert generic_group_name(seed) == "Strawberries"


def test_other_brand_of_strawberries_auto_matches():
    candidate = product(
        "0000000000001",
        "Wegmans Organic Strawberries",
        brand="Wegmans",
        package_size="32 Oz",
    )
    profile = profile_from_product(
        candidate, {"en:fruits", "en:berries", "en:strawberries"}
    )

    assert profile is not None
    assert score_profile(strawberry_group(), profile).confidence >= 0.90


def test_strawberry_yogurt_is_not_suggested():
    candidate = product(
        "0000000000002",
        "Strawberry Greek Yogurt",
        brand="Store Brand",
    )
    profile = profile_from_product(candidate, {"en:dairy", "en:yogurts"})

    assert profile is not None
    score = score_profile(strawberry_group(), profile)
    assert score.confidence < 0.65
    assert score.evidence["categoryConflict"] is True


def test_frozen_strawberries_are_sent_to_review_not_auto_accepted():
    candidate = product("0000000000003", "Frozen Strawberries")
    profile = profile_from_product(
        candidate, {"en:fruits", "en:frozen-fruits"}
    )

    assert profile is not None
    score = score_profile(strawberry_group(), profile)
    assert 0.65 <= score.confidence < 0.90
    assert score.evidence["formConflict"] is True


def test_exact_generic_name_can_match_without_provider_categories():
    candidate = product("0000000000004", "Strawberries")
    profile = profile_from_product(candidate, set())

    assert profile is not None
    assert score_profile(strawberry_group(), profile).confidence >= 0.90


def test_semantic_category_names_match_across_catalog_providers():
    candidate = product("0000000000005", "Strawberries")
    profile = profile_from_product(candidate, {"upcitemdb:fruits"})

    assert profile is not None
    score = score_profile(strawberry_group(), profile)
    assert score.confidence >= 0.90
    assert score.evidence["categoryTermOverlap"] == ["fruit"]
    assert "categoryConflict" not in score.evidence
