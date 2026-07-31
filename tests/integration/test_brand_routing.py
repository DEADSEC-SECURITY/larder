import httpx
import pytest

from larder.api import create_app
from larder.config import Settings

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
async def test_canonical_brand_rule_routes_grocery_and_manual_override_wins(
    session_factory, database_url
):
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        created = await client.post(
            "/v1/web/quick-add",
            json={"name": "Kirkland Olive Oil", "brand": "Kirkland-Signature", "quantity": 1},
        )
        assert created.status_code == 201, created.text
        barcode = created.json()["barcode"]

        second = await client.post(
            "/v1/web/quick-add",
            json={"name": "Kirkland Nuts", "brand": " KIRKLAND  Signature ", "quantity": 1},
        )
        assert second.status_code == 201, second.text

        brands = (await client.get("/v1/web/brands")).json()["brands"]
        assert len(brands) == 1
        brand = brands[0]
        assert brand["normalizedName"] == "kirkland signature"
        assert brand["productCount"] == 2

        costco = (await client.post("/v1/web/stores", json={"name": "Costco"})).json()
        wegmans = (await client.post("/v1/web/stores", json={"name": "Wegmans"})).json()

        rule_response = await client.post(
            "/v1/web/store-routing-rules",
            json={
                "matchType": "brand",
                "brandId": brand["id"],
                "storeId": costco["id"],
                "behavior": "require",
            },
        )
        assert rule_response.status_code == 201, rule_response.text
        rule = rule_response.json()
        assert rule["match"]["productCount"] == 2
        assert rule["behavior"] == "require"

        # Creating a rule backfills in-stock units that had no explicit store.
        detail = (await client.get(f"/v1/web/products/{barcode}")).json()
        assert detail["units"][0]["storeId"] == costco["id"]
        assert detail["units"][0]["storeName"] == "Costco"

        # Future units default from the rule, while an explicit store still wins.
        defaulted = await client.post(
            "/v1/web/quick-add",
            json={"name": "Kirkland Coffee", "brand": "Kirkland Signature", "quantity": 1},
        )
        defaulted_barcode = defaulted.json()["barcode"]
        defaulted_detail = (await client.get(f"/v1/web/products/{defaulted_barcode}")).json()
        assert defaulted_detail["units"][0]["storeId"] == costco["id"]

        explicit = await client.post(
            "/v1/web/quick-add",
            json={
                "name": "Kirkland Crackers",
                "brand": "Kirkland Signature",
                "quantity": 1,
                "storeId": wegmans["id"],
            },
        )
        explicit_barcode = explicit.json()["barcode"]
        explicit_detail = (await client.get(f"/v1/web/products/{explicit_barcode}")).json()
        assert explicit_detail["units"][0]["storeId"] == wegmans["id"]

        duplicate = await client.post(
            "/v1/web/store-routing-rules",
            json={
                "matchType": "BRAND",
                "brandId": brand["id"],
                "storeId": wegmans["id"],
            },
        )
        assert duplicate.status_code == 409

        policy = await client.put(
            f"/v1/web/products/{barcode}/stock-policy",
            json={"reorderPoint": 2, "targetStock": 2},
        )
        assert policy.status_code == 200, policy.text

        item = (await client.get("/v1/web/grocery")).json()["items"][0]
        assert item["storeId"] == costco["id"]
        assert item["storeAssignment"] == {
            "storeId": costco["id"],
            "storeName": "Costco",
            "storeActive": True,
            "source": "brand_rule",
            "reason": "Brand rule: Kirkland-Signature",
            "behavior": "require",
            "ruleId": rule["id"],
            "warning": None,
        }

        override = await client.patch(
            f"/v1/web/grocery/{barcode}", json={"preferredStoreId": wegmans["id"]}
        )
        assert override.status_code == 200, override.text
        item = (await client.get("/v1/web/grocery")).json()["items"][0]
        assert item["storeId"] == wegmans["id"]
        assert item["storeAssignment"]["source"] == "manual"

        cleared = await client.patch(f"/v1/web/grocery/{barcode}", json={"preferredStoreId": None})
        assert cleared.status_code == 200, cleared.text
        assignment = (await client.get(f"/v1/web/products/{barcode}/store-assignment")).json()[
            "assignment"
        ]
        assert assignment["storeId"] == costco["id"]
        assert assignment["source"] == "brand_rule"


@pytest.mark.asyncio
async def test_brand_aliases_and_product_brand_correction(session_factory, database_url):
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        brand = await client.post(
            "/v1/web/brands",
            json={"name": "Kirkland Signature", "aliases": ["Kirkland", "Costco Kirkland"]},
        )
        assert brand.status_code == 201, brand.text
        brand = brand.json()
        assert brand["aliases"] == ["Costco Kirkland", "Kirkland"]

        created = await client.post(
            "/v1/web/quick-add",
            json={"name": "Paper Towels", "brand": "KIRKLAND", "quantity": 1},
        )
        barcode = created.json()["barcode"]
        detail = (await client.get(f"/v1/web/products/{barcode}")).json()
        assert detail["brandRef"]["id"] == brand["id"]
        assert detail["brand"] == "Kirkland Signature"

        cleared = await client.put(f"/v1/web/products/{barcode}/brands", json={"brandIds": []})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["brands"] == []
