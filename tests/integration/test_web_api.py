from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from larder.api import create_app
from larder.config import Settings
from larder.models import (
    ControlBarcode,
    ProductCategory,
    ProductNutrition,
    Scanner,
    ScannerContext,
)
from larder.webapi.common import MANUAL_BARCODE_PREFIX

pytestmark = pytest.mark.integration


def web_app(session_factory, database_url):
    settings = Settings(database_url=database_url, scan_api_token="test-token")
    app = create_app(settings)
    app.state.settings = settings
    app.state.session_factory = session_factory
    return app


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://web-test")


@pytest.mark.asyncio
async def test_tax_location_default_snapshots_subtotal_tax_and_total(session_factory, database_url):
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        location_response = await client.post(
            "/v1/web/tax-locations",
            json={
                "label": "Home Grocery Tax",
                "city": "Rochester",
                "state": "NY",
                "postalCode": "14604",
                "rates": [{"taxClass": "GROCERY", "rate": 0.08875}],
            },
        )
        assert location_response.status_code == 201, location_response.text
        location = location_response.json()
        rate_id = location["rates"][0]["id"]

        settings_response = await client.put("/v1/web/settings", json={"defaultTaxRateId": rate_id})
        assert settings_response.status_code == 200, settings_response.text
        assert settings_response.json()["defaultTaxRateId"] == rate_id

        created = await client.post(
            "/v1/web/quick-add",
            json={
                "name": "Taxed Test Item",
                "quantity": 1,
                "newStoreName": "Test Store",
                "priceTotal": 4.99,
            },
        )
        assert created.status_code == 201, created.text

        detail = (await client.get(f"/v1/web/products/{created.json()['barcode']}")).json()
        unit = detail["units"][0]
        assert unit["priceSubtotal"] == pytest.approx(4.99)
        assert unit["taxRate"] == pytest.approx(0.08875)
        assert unit["taxAmount"] == pytest.approx(0.44)
        assert unit["price"] == pytest.approx(5.43)
        assert unit["taxRateId"] == rate_id
        assert unit["taxLocationId"] == location["id"]
        assert detail["history"][0]["priceSubtotal"] == pytest.approx(4.99)
        assert detail["history"][0]["price"] == pytest.approx(5.43)


