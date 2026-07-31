from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from larder.api import create_app
from larder.config import Settings, get_settings
from larder.models import (
    FoodCategory,
    GroceryListItem,
    Product,
    ProductCategory,
    ProductStockPolicy,
    Scanner,
    ScannerContext,
    StockGroupProduct,
    StockGroupShoppingItem,
)
from larder.models.enums import ScanResult
from larder.schemas.scans import ScanEnvelope
from larder.services.command_router import CommandRouter
from larder.services.stock_matching import create_stock_group_from_product

pytestmark = pytest.mark.integration
GTIN = "4006381333931"


@pytest.mark.asyncio
async def test_stock_group_aggregates_inventory_and_migrates_exact_policy(database_url):
    settings = get_settings()
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            seed = await client.post(
                "/v1/web/quick-add",
                json={
                    "name": "Driscoll's Strawberries",
                    "brand": "Driscoll's",
                    "packageSize": "16 Oz",
                    "quantity": 1,
                },
            )
            assert seed.status_code == 201, seed.text
            seed_barcode = seed.json()["barcode"]
            second = await client.post(
                "/v1/web/quick-add",
                json={
                    "name": "Wegmans Organic Strawberries",
                    "brand": "Wegmans",
                    "packageSize": "32 Oz",
                    "quantity": 1,
                },
            )
            assert second.status_code == 201, second.text
            second_barcode = second.json()["barcode"]

            exact_policy = await client.put(
                f"/v1/web/products/{seed_barcode}/stock-policy",
                json={"reorderPoint": 3, "targetStock": 4},
            )
            assert exact_policy.status_code == 200, exact_policy.text

            created = await client.post(
                "/v1/web/stock-groups",
                json={"seedBarcode": seed_barcode},
            )
            assert created.status_code == 201, created.text
            group = created.json()["stockGroup"]
            group_id = group["id"]
            assert group["name"] == "Strawberries"
            assert group["memberCount"] == 2
            assert group["inStock"] == 2
            assert group["policy"] == {"reorderPoint": 3, "targetStock": 4}

            detail = (await client.get(f"/v1/web/stock-groups/{group_id}")).json()
            assert {member["barcode"] for member in detail["members"]} == {
                seed_barcode,
                second_barcode,
            }
            assert any(member["source"] == "automatic" for member in detail["members"])

            shopping = (await client.get("/v1/web/shopping")).json()
            assert len(shopping["items"]) == 1
            item = shopping["items"][0]
            assert item["itemType"] == "stock_group"
            assert item["barcode"] == f"group:{group_id}"
            assert item["scopeLabel"] == "Any compatible brand or size"
            assert item["quantity"] == 2
            assert item["inStock"] == 2

            seed_detail = (await client.get(f"/v1/web/products/{seed_barcode}")).json()
            assert seed_detail["stockGroup"]["id"] == group_id
            assert seed_detail["stockGroup"]["inStock"] == 2
            assert seed_detail["policy"]["scope"] == "stock_group"

            redirected = await client.put(
                f"/v1/web/products/{second_barcode}/stock-policy",
                json={"reorderPoint": 2, "targetStock": 3},
            )
            assert redirected.status_code == 200, redirected.text
            assert redirected.json()["scope"] == "stock_group"
            assert redirected.json()["stockGroupId"] == group_id
            assert (await client.get("/v1/web/shopping")).json()["items"] == []

            manual = await client.post(
                "/v1/web/shopping",
                json={"stockGroupId": group_id, "quantity": 2},
            )
            assert manual.status_code == 201, manual.text
            assert manual.json()["barcode"] == f"group:{group_id}"
            checked = await client.patch(
                f"/v1/web/shopping/group:{group_id}", json={"checked": True}
            )
            assert checked.status_code == 200, checked.text
            assert checked.json()["checked"] is True
            cleared = await client.post("/v1/web/shopping/clear-checks")
            assert cleared.json()["removed"] == 1
            assert (await client.get("/v1/web/shopping")).json()["items"] == []

            await client.put(
                f"/v1/web/stock-groups/{group_id}/stock-policy",
                json={"reorderPoint": 3, "targetStock": 4},
            )
            before_deactivate = (await client.get(f"/v1/web/products/{seed_barcode}")).json()
            assert before_deactivate["lowStock"] is True
            deactivated = await client.patch(
                f"/v1/web/stock-groups/{group_id}", json={"active": False}
            )
            assert deactivated.status_code == 200, deactivated.text
            after_deactivate = (await client.get(f"/v1/web/products/{seed_barcode}")).json()
            assert after_deactivate["stockGroup"]["active"] is False
            assert after_deactivate["policy"] is None
            assert after_deactivate["lowStock"] is False

        async with app.state.session_factory() as session:
            assert await session.get(ProductStockPolicy, seed_barcode) is None
            assert await session.get(GroceryListItem, seed_barcode) is None


