from __future__ import annotations

import re

import httpx

from larder.enrichment.off_client import TokenBucket
from larder.schemas.enrichment import FetchDisposition, OffFetchResult


def _clean(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split()).strip()
    return value or None


def _category_hierarchy(value) -> list[str]:
    if not isinstance(value, str):
        return []
    parts = [part.strip() for part in re.split(r"\s*[>/|]\s*", value) if part.strip()]
    result: list[str] = []
    path: list[str] = []
    for part in parts:
        path.append(part)
        slug = re.sub(r"[^a-z0-9]+", "-", "-".join(path).lower()).strip("-")
        if slug:
            result.append(f"upcitemdb:{slug}")
    return result


class UpcItemDbClient:
    """Low-volume UPCitemdb fallback.

    The trial endpoint is intentionally rate limited. A paid endpoint can be
    selected later by changing the base URL and supplying credentials.
    """

    def __init__(
        self,
        *,
        base_url: str,
        requests_per_minute: int = 6,
        connect_timeout: float = 5,
        total_timeout: float = 15,
        user_key: str | None = None,
        key_type: str = "3scale",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.rate_limiter = TokenBucket(requests_per_minute)
        self._owns_client = client is None
        headers = {"Accept": "application/json"}
        if user_key:
            headers.update({"user_key": user_key, "key_type": key_type})
        self.client = client or httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(total_timeout, connect=connect_timeout),
            follow_redirects=True,
        )

    async def fetch_product(self, barcode: str) -> OffFetchResult:
        await self.rate_limiter.acquire()
        try:
            response = await self.client.get(f"{self.base_url}/lookup", params={"upc": barcode})
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            return OffFetchResult(
                FetchDisposition.RETRY, error=type(exc).__name__, provider="UPCITEMDB"
            )
        if response.status_code in {429, 500, 502, 503, 504}:
            return OffFetchResult(
                FetchDisposition.RETRY,
                http_status=response.status_code,
                error=f"UPCitemdb HTTP {response.status_code}",
                provider="UPCITEMDB",
            )
        if response.status_code in {404, 422}:
            return OffFetchResult(
                FetchDisposition.NOT_FOUND,
                http_status=response.status_code,
                provider="UPCITEMDB",
            )
        if not response.is_success:
            return OffFetchResult(
                FetchDisposition.FAILED,
                http_status=response.status_code,
                error=f"UPCitemdb HTTP {response.status_code}",
                provider="UPCITEMDB",
            )
        try:
            body = response.json()
        except ValueError:
            return OffFetchResult(
                FetchDisposition.RETRY,
                http_status=response.status_code,
                error="UPCitemdb response contains invalid JSON",
                provider="UPCITEMDB",
            )
        items = body.get("items") if isinstance(body, dict) else None
        item = (
            items[0]
            if isinstance(items, list) and items and isinstance(items[0], dict)
            else None
        )
        if item is None:
            return OffFetchResult(
                FetchDisposition.NOT_FOUND,
                http_status=response.status_code,
                provider="UPCITEMDB",
            )
        images = item.get("images")
        image = next((_clean(value) for value in images or [] if _clean(value)), None)
        category = _clean(item.get("category"))
        hierarchy = _category_hierarchy(category)
        product = {
            "code": barcode,
            "product_name": _clean(item.get("title")),
            "brands": _clean(item.get("brand")),
            "quantity": _clean(item.get("size")) or _clean(item.get("weight")),
            "image_front_url": image,
            "categories_hierarchy": hierarchy,
            "_category_labels": {
                category_id: label
                for category_id, label in zip(
                    hierarchy,
                    [
                        part.strip()
                        for part in re.split(r"\s*[>/|]\s*", category or "")
                        if part.strip()
                    ],
                    strict=False,
                )
            },
            "_description": _clean(item.get("description")),
            "_manufacturer": _clean(item.get("manufacturer")),
            "_material": _clean(item.get("material")),
            "_variant": _clean(item.get("model")) or _clean(item.get("color")),
            "_source_url": f"https://www.upcitemdb.com/upc/{barcode}",
            "_provider_payload": body,
            "_product_type": "product",
        }
        return OffFetchResult(
            FetchDisposition.FOUND,
            http_status=response.status_code,
            product=product,
            provider="UPCITEMDB",
            product_type="product",
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()