@pytest.mark.asyncio
async def test_quick_add_manual_product_full_flow(session_factory, database_url):
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        response = await client.post(
            "/v1/web/quick-add",
            json={
                "name": "gREEK YOGURT",
                "brand": "cHOBANI",
                "quantity": 2,
                "newStoreName": "wEGMANS",
                "priceTotal": 2.58,
                "expirationDate": "2026-07-24",
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["created"] == 2
        barcode = body["barcode"]
        assert barcode.startswith(MANUAL_BARCODE_PREFIX)

        inventory = (await client.get("/v1/web/inventory")).json()
        assert inventory["totalUnits"] == 2
        row = inventory["products"][0]
        assert row["name"] == "Greek Yogurt"
        assert row["brand"] == "Chobani"
        assert row["inStock"] == 2
        assert row["soonestExpiration"]["kind"] == "confirmed"
        assert row["lowestPrice"] == pytest.approx(1.29)

        stores = (await client.get("/v1/web/stores")).json()["stores"]
        assert len(stores) == 1
        assert stores[0]["name"] == "Wegmans"
        assert stores[0]["controlPayload"] == f"CMD:STORE:{stores[0]['code']}"
        assert stores[0]["priceCoveragePct"] == 100

        detail = (await client.get(f"/v1/web/products/{barcode}")).json()
        assert len(detail["units"]) == 2
        assert detail["priceStats"]["lowest"] == pytest.approx(1.29)
        assert detail["history"][0]["storeName"] == "Wegmans"

    async with session_factory() as session:
        control = await session.scalar(
            select(ControlBarcode).where(ControlBarcode.command_type == "SET_STORE")
        )
        assert control is not None


@pytest.mark.asyncio
async def test_stock_policy_drives_grocery_list(session_factory, database_url):
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        created = (
            await client.post("/v1/web/quick-add", json={"name": "Oat Milk", "quantity": 1})
        ).json()
        barcode = created["barcode"]

        response = await client.put(
            f"/v1/web/products/{barcode}/stock-policy",
            json={"reorderPoint": 2, "targetStock": 3},
        )
        assert response.status_code == 200, response.text

        grocery = (await client.get("/v1/web/grocery")).json()
        assert len(grocery["items"]) == 1
        item = grocery["items"][0]
        assert item["barcode"] == barcode
        assert item["quantity"] == 2  # target 3 - on hand 1
        assert "Below minimum" in item["reason"]

        # Checking the item, then clearing completed rows for good.
        checked = (await client.patch(f"/v1/web/grocery/{barcode}", json={"checked": True})).json()
        assert checked["checked"] is True
        cleared = await client.post("/v1/web/grocery/clear-checks")
        assert cleared.json()["removed"] == 1
        grocery = (await client.get("/v1/web/grocery")).json()
        assert grocery["items"] == []

        # Removing the policy empties the automatic part.
        await client.put(
            f"/v1/web/products/{barcode}/stock-policy",
            json={"reorderPoint": 0, "targetStock": 0},
        )
        grocery = (await client.get("/v1/web/grocery")).json()
        assert grocery["items"] == []


@pytest.mark.asyncio
async def test_unit_lifecycle_and_activity(session_factory, database_url):
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        created = (
            await client.post("/v1/web/quick-add", json={"name": "Sourdough Loaf", "quantity": 2})
        ).json()
        barcode = created["barcode"]
        first, second = created["itemIds"]

        updated = (
            await client.patch(f"/v1/web/units/{first}", json={"expirationDate": "2026-07-20"})
        ).json()
        assert updated["expirationKind"] == "confirmed"

        removed = (await client.post(f"/v1/web/units/{first}/remove")).json()
        assert removed["status"] == "removed"
        wasted = (await client.post(f"/v1/web/units/{second}/waste")).json()
        assert wasted["status"] == "trashed"

        conflict = await client.post(f"/v1/web/units/{first}/remove")
        assert conflict.status_code == 409

        detail = (await client.get(f"/v1/web/products/{barcode}")).json()
        assert detail["inStock"] == 0
        statuses = sorted(u["status"] for u in detail["units"])
        assert statuses == ["removed", "trashed"]

        restored = (
            await client.post(f"/v1/web/units/{first}/status", json={"status": "in_stock"})
        ).json()
        assert restored["status"] == "in_stock"
        corrected = (
            await client.post(f"/v1/web/units/{second}/status", json={"status": "removed"})
        ).json()
        assert corrected["status"] == "removed"

        detail = (await client.get(f"/v1/web/products/{barcode}")).json()
        assert detail["inStock"] == 1
        statuses = sorted(u["status"] for u in detail["units"])
        assert statuses == ["in_stock", "removed"]

        activity = (await client.get("/v1/web/activity")).json()["events"]
        kinds = [e["kind"] for e in activity]
        assert {"added", "removed", "wasted", "restored", "changed"} <= set(kinds)
        assert all(e["source"] == "admin" for e in activity)


@pytest.mark.asyncio
async def test_category_rules_and_estimates(session_factory, database_url):
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        category = (
            await client.post("/v1/web/categories", json={"name": "Dairy", "days": 7})
        ).json()
        assert category["id"] == "local:dairy"

        created = (await client.post("/v1/web/quick-add", json={"name": "Whole Milk"})).json()
        barcode = created["barcode"]

    # Link product to the category the way enrichment would.
    async with session_factory() as session, session.begin():
        session.add(
            ProductCategory(
                product_barcode=barcode,
                category_id="local:dairy",
                is_primary=True,
                source_rank=1,
            )
        )

    async with client_for(app) as client:
        recalc = (await client.post("/v1/web/maintenance/recalculate-estimates")).json()
        assert recalc["updated"] == 1

        detail = (await client.get(f"/v1/web/products/{barcode}")).json()
        unit = detail["units"][0]
        assert unit["expirationKind"] == "estimated"
        assert detail["rule"] == {
            "categoryId": "local:dairy",
            "categoryName": "Dairy",
            "days": 7,
        }

        # Clearing the rule and recalculating reverts the estimate.
        await client.patch("/v1/web/categories/local:dairy", json={"days": None})
        recalc = (await client.post("/v1/web/maintenance/recalculate-estimates")).json()
        assert recalc["updated"] == 1
        detail = (await client.get(f"/v1/web/products/{barcode}")).json()
        assert detail["units"][0]["expirationKind"] == "unknown"


@pytest.mark.asyncio
async def test_categories_return_nested_tree_and_reject_cycles(session_factory, database_url):
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        root = (await client.post("/v1/web/categories", json={"name": "Food", "days": 10})).json()
        child = (
            await client.post(
                "/v1/web/categories",
                json={"name": "Fruit", "parentId": root["id"]},
            )
        ).json()
        grandchild = (
            await client.post(
                "/v1/web/categories",
                json={"name": "Berries", "parentId": child["id"]},
            )
        ).json()

        response = await client.get("/v1/web/categories")
        assert response.status_code == 200
        body = response.json()
        flat = {row["id"]: row for row in body["categories"]}
        assert flat[child["id"]]["parentId"] == root["id"]
        assert flat[grandchild["id"]]["parentId"] == child["id"]
        assert flat[grandchild["id"]]["inheritedDays"] == 10

        tree_root = next(row for row in body["tree"] if row["id"] == root["id"])
        assert tree_root["children"][0]["id"] == child["id"]
        assert tree_root["children"][0]["children"][0]["id"] == grandchild["id"]

        cycle = await client.patch(
            f"/v1/web/categories/{root['id']}", json={"parentId": grandchild["id"]}
        )
        assert cycle.status_code == 422
        assert cycle.json()["detail"] == "category cycle detected"


@pytest.mark.asyncio
async def test_settings_roundtrip_and_reset(session_factory, database_url):
    scanner_id = uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            Scanner(
                id=scanner_id,
                code="SETTINGS-TEST",
                name="Settings Test Scanner",
                mode_timeout_seconds=30,
                store_timeout_seconds=1800,
            )
        )
        session.add(ScannerContext(scanner_id=scanner_id))

    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        settings = (await client.get("/v1/web/settings")).json()
        assert settings["removalPolicy"] == "FEFO"
        assert {
            key: settings[key]
            for key in (
                "intakePopupTimeoutSeconds",
                "intakeScanGroupTimeoutSeconds",
                "scannerModeTimeoutSeconds",
                "scannerContextTimeoutSeconds",
                "scannerOfflineAfterSeconds",
                "toastDurationSeconds",
                "confirmationTimeoutSeconds",
                "liveUpdateDebounceMs",
                "fallbackRefreshSeconds",
            )
        } == {
            "intakePopupTimeoutSeconds": 30,
            "intakeScanGroupTimeoutSeconds": 30,
            "scannerModeTimeoutSeconds": 30,
            "scannerContextTimeoutSeconds": 1800,
            "scannerOfflineAfterSeconds": 172800,
            "toastDurationSeconds": 3,
            "confirmationTimeoutSeconds": 3,
            "liveUpdateDebounceMs": 300,
            "fallbackRefreshSeconds": 60,
        }

        updated = (
            await client.put(
                "/v1/web/settings",
                json={
                    "removalPolicy": "FIFO",
                    "expiringWindowDays": 7,
                    "intakePopupTimeoutSeconds": 45,
                    "intakeScanGroupTimeoutSeconds": 12,
                    "scannerModeTimeoutSeconds": 90,
                    "scannerContextTimeoutSeconds": 2400,
                    "scannerOfflineAfterSeconds": 3600,
                    "toastDurationSeconds": 5,
                    "confirmationTimeoutSeconds": 6,
                    "liveUpdateDebounceMs": 450,
                    "fallbackRefreshSeconds": 120,
                },
            )
        ).json()
        assert updated["removalPolicy"] == "FIFO"
        assert updated["expiringWindowDays"] == 7
        assert updated["intakePopupTimeoutSeconds"] == 45
        assert updated["intakeScanGroupTimeoutSeconds"] == 12
        assert updated["scannerModeTimeoutSeconds"] == 90
        assert updated["scannerContextTimeoutSeconds"] == 2400
        assert updated["scannerOfflineAfterSeconds"] == 3600
        assert updated["toastDurationSeconds"] == 5
        assert updated["confirmationTimeoutSeconds"] == 6
        assert updated["liveUpdateDebounceMs"] == 450
        assert updated["fallbackRefreshSeconds"] == 120

        invalid = await client.put("/v1/web/settings", json={"scannerModeTimeoutSeconds": 4})
        assert invalid.status_code == 422

        await client.post("/v1/web/quick-add", json={"name": "Eggs", "quantity": 3})
        overview = (await client.get("/v1/web/overview")).json()
        assert overview["metrics"]["unitsInStock"] == 3
        assert overview["windowDays"] == 7

        csv_export = await client.get("/v1/web/export/inventory.csv")
        assert csv_export.status_code == 200
        assert "Eggs" in csv_export.text

        reset = (await client.post("/v1/web/maintenance/reset-inventory")).json()
        assert reset["removed"] == 3
        overview = (await client.get("/v1/web/overview")).json()
        assert overview["metrics"]["unitsInStock"] == 0

    async with session_factory() as session:
        scanner = await session.get(Scanner, scanner_id)
        assert scanner is not None
        assert scanner.mode_timeout_seconds == 90
        assert scanner.store_timeout_seconds == 2400


@pytest.mark.asyncio
async def test_product_detail_exposes_nutrition_basis_and_per_serving(
    session_factory, database_url
):
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        created = (await client.post("/v1/web/quick-add", json={"name": "Iced Tea"})).json()
        barcode = created["barcode"]

    async with session_factory() as session, session.begin():
        session.add(
            ProductNutrition(
                product_barcode=barcode,
                energy_kcal_100g=Decimal("23.529"),
                carbohydrates_g_100g=Decimal("5.588"),
                sugars_g_100g=Decimal("5.294"),
                basis_unit="ml",
                serving_size_text="340 ml (1 CAN)",
                serving_amount=Decimal("340"),
                serving_unit="ml",
                source="OPEN_FOOD_FACTS",
            )
        )

    async with client_for(app) as client:
        nutrition = (await client.get(f"/v1/web/products/{barcode}")).json()["nutrition"]

    assert nutrition["basisAmount"] == 100
    assert nutrition["basisUnit"] == "ml"
    assert nutrition["servingSize"] == "340 ml (1 CAN)"
    assert nutrition["energyKcal"] == pytest.approx(23.529)
    assert nutrition["perServing"]["energyKcal"] == pytest.approx(79.999)
    assert nutrition["perServing"]["sugarsG"] == pytest.approx(18.0)
