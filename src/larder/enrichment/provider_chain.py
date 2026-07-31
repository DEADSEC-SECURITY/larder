from __future__ import annotations

from larder.schemas.enrichment import FetchDisposition, OffFetchResult, ProviderAttempt


def _attempt(result: OffFetchResult) -> ProviderAttempt:
    return ProviderAttempt(
        provider=result.provider,
        disposition=result.disposition,
        http_status=result.http_status,
        product_type=result.product_type,
        product=result.product,
        error=result.error,
    )


class ProductProviderChain:
    def __init__(self, primary, fallbacks: list | None = None) -> None:
        self.primary = primary
        self.fallbacks = fallbacks or []

    async def fetch_product(self, barcode: str) -> OffFetchResult:
        attempts: list[ProviderAttempt] = []
        result = await self.primary.fetch_product(barcode)
        attempts.append(_attempt(result))
        if result.disposition == FetchDisposition.FOUND:
            return OffFetchResult(
                result.disposition,
                result.http_status,
                result.product,
                result.error,
                result.provider,
                result.product_type,
                tuple(attempts),
            )
        # Provider outages should retry instead of turning a temporary primary
        # failure into a permanent generic identification.
        if result.disposition != FetchDisposition.NOT_FOUND:
            return OffFetchResult(
                result.disposition,
                result.http_status,
                result.product,
                result.error,
                result.provider,
                result.product_type,
                tuple(attempts),
            )
        for provider in self.fallbacks:
            result = await provider.fetch_product(barcode)
            attempts.append(_attempt(result))
            if result.disposition != FetchDisposition.NOT_FOUND:
                return OffFetchResult(
                    result.disposition,
                    result.http_status,
                    result.product,
                    result.error,
                    result.provider,
                    result.product_type,
                    tuple(attempts),
                )
        return OffFetchResult(
            FetchDisposition.NOT_FOUND,
            http_status=result.http_status,
            provider=result.provider,
            attempts=tuple(attempts),
        )

    async def aclose(self) -> None:
        await self.primary.aclose()
        for provider in self.fallbacks:
            await provider.aclose()
