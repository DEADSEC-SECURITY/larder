"""Shopping-mode scanning, per-trip prices, row deletion, and checkout."""

import httpx
import pytest
from sqlalchemy import select

from larder.api import create_app
from larder.config import Settings
from larder.enrichment.worker import EnrichmentWorker
from larder.models import GroceryListItem, InventoryItem, Product, StockGroupProduct
from larder.models.enums import EnrichmentStatus, InventoryStatus
from larder.schemas.enrichment import FetchDisposition, OffFetchResult
from larder.services.stock_matching import create_stock_group_from_product

pytestmark = pytest.mark.integration

# Distinct, checksum-valid GTINs standing in for two dairy brands and an oat milk.
MILK_A = "4006381333931"
MILK_B = "5000112637922"
OAT_MILK = "8710398515162"
SNACK = "0012000161155"


def web_app(session_factory, database_url, **overrides):
    settings = Settings(database_url=database_url, scan_api_token="test-token", **overrides)
    app = create_app(settings)
    app.state.settings = settings
    app.state.session_factory = session_factory
    return app


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://web-test")


async def add_to_list(client, **payload):
    response = await client.post("/v1/web/shopping", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def items_by_name(client) -> dict:
    listing = await client.get("/v1/web/shopping")
    assert listing.status_code == 200, listing.text
    return {item["name"]: item for item in listing.json()["items"]}


@pytest.mark.asyncio
async def test_scanning_checks_one_unit_at_a_time(session_factory, database_url):
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        await client.post("/v1/web/barcodes/resolve", json={"barcode": SNACK})
        await add_to_list(client, barcode=SNACK, quantity=2)

        first = await client.post("/v1/web/shopping/scan", json={"barcode": SNACK})
        assert first.status_code == 200, first.text
        assert first.json()["matched"] is True
        assert first.json()["added"] is False
        # Two on the list, one in the cart — not done yet.
        assert first.json()["checkedQuantity"] == 1
        assert first.json()["checked"] is False

        # Clearing completed rows must not discard a partially scanned x2 row.
        partial_clear = await client.post("/v1/web/shopping/clear-checks")
        assert partial_clear.json()["removed"] == 0

        second = await client.post("/v1/web/shopping/scan", json={"barcode": SNACK})
        assert second.json()["checkedQuantity"] == 2
        assert second.json()["checked"] is True

        # A third scan cannot over-check the row.
        third = await client.post("/v1/web/shopping/scan", json={"barcode": SNACK})
        assert third.json()["checkedQuantity"] == 2

        completed_clear = await client.post("/v1/web/shopping/clear-checks")
        assert completed_clear.json()["removed"] == 1
        assert (await client.get("/v1/web/shopping")).json()["items"] == []


@pytest.mark.asyncio
async def test_unknown_phone_scan_starts_enrichment_immediately(session_factory, database_url):
    calls = []

    class ImmediateClient:
        async def fetch_product(self, barcode):
            calls.append(barcode)
            return OffFetchResult(
                FetchDisposition.FOUND,
                http_status=200,
                product={"code": barcode, "product_name": "Instantly Identified Snack"},
            )

    app = web_app(session_factory, database_url)
    app.state.enrichment_worker = EnrichmentWorker(
        session_factory,
        ImmediateClient(),
        Settings(database_url=database_url, scan_api_token="test-token"),
    )

    async with client_for(app) as client:
        scan = await client.post("/v1/web/shopping/scan", json={"barcode": SNACK})
        assert scan.status_code == 200, scan.text
        assert scan.json()["added"] is True
        assert calls == [SNACK]

        listing = await client.get("/v1/web/shopping")
        assert listing.json()["items"][0]["name"] == "Instantly Identified Snack"

    async with session_factory() as session:
        product = await session.get(Product, SNACK)
        assert product.enrichment_status == EnrichmentStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_scanning_a_compatible_brand_satisfies_a_group_row(session_factory, database_url):
    """Any accepted member ticks off a generic row..."""
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        for barcode in (MILK_A, MILK_B):
            await client.post("/v1/web/barcodes/resolve", json={"barcode": barcode})
            await client.patch(
                f"/v1/web/products/{barcode}",
                json={"name": f"Whole Milk {barcode[-2:]}"},
            )

        async with session_factory() as session, session.begin():
            group, _ = await create_stock_group_from_product(
                session, await session.get(Product, MILK_A)
            )
            group_id = group.id
            # Same product name, so the auto-matcher usually accepts MILK_B on
            # its own; only add it explicitly when it did not.
            already = await session.scalar(
                select(StockGroupProduct.product_barcode).where(
                    StockGroupProduct.product_barcode == MILK_B
                )
            )
            if already is None:
                session.add(
                    StockGroupProduct(
                        stock_group_id=group_id, product_barcode=MILK_B, source="USER"
                    )
                )

        await add_to_list(client, stockGroupId=str(group_id), quantity=1)

        # Scanning the *other* brand still satisfies "any compatible milk".
        scan = await client.post("/v1/web/shopping/scan", json={"barcode": MILK_B})
        assert scan.status_code == 200, scan.text
        assert scan.json()["matched"] is True
        assert scan.json()["itemType"] == "stock_group"
        assert scan.json()["identifier"] == f"group:{group_id}"
        assert scan.json()["checked"] is True


@pytest.mark.asyncio
async def test_scanning_dairy_milk_does_not_check_an_oat_milk_row(session_factory, database_url):
    """...but an unrelated product must never tick off a different line."""
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        for barcode, name in ((MILK_A, "Whole Milk"), (OAT_MILK, "Oat Milk")):
            await client.post("/v1/web/barcodes/resolve", json={"barcode": barcode})
            await client.patch(f"/v1/web/products/{barcode}", json={"name": name})

        await add_to_list(client, barcode=OAT_MILK, quantity=1)

        scan = await client.post(
            "/v1/web/shopping/scan", json={"barcode": MILK_A, "addIfMissing": False}
        )
        assert scan.json()["matched"] is False

        items = await items_by_name(client)
        assert items["Oat Milk"]["checked"] is False
        assert items["Oat Milk"]["checkedQuantity"] == 0


@pytest.mark.asyncio
async def test_scanning_an_unlisted_barcode_adds_it_already_checked(session_factory, database_url):
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        assert (await items_by_name(client)) == {}

        scan = await client.post("/v1/web/shopping/scan", json={"barcode": SNACK})
        assert scan.status_code == 200, scan.text
        assert scan.json()["matched"] is True
        assert scan.json()["added"] is True
        assert scan.json()["checked"] is True

        async with session_factory() as session:
            row = await session.get(GroceryListItem, SNACK)
            assert row is not None
            assert row.manual_quantity == 1
            assert row.checked_quantity == 1
            assert row.scanned_barcodes == [SNACK]


@pytest.mark.asyncio
async def test_entered_price_overrides_the_observed_estimate(session_factory, database_url):
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        await client.post("/v1/web/barcodes/resolve", json={"barcode": SNACK})
        await client.patch(f"/v1/web/products/{SNACK}", json={"name": "Sparkling Water"})
        await add_to_list(client, barcode=SNACK, quantity=2)

        items = await items_by_name(client)
        assert items["Sparkling Water"]["unitPrice"] is None

        update = await client.patch(f"/v1/web/shopping/{SNACK}", json={"unitPrice": 3.25})
        assert update.status_code == 200, update.text

        items = await items_by_name(client)
        assert items["Sparkling Water"]["unitPrice"] == 3.25
        assert items["Sparkling Water"]["priceSource"] == "entered"

        # Sending null falls back to the observed estimate again.
        cleared = await client.patch(f"/v1/web/shopping/{SNACK}", json={"unitPrice": None})
        assert cleared.status_code == 200
        items = await items_by_name(client)
        assert items["Sparkling Water"]["priceSource"] is None


@pytest.mark.asyncio
async def test_deleting_a_manual_row_removes_it(session_factory, database_url):
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        await client.post("/v1/web/barcodes/resolve", json={"barcode": SNACK})
        await client.patch(f"/v1/web/products/{SNACK}", json={"name": "Sparkling Water"})
        await add_to_list(client, barcode=SNACK, quantity=1)
        assert "Sparkling Water" in await items_by_name(client)

        deleted = await client.delete(f"/v1/web/shopping/{SNACK}")
        assert deleted.status_code == 200, deleted.text
        assert await items_by_name(client) == {}

        async with session_factory() as session:
            assert await session.get(GroceryListItem, SNACK) is None


@pytest.mark.asyncio
async def test_deleting_an_automatic_row_keeps_it_off_the_list(session_factory, database_url):
    """A restock-policy row would otherwise come straight back."""
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        created = await client.post(
            "/v1/web/quick-add", json={"name": "Paper Towels", "quantity": 1}
        )
        barcode = created.json()["barcode"]
        policy = await client.put(
            f"/v1/web/products/{barcode}/stock-policy",
            json={"reorderPoint": 4, "targetStock": 6},
        )
        assert policy.status_code in (200, 201), policy.text
        assert "Paper Towels" in await items_by_name(client)

        assert (await client.delete(f"/v1/web/shopping/{barcode}")).status_code == 200
        assert "Paper Towels" not in await items_by_name(client)

        # Another recalculation must not resurrect it.
        await client.post("/v1/web/quick-add", json={"barcode": barcode, "quantity": 1})
        assert "Paper Towels" not in await items_by_name(client)


@pytest.mark.asyncio
async def test_checkout_moves_checked_items_into_inventory(session_factory, database_url):
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        await client.post("/v1/web/barcodes/resolve", json={"barcode": SNACK})
        await client.patch(f"/v1/web/products/{SNACK}", json={"name": "Sparkling Water"})
        await add_to_list(client, barcode=SNACK, quantity=2)
        await client.patch(f"/v1/web/shopping/{SNACK}", json={"unitPrice": 2.50})

        # Two scans = two in the cart.
        await client.post("/v1/web/shopping/scan", json={"barcode": SNACK})
        await client.post("/v1/web/shopping/scan", json={"barcode": SNACK})

        checkout = await client.post("/v1/web/shopping/checkout", json={})
        assert checkout.status_code == 200, checkout.text
        assert checkout.json()["added"] == 2
        assert checkout.json()["skipped"] == []

        async with session_factory() as session:
            units = (
                (
                    await session.execute(
                        select(InventoryItem).where(
                            InventoryItem.product_barcode == SNACK,
                            InventoryItem.status == InventoryStatus.IN_STOCK,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(units) == 2
            assert all(unit.price_paid is not None for unit in units)

        # Bought items leave the list.
        assert "Sparkling Water" not in await items_by_name(client)


@pytest.mark.asyncio
async def test_checkout_of_a_group_row_uses_the_scanned_member(session_factory, database_url):
    """The whole point of scanning: checkout knows which brand was bought."""
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        for barcode in (MILK_A, MILK_B):
            await client.post("/v1/web/barcodes/resolve", json={"barcode": barcode})
            await client.patch(
                f"/v1/web/products/{barcode}",
                json={"name": f"Whole Milk {barcode[-2:]}"},
            )

        async with session_factory() as session, session.begin():
            group, _ = await create_stock_group_from_product(
                session, await session.get(Product, MILK_A)
            )
            group_id = group.id
            # Same product name, so the auto-matcher usually accepts MILK_B on
            # its own; only add it explicitly when it did not.
            already = await session.scalar(
                select(StockGroupProduct.product_barcode).where(
                    StockGroupProduct.product_barcode == MILK_B
                )
            )
            if already is None:
                session.add(
                    StockGroupProduct(
                        stock_group_id=group_id, product_barcode=MILK_B, source="USER"
                    )
                )

        await add_to_list(client, stockGroupId=str(group_id), quantity=1)
        await client.post("/v1/web/shopping/scan", json={"barcode": MILK_B})

        checkout = await client.post("/v1/web/shopping/checkout", json={})
        assert checkout.status_code == 200, checkout.text
        assert checkout.json()["added"] == 1

        async with session_factory() as session:
            # MILK_B went in the cart, so MILK_B is what lands in inventory.
            stocked = (
                (
                    await session.execute(
                        select(InventoryItem.product_barcode).where(
                            InventoryItem.status == InventoryStatus.IN_STOCK
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert stocked == [MILK_B]


@pytest.mark.asyncio
async def test_a_skipped_row_stays_on_the_list(session_factory, database_url):
    """Nothing stocked means nothing was bought — the row must survive.

    With requireStoreOnManualAdd on, an item with no store cannot be stocked.
    Clearing it anyway would silently lose it off the list.
    """
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        settings = await client.put("/v1/web/settings", json={"requireStoreOnManualAdd": True})
        assert settings.status_code == 200, settings.text

        await client.post("/v1/web/barcodes/resolve", json={"barcode": SNACK})
        await client.patch(f"/v1/web/products/{SNACK}", json={"name": "Sparkling Water"})
        await add_to_list(client, barcode=SNACK, quantity=1)
        await client.post("/v1/web/shopping/scan", json={"barcode": SNACK})

        checkout = await client.post("/v1/web/shopping/checkout", json={})
        assert checkout.json()["added"] == 0
        assert len(checkout.json()["skipped"]) == 1
        assert "store" in checkout.json()["skipped"][0]["reason"]

        items = await items_by_name(client)
        assert "Sparkling Water" in items
        # The check survives too, so retrying does not mean re-scanning.
        assert items["Sparkling Water"]["checkedQuantity"] == 1

        async with session_factory() as session:
            stocked = (
                (
                    await session.execute(
                        select(InventoryItem).where(InventoryItem.product_barcode == SNACK)
                    )
                )
                .scalars()
                .all()
            )
            assert stocked == []

        # Naming the trip's store lets the same cart go through.
        store = await client.post("/v1/web/stores", json={"name": "Corner Shop"})
        assert store.status_code in (200, 201), store.text
        retry = await client.post("/v1/web/shopping/checkout", json={"storeId": store.json()["id"]})
        assert retry.status_code == 200, retry.text
        assert retry.json()["added"] == 1
        assert retry.json()["skipped"] == []
        assert "Sparkling Water" not in await items_by_name(client)


@pytest.mark.asyncio
async def test_checkout_with_nothing_checked_is_a_no_op(session_factory, database_url):
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        await client.post("/v1/web/barcodes/resolve", json={"barcode": SNACK})
        await add_to_list(client, barcode=SNACK, quantity=1)

        checkout = await client.post("/v1/web/shopping/checkout", json={})
        assert checkout.status_code == 200
        assert checkout.json() == {"added": 0, "items": [], "skipped": []}
