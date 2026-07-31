from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy.ext.asyncio import AsyncSession

from larder.config import Settings
from larder.models import IntegrationSetting
from larder.services.integrations import PROVIDERS, resolve_api_key
from larder.services.tax import lookup_ziptax
from larder.webapi.common import get_app_settings, get_session, web_api_router

router = web_api_router(tags=["integrations"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


class IntegrationKeyRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    # None clears the saved key (env fallback applies again).
    api_key: str | None = Field(default=None, max_length=200)


class IntegrationTestRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    address: str = Field(default="100 Main St, Rochester, NY 14604", max_length=300)


def _mask(key: str) -> str:
    return f"••••{key[-4:]}" if len(key) >= 8 else "••••"


def _require_provider(provider: str) -> None:
    if provider not in PROVIDERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown integration: {provider}")


async def _entry(session: AsyncSession, settings: Settings, provider: str) -> dict:
    key, source = await resolve_api_key(session, settings, provider)
    label, _attr = PROVIDERS[provider]
    payload = {
        "provider": provider,
        "label": label,
        "configured": bool(key),
        # "db" = saved from the UI, "env" = server environment variable.
        "source": source,
        "keyMask": _mask(key) if key else None,
    }
    if provider == "ziptax":
        payload["baseUrl"] = settings.ziptax_base_url
    return payload


@router.get("/integrations")
async def list_integrations(session: SessionDep, settings: SettingsDep) -> dict:
    return {
        "integrations": [
            await _entry(session, settings, provider) for provider in PROVIDERS
        ]
    }


@router.put("/integrations/{provider}")
async def set_integration_key(
    provider: str, payload: IntegrationKeyRequest, session: SessionDep, settings: SettingsDep
) -> dict:
    _require_provider(provider)
    key = payload.api_key.strip() if payload.api_key else None
    if payload.api_key is not None and not key:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "apiKey cannot be blank")
    row = await session.get(IntegrationSetting, provider, with_for_update=True)
    if row is None:
        row = IntegrationSetting(provider=provider)
        session.add(row)
    row.api_key = key
    await session.flush()
    return await _entry(session, settings, provider)


@router.post("/integrations/ziptax/test")
async def test_ziptax(
    payload: IntegrationTestRequest, session: SessionDep, settings: SettingsDep
) -> dict:
    key, source = await resolve_api_key(session, settings, "ziptax")
    rate, normalized = await lookup_ziptax(settings, payload.address, api_key=key)
    return {
        "ok": True,
        "source": source,
        "address": payload.address,
        "normalizedAddress": normalized,
        "rate": float(rate),
        "percentage": round(float(rate) * 100, 4),
    }
