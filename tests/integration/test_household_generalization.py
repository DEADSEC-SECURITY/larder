import httpx

from larder.api import create_app
from larder.config import get_settings


async def test_household_product_storage_and_shopping_aliases(database_url):
    settings = get_settings()
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created_location = await client.post(
                "/v1/web/storage-locations", json={"name": "Utility Closet"}
            )
            assert created_location.status_code == 201
            location = created_location.json()

            added = await client.post(
                "/v1/web/quick-add",
                json={
                    "name": "Nitrile Gloves",
                    "brand": "ACME",
                    "productKind": "HOUSEHOLD",
                    "description": "Disposable cleaning gloves",
                    "packageSize": "100 Count",
                    "storageLocationId": location["id"],
                },
            )
            assert added.status_code == 201
            barcode = added.json()["barcode"]

            detail = (await client.get(f"/v1/web/products/{barcode}")).json()
            assert detail["productKind"] == "household"
            assert detail["packageSize"] == "100 Count"
            assert detail["units"][0]["storageLocationName"] == "Utility Closet"

            patched = await client.patch(
                f"/v1/web/products/{barcode}",
                json={"manufacturer": "ACME INDUSTRIES", "variant": "LARGE"},
            )
            assert patched.status_code == 200
            assert patched.json()["manufacturer"] == "Acme Industries"
            assert patched.json()["fieldSources"]["manufacturer"] == "USER"

            assert (
                await client.post(
                    "/v1/web/shopping", json={"barcode": barcode, "quantity": 2}
                )
            ).status_code == 201
            shopping = (await client.get("/v1/web/shopping")).json()
            grocery = (await client.get("/v1/web/grocery")).json()
            assert shopping == grocery
            assert shopping["items"][0]["productKind"] == "household"