@pytest.mark.asyncio
async def test_scanner_list_mode_targets_known_stock_group(session_factory, database_url):
    scanner_id = uuid4()
    async with session_factory() as session, session.begin():
        product = Product(
            barcode=GTIN,
            barcode_format="GTIN_13",
            name="Strawberries",
            product_kind="FOOD",
        )
        session.add(product)
        session.add(
            Scanner(
                id=scanner_id,
                code="SCN01",
                name="Kitchen scanner",
                mode_timeout_seconds=30,
                store_timeout_seconds=1800,
            )
        )
        session.add(ScannerContext(scanner_id=scanner_id))
        await session.flush()
        group, _ = await create_stock_group_from_product(session, product)
        group_id = group.id

    service = CommandRouter(
        session_factory, Settings(database_url=database_url, scan_api_token="test")
    )

    def envelope(payload: str) -> ScanEnvelope:
        return ScanEnvelope(
            id=uuid4(),
            raw_frame=f"LDR:SCN01|{payload}\r\n".encode(),
            port="fake:SCN01",
            expected_scanner_code="SCN01",
        )

    assert (await service.process(envelope("CMD:MODE:LIST"))).result == ScanResult.APPLIED
    assert (await service.process(envelope(GTIN))).result == ScanResult.APPLIED
    assert (await service.process(envelope(GTIN))).result == ScanResult.APPLIED

    async with session_factory() as session:
        group_row = await session.get(StockGroupShoppingItem, group_id)
        assert group_row is not None
        assert group_row.manual_quantity == 2
        assert await session.get(GroceryListItem, GTIN) is None
        assert (
            await session.scalar(
                select(func.count())
                .select_from(StockGroupProduct)
                .where(StockGroupProduct.stock_group_id == group_id)
            )
            == 1
        )


@pytest.mark.asyncio
async def test_rejected_candidate_teaches_group_form_exclusion(session_factory, database_url):
    seed_barcode = "0000000000101"
    frozen_barcode = "0000000000102"
    async with session_factory() as session, session.begin():
        session.add_all(
            [
                FoodCategory(id="en:fruits", display_name="Fruits"),
                FoodCategory(id="en:berries", display_name="Berries"),
                FoodCategory(id="en:frozen-fruits", display_name="Frozen Fruits"),
            ]
        )
        seed = Product(
            barcode=seed_barcode,
            barcode_format="GTIN_13",
            name="Strawberries",
            product_kind="FOOD",
        )
        frozen = Product(
            barcode=frozen_barcode,
            barcode_format="GTIN_13",
            name="Frozen Strawberries",
            product_kind="FOOD",
        )
        session.add_all([seed, frozen])
        await session.flush()
        session.add_all(
            [
                ProductCategory(
                    product_barcode=seed_barcode,
                    category_id="en:fruits",
                    source_rank=0,
                    is_primary=False,
                ),
                ProductCategory(
                    product_barcode=seed_barcode,
                    category_id="en:berries",
                    source_rank=1,
                    is_primary=True,
                ),
                ProductCategory(
                    product_barcode=frozen_barcode,
                    category_id="en:fruits",
                    source_rank=0,
                    is_primary=False,
                ),
                ProductCategory(
                    product_barcode=frozen_barcode,
                    category_id="en:frozen-fruits",
                    source_rank=1,
                    is_primary=True,
                ),
            ]
        )
        await session.flush()
        group, _ = await create_stock_group_from_product(session, seed)
        group_id = group.id

    settings = Settings(database_url=database_url, scan_api_token="test")
    app = create_app(settings)
    app.state.settings = settings
    app.state.session_factory = session_factory
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        detail = (await client.get(f"/v1/web/stock-groups/{group_id}")).json()
        pending = [
            candidate
            for candidate in detail["candidates"]
            if candidate["barcode"] == frozen_barcode
        ]
        assert len(pending) == 1
        assert pending[0]["status"] == "pending"
        assert 0.65 <= pending[0]["confidence"] < 0.90

        rejected = await client.post(
            f"/v1/web/stock-groups/{group_id}/candidates/{frozen_barcode}/decision",
            json={"accepted": False, "learn": True},
        )
        assert rejected.status_code == 200, rejected.text
        assert "frozen" in rejected.json()["excludedTerms"]

        next_barcode = "0000000000103"
        async with session_factory() as session, session.begin():
            session.add(
                Product(
                    barcode=next_barcode,
                    barcode_format="GTIN_13",
                    name="Frozen Strawberries",
                    product_kind="FOOD",
                )
            )
            await session.flush()
            session.add_all(
                [
                    ProductCategory(
                        product_barcode=next_barcode,
                        category_id="en:fruits",
                        source_rank=0,
                        is_primary=False,
                    ),
                    ProductCategory(
                        product_barcode=next_barcode,
                        category_id="en:frozen-fruits",
                        source_rank=1,
                        is_primary=True,
                    ),
                ]
            )

        refreshed = await client.post(
            f"/v1/web/products/{next_barcode}/stock-group-matches/refresh"
        )
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["status"] == "none"
        assert refreshed.json()["evaluation"]["status"] == "no_match"
