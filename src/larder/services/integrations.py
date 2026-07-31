from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from larder.config import Settings
from larder.models import IntegrationSetting

# provider id -> (display label, Settings attribute holding the env fallback)
PROVIDERS: dict[str, tuple[str, str]] = {
    "ziptax": ("ZipTax", "ziptax_api_key"),
}


async def resolve_api_key(
    session: AsyncSession, settings: Settings, provider: str
) -> tuple[str | None, str | None]:
    """Effective key for a provider: (key, "db" | "env" | None)."""
    row = await session.get(IntegrationSetting, provider)
    if row is not None and row.api_key:
        return row.api_key, "db"
    _, attr = PROVIDERS[provider]
    env_key = getattr(settings, attr, None)
    if env_key:
        return env_key, "env"
    return None, None
