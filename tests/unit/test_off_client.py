import asyncio

import httpx

from larder.enrichment.off_client import OpenFoodFactsClient
from larder.schemas.enrichment import FetchDisposition


def test_accepts_v3_success_with_warnings_for_canonical_upca():
    async def scenario():
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/api/v2/taxonomy"):
                assert request.url.params["tagtype"] == "categories"
                return httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json={
                        "en:berries": {"parents": ["en:fruits"]},
                        "en:strawberries": {"parents": ["en:berries"]},
                    },
                )
            assert request.url.path.endswith("/api/v3.6/product/0613008725914.json")
            assert "serving_size" in request.url.params["fields"]
            assert request.url.params["product_type"] == "all"
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "status": "success_with_warnings",
                    "product": {
                        "code": "0613008725914",
                        "product_name": "HALF & HALF ICED TEA LEMONADE",
                        "brands": "Arnold Palmer LITE",
                        "categories_tags": ["en:berries", "en:strawberries"],
                    },
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            off = OpenFoodFactsClient(
                base_url="https://world.openfoodfacts.org",
                user_agent="Larder/0.1 (test@example.invalid)",
                client=client,
            )
            result = await off.fetch_product("0613008725914")

        assert result.disposition == FetchDisposition.FOUND
        assert result.product is not None
        assert result.product["product_name"] == "HALF & HALF ICED TEA LEMONADE"
        assert result.product["_category_parents"] == {
            "en:berries": "en:fruits",
            "en:strawberries": "en:berries",
        }

    asyncio.run(scenario())
