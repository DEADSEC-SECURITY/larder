import httpx
import pytest
from sqlalchemy import func, select

from larder.api import create_app
from larder.config import Settings
from larder.models import Product

pytestmark = pytest.mark.integration


def web_app(session_factory, database_url):
    settings = Settings(database_url=database_url, scan_api_token="test-token")
    app = create_app(settings)
    app.state.settings = settings
    app.state.session_factory = session_factory
    return app


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_bulk_update_by_barcode_and_ids(session_factory, database_url):
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        created = (
            await client.post("/v1/web/quick-add", json={"name": "Yogurt Cups", "quantity": 5})
        ).json()
        barcode = created["barcode"]
        store = (await client.post("/v1/web/stores", json={"name": "Costco"})).json()

        # Whole-product bulk edit: set store + confirmed expiration on all units.
        response = await client.post(
            "/v1/web/units/bulk",
            json={
                "barcodes": [barcode],
                "action": "update",
                "setStore": True,
                "storeId": store["id"],
                "expiration": "date",
                "expirationDate": "2026-08-01",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"matched": 5, "updated": 5, "skipped": 0}

        detail = (await client.get(f"/v1/web/products/{barcode}")).json()
        assert all(u["storeName"] == "Costco" for u in detail["units"])
        assert all(u["expirationKind"] == "confirmed" for u in detail["units"])
        assert all(u["expirationDate"] == "2026-08-01" for u in detail["units"])

        # Subset bulk waste by explicit ids.
        ids = [u["id"] for u in detail["units"][:2]]
        response = await client.post("/v1/web/units/bulk", json={"itemIds": ids, "action": "waste"})
        assert response.json() == {"matched": 2, "updated": 2, "skipped": 0}
        detail = (await client.get(f"/v1/web/products/{barcode}")).json()
        assert detail["inStock"] == 3

        # Re-running on the same ids skips the no-longer-stocked units.
        response = await client.post(
            "/v1/web/units/bulk", json={"itemIds": ids, "action": "remove"}
        )
        assert response.json() == {"matched": 2, "updated": 0, "skipped": 2}


@pytest.mark.asyncio
async def test_price_editing_single_and_bulk(session_factory, database_url):
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        store = (await client.post("/v1/web/stores", json={"name": "Aldi"})).json()
        created = (
            await client.post(
                "/v1/web/quick-add",
                json={"name": "Frozen Peas", "quantity": 3, "storeId": store["id"]},
            )
        ).json()
        barcode = created["barcode"]
        first = created["itemIds"][0]

        # Single-unit price set → unit price + one history observation.
        updated = (await client.patch(f"/v1/web/units/{first}", json={"price": 1.19})).json()
        assert updated["price"] == pytest.approx(1.19)
        detail = (await client.get(f"/v1/web/products/{barcode}")).json()
        assert detail["lowestPrice"] == pytest.approx(1.19)
        assert len(detail["history"]) == 1

        # Bulk price on all units → one additional observation, not three.
        response = await client.post(
            "/v1/web/units/bulk",
            json={"barcodes": [barcode], "action": "update", "setPrice": True, "price": 0.99},
        )
        assert response.json()["updated"] == 3
        detail = (await client.get(f"/v1/web/products/{barcode}")).json()
        assert all(u["price"] == pytest.approx(0.99) for u in detail["units"])
        assert len(detail["history"]) == 2
        assert detail["lowestPrice"] == pytest.approx(0.99)

        # Explicit null clears the price without touching history.
        cleared = (await client.patch(f"/v1/web/units/{first}", json={"price": None})).json()
        assert cleared["price"] is None
        detail = (await client.get(f"/v1/web/products/{barcode}")).json()
        assert len(detail["history"]) == 2


@pytest.mark.asyncio
async def test_bulk_update_requires_changes(session_factory, database_url):
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        response = await client.post(
            "/v1/web/units/bulk", json={"barcodes": ["X"], "action": "update"}
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_delete_without_history_hard_deletes(session_factory, database_url):
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        # Grocery-only manual product: no inventory rows exist.
        await client.post("/v1/web/grocery", json={"name": "Napkins", "quantity": 1})
        grocery = (await client.get("/v1/web/grocery")).json()["items"]
        barcode = grocery[0]["barcode"]

        response = await client.delete(f"/v1/web/products/{barcode}")
        assert response.json() == {"barcode": barcode, "deleted": True, "archived": False}
        assert (await client.get(f"/v1/web/products/{barcode}")).status_code == 404

    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(Product))
        assert count == 0


@pytest.mark.asyncio
async def test_delete_with_history_archives_and_restores(session_factory, database_url):
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        created = (
            await client.post("/v1/web/quick-add", json={"name": "Old Sauce", "quantity": 1})
        ).json()
        barcode = created["barcode"]
        item_id = created["itemIds"][0]

        # Stock present → refused.
        assert (await client.delete(f"/v1/web/products/{barcode}")).status_code == 409

        await client.post(f"/v1/web/units/{item_id}/remove")
        response = await client.delete(f"/v1/web/products/{barcode}")
        assert response.json() == {"barcode": barcode, "deleted": False, "archived": True}

        # Hidden from the catalog but still resolvable directly.
        products = (await client.get("/v1/web/products")).json()["products"]
        assert all(p["barcode"] != barcode for p in products)
        detail = (await client.get(f"/v1/web/products/{barcode}")).json()
        assert detail["archived"] is True

        # Restore brings it back.
        await client.post(f"/v1/web/products/{barcode}/restore")
        products = (await client.get("/v1/web/products")).json()["products"]
        assert any(p["barcode"] == barcode for p in products)

        # Quick-adding stock to an archived product also revives it.
        await client.delete(f"/v1/web/products/{barcode}")
        await client.post("/v1/web/quick-add", json={"barcode": barcode, "quantity": 1})
        detail = (await client.get(f"/v1/web/products/{barcode}")).json()
        assert detail["archived"] is False
        assert detail["inStock"] == 1
