from __future__ import annotations

import asyncio
import time
from urllib.parse import quote

import httpx

from larder.schemas.enrichment import FetchDisposition, OffFetchResult

OFF_FIELDS = (
    "code,product_name,generic_name,brands,manufacturing_places,ingredients_text,quantity,"
    "categories,categories_tags,categories_hierarchy,tags_sources,serving_size,nutrition,"
    "nutriments,selected_images,image_front_url,url,last_modified_t,product_type"
)


class TokenBucket:
    def __init__(self, requests_per_minute: int) -> None:
        self.interval = 60.0 / requests_per_minute
        self._next_slot = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next_slot - now)
            if wait:
                await asyncio.sleep(wait)
            self._next_slot = max(now, self._next_slot) + self.interval


class OpenFoodFactsClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_version: str = "v3.6",
        user_agent: str,
        requests_per_minute: int = 12,
        connect_timeout: float = 5,
        total_timeout: float = 15,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version.strip("/")
        self.rate_limiter = TokenBucket(requests_per_minute)
        self._category_parent_cache: dict[str, str | None] = {}
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=httpx.Timeout(total_timeout, connect=connect_timeout),
            follow_redirects=True,
        )

    async def fetch_product(self, barcode: str) -> OffFetchResult:
        await self.rate_limiter.acquire()
        url = f"{self.base_url}/api/{self.api_version}/product/{quote(barcode, safe='')}.json"
        try:
            response = await self.client.get(
                url, params={"fields": OFF_FIELDS, "product_type": "all"}
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            return OffFetchResult(FetchDisposition.RETRY, error=type(exc).__name__)

        if response.status_code in {429, 503}:
            return OffFetchResult(
                FetchDisposition.RETRY,
                http_status=response.status_code,
                error=f"OFF HTTP {response.status_code}",
            )
        if response.status_code == 404:
            return OffFetchResult(FetchDisposition.NOT_FOUND, http_status=404)
        if 400 <= response.status_code < 500:
            return OffFetchResult(
                FetchDisposition.FAILED,
                http_status=response.status_code,
                error=f"OFF HTTP {response.status_code}",
            )
        if response.status_code >= 500:
            return OffFetchResult(
                FetchDisposition.RETRY,
                http_status=response.status_code,
                error=f"OFF HTTP {response.status_code}",
            )
        content_type = response.headers.get("content-type", "").lower()
        if "json" not in content_type:
            return OffFetchResult(
                FetchDisposition.RETRY,
                http_status=response.status_code,
                error="OFF response is not JSON",
            )
        try:
            body = response.json()
        except ValueError:
            return OffFetchResult(
                FetchDisposition.RETRY,
                http_status=response.status_code,
                error="OFF response contains invalid JSON",
            )
        if not isinstance(body, dict):
            return OffFetchResult(
                FetchDisposition.RETRY,
                http_status=response.status_code,
                error="OFF response root is not an object",
            )
        if body.get("status") == 0 or body.get("product") is None:
            return OffFetchResult(FetchDisposition.NOT_FOUND, http_status=response.status_code)
        product = body.get("product")
        if not isinstance(product, dict) or str(product.get("code", "")) != barcode:
            return OffFetchResult(
                FetchDisposition.RETRY,
                http_status=response.status_code,
                error="OFF response barcode mismatch",
            )
        product = dict(product)
        product_type = body.get("product_type") or product.get("product_type")
        if isinstance(product_type, str):
            product["_product_type"] = product_type
        product.setdefault("url", f"{self.base_url}/product/{barcode}")
        categories = product.get("categories_tags")
        if isinstance(categories, list):
            product["_category_parents"] = await self.fetch_category_parents(
                [item for item in categories if isinstance(item, str)]
            )
        return OffFetchResult(
            FetchDisposition.FOUND,
            http_status=response.status_code,
            product=product,
            provider="OPEN_FACTS",
            product_type=product_type if isinstance(product_type, str) else None,
        )

    async def fetch_category_parents(
        self, category_ids: list[str]
    ) -> dict[str, str | None]:
        """Return OFF's first canonical parent for each requested category."""
        requested = list(
            dict.fromkeys(
                category_id
                for category_id in category_ids
                if category_id.startswith("en:")
            )
        )
        missing = [
            category_id
            for category_id in requested
            if category_id not in self._category_parent_cache
        ]
        if missing:
            await self.rate_limiter.acquire()
            url = f"{self.base_url}/api/v2/taxonomy"
            try:
                response = await self.client.get(
                    url,
                    params={
                        "tagtype": "categories",
                        "tags": ",".join(missing),
                        "fields": "name,parents",
                        "include_parents": "1",
                        "lc": "en",
                    },
                )
                if response.is_success and "json" in response.headers.get(
                    "content-type", ""
                ).lower():
                    body = response.json()
                    if isinstance(body, dict):
                        for category_id in missing:
                            entry = body.get(category_id)
                            if not isinstance(entry, dict):
                                continue
                            parents = entry.get("parents")
                            parent_id = next(
                                (
                                    parent
                                    for parent in parents or []
                                    if isinstance(parent, str) and parent.startswith("en:")
                                ),
                                None,
                            )
                            self._category_parent_cache[category_id] = parent_id
            except (httpx.HTTPError, ValueError):
                # Product enrichment is still useful when taxonomy lookup is
                # temporarily unavailable. A later scan or explicit sync retries it.
                pass
        return {
            category_id: self._category_parent_cache[category_id]
            for category_id in requested
            if category_id in self._category_parent_cache
        }

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()
