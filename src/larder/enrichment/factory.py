from __future__ import annotations

from larder.config import Settings
from larder.enrichment.off_client import OpenFoodFactsClient
from larder.enrichment.provider_chain import ProductProviderChain
from larder.enrichment.upcitemdb_client import UpcItemDbClient


def build_product_client(settings: Settings) -> ProductProviderChain:
    """Build the configured product lookup chain for daemon or request-time use."""
    primary = OpenFoodFactsClient(
        base_url=settings.off_base_url,
        api_version=settings.off_api_version,
        user_agent=settings.off_user_agent,
        requests_per_minute=settings.off_requests_per_minute,
        connect_timeout=settings.off_connect_timeout_seconds,
        total_timeout=settings.off_total_timeout_seconds,
    )
    fallbacks = []
    if settings.upcitemdb_enabled:
        fallbacks.append(
            UpcItemDbClient(
                base_url=settings.upcitemdb_base_url,
                requests_per_minute=settings.upcitemdb_requests_per_minute,
                connect_timeout=settings.off_connect_timeout_seconds,
                total_timeout=settings.off_total_timeout_seconds,
                user_key=settings.upcitemdb_user_key,
                key_type=settings.upcitemdb_key_type,
            )
        )
    return ProductProviderChain(primary, fallbacks)
