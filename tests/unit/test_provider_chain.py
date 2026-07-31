import asyncio

import httpx

from larder.enrichment.mapper import map_product
from larder.enrichment.provider_chain import ProductProviderChain
from larder.enrichment.upcitemdb_client import UpcItemDbClient
from larder.schemas.enrichment import FetchDisposition, OffFetchResult


class StubProvider:
    def __init__(self, result):
        self.result = result

    async def fetch_product(self, _barcode):
        return self.result

    async def aclose(self):
        pass


def test_provider_chain_uses_generic_fallback_after_open_facts_miss():
    async def scenario():
        primary = StubProvider(
            OffFetchResult(FetchDisposition.NOT_FOUND, 404, provider="OPEN_FACTS")
        )
        fallback = StubProvider(
            OffFetchResult(
                FetchDisposition.FOUND,
                200,
                {"code": "123", "product_name": "Nitrile Gloves"},
                provider="UPCITEMDB",
                product_type="product",
            )
        )
        result = await ProductProviderChain(primary, [fallback]).fetch_product("123")
        assert result.disposition == FetchDisposition.FOUND
        assert result.provider == "UPCITEMDB"
        assert [attempt.provider for attempt in result.attempts] == [
            "OPEN_FACTS",
            "UPCITEMDB",
        ]

    asyncio.run(scenario())


def test_upcitemdb_maps_household_product_and_category_tree():
    async def scenario():
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/prod/trial/lookup"
            assert request.url.params["upc"] == "012345678905"
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "code": "OK",
                    "items": [
                        {
                            "ean": "0012345678905",
                            "title": "NITRILE CLEANING GLOVES",
                            "brand": "ACME",
                            "category": "Home & Garden > Household Supplies > Gloves",
                            "size": "100 Count",
                            "description": "Disposable gloves",
                            "images": ["https://example.test/gloves.jpg"],
                        }
                    ],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = UpcItemDbClient(
                base_url="https://api.upcitemdb.com/prod/trial", client=http
            )
            result = await client.fetch_product("012345678905")
        assert result.disposition == FetchDisposition.FOUND
        mapped = map_product(result.product)
        assert mapped.values["name"] == "Nitrile Cleaning Gloves"
        assert mapped.values["product_kind"] == "HOUSEHOLD"
        assert mapped.values["package_size"] == "100 Count"
        assert mapped.categories[-1].endswith("household-supplies-gloves")
        assert mapped.category_parents[mapped.categories[-1]] == mapped.categories[-2]

    asyncio.run(scenario())
