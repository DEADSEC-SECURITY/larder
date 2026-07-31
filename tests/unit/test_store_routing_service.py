from uuid import UUID

from larder.models import Store, StoreRoutingRule
from larder.services.brands import normalize_brand_name, split_brand_names
from larder.services.store_routing import (
    RoutingContext,
    resolve_store_assignment,
)


def test_brand_names_are_normalized_and_split_without_duplicates():
    assert normalize_brand_name("  KIRKLAND-Signature™  ") == "kirkland signature"
    assert split_brand_names("Kirkland Signature, kirkland-signature; Costco") == [
        "Kirkland Signature",
        "Costco",
    ]


def test_store_assignment_uses_specificity_before_priority_and_manual_first():
    costco_id = UUID("10000000-0000-0000-0000-000000000001")
    wegmans_id = UUID("10000000-0000-0000-0000-000000000002")
    brand_id = UUID("20000000-0000-0000-0000-000000000001")
    stores = {
        costco_id: Store(id=costco_id, code="COSTCO", name="Costco", active=True),
        wegmans_id: Store(id=wegmans_id, code="WEGMANS", name="Wegmans", active=True),
    }
    brand_rule = StoreRoutingRule(
        id=UUID("30000000-0000-0000-0000-000000000001"),
        name="Kirkland to Costco",
        match_type="BRAND",
        brand_id=brand_id,
        store_id=costco_id,
        behavior="REQUIRE",
        priority=100,
        enabled=True,
    )
    product_rule = StoreRoutingRule(
        id=UUID("30000000-0000-0000-0000-000000000002"),
        name="One product to Wegmans",
        match_type="PRODUCT",
        product_barcode="123",
        store_id=wegmans_id,
        behavior="PREFER",
        priority=-100,
        enabled=True,
    )
    context = RoutingContext(
        rules=[brand_rule, product_rule],
        product_brands={"123": {brand_id}},
        brand_names={brand_id: "Kirkland Signature"},
    )

    assignment = resolve_store_assignment(
        barcode="123",
        preferred_store_id=None,
        store_prices={costco_id: 5.0, wegmans_id: 6.0},
        stores=stores,
        context=context,
    )
    assert assignment.store_id == wegmans_id
    assert assignment.source == "product_rule"

    manual = resolve_store_assignment(
        barcode="123",
        preferred_store_id=costco_id,
        store_prices={},
        stores=stores,
        context=context,
    )
    assert manual.store_id == costco_id
    assert manual.source == "manual"


def test_inactive_preference_falls_back_but_inactive_requirement_is_explained():
    store_id = UUID("10000000-0000-0000-0000-000000000001")
    fallback_id = UUID("10000000-0000-0000-0000-000000000002")
    stores = {
        store_id: Store(id=store_id, code="COSTCO", name="Costco", active=False),
        fallback_id: Store(id=fallback_id, code="WEGMANS", name="Wegmans", active=True),
    }
    rule = StoreRoutingRule(
        id=UUID("30000000-0000-0000-0000-000000000001"),
        name="Product preference",
        match_type="PRODUCT",
        product_barcode="123",
        store_id=store_id,
        behavior="PREFER",
        priority=0,
        enabled=True,
    )
    context = RoutingContext(rules=[rule])
    preferred = resolve_store_assignment(
        barcode="123",
        preferred_store_id=None,
        store_prices={fallback_id: 4.0},
        stores=stores,
        context=context,
    )
    assert preferred.store_id == fallback_id
    assert preferred.source == "price_history"

    rule.behavior = "REQUIRE"
    required = resolve_store_assignment(
        barcode="123",
        preferred_store_id=None,
        store_prices={fallback_id: 4.0},
        stores=stores,
        context=context,
    )
    assert required.store_id == store_id
    assert required.warning == "Required store is inactive"
