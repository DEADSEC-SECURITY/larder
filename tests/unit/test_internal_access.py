from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from starlette.requests import Request

from larder.config import Settings
from larder.webapi.auth import install_web_auth, internal_access, internal_operation


@pytest.mark.parametrize(
    "origin",
    [
        "http://larder.house.internal",
        "https://larder.example.com",
        "https://user:password@larder.house.internal",
        "https://larder.house.internal/path",
        "https://larder.house.internal?query=1",
        "https://larder.house.internal#fragment",
        "https://larder.house.internal:8443",
    ],
)
def test_internal_origin_rejects_unsafe_configuration(origin):
    with pytest.raises(ValidationError):
        Settings(internal_base_url=origin)


def test_internal_origin_is_opt_in_and_normalized():
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/web/overview",
            "headers": [(b"host", b"larder.house.internal:443")],
        }
    )
    assert not internal_access(request, Settings())
    settings = Settings(internal_base_url=" https://LARDER.house.internal:443/ ")
    assert settings.internal_base_url == "https://larder.house.internal"
    assert internal_access(request, settings)


@pytest.mark.parametrize(
    "method,path,allowed",
    [
        ("GET", "/v1/web/events", True),
        ("GET", "/v1/web/settings", True),
        ("PUT", "/v1/web/settings", False),
        ("POST", "/v1/web/shopping/scan", True),
        ("POST", "/v1/web/shopping/checkout", True),
        ("POST", "/v1/web/scanners/one/mode", True),
        ("POST", "/v1/web/intake-batches/one/confirm", True),
        ("POST", "/v1/web/units/one/remove", True),
        ("POST", "/v1/web/print-jobs", True),
        ("PUT", "/v1/web/products/one/stock-policy", False),
        ("PATCH", "/v1/web/stores/one", False),
        ("GET", "/v1/web/integrations", False),
        ("POST", "/v1/web/shopping/one/future-operation", False),
        ("POST", "/v1/web/maintenance/reset-inventory", False),
    ],
)
def test_internal_operation_allowlist(method, path, allowed):
    request = Request({"type": "http", "method": method, "path": path, "headers": []})
    assert internal_operation(request) is allowed


@pytest.mark.asyncio
async def test_guard_enforces_internal_http_boundary_and_preserves_request_body():
    internal_origin = "https://larder.house.internal"
    settings = Settings(
        oidc_enabled=True,
        oidc_issuer="https://auth.example.test/",
        oidc_client_id="test",
        oidc_client_secret="test",
        session_secret="test-secret",
        public_base_url="https://larder.example.test",
        internal_base_url=internal_origin,
    )
    app = FastAPI()
    install_web_auth(app, settings)

    @asynccontextmanager
    async def sessions():
        yield SimpleNamespace(commit=AsyncMock())

    app.state.session_factory = sessions

    @app.post("/v1/web/quick-add")
    async def add(request: Request):
        return await request.json()

    @app.put("/v1/web/settings")
    async def change_settings():
        pytest.fail("Anonymous settings mutation must never reach its handler")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=internal_origin,
        headers={"Origin": internal_origin, "Sec-Fetch-Site": "same-origin"},
    ) as client:
        response = await client.post("/v1/web/quick-add", json={"name": "Milk"})
        assert response.status_code == 200
        assert response.json() == {"name": "Milk"}
        for field in ("newStoreName", "new_store_name"):
            response = await client.post("/v1/web/quick-add", json={"name": "Milk", field: "Shop"})
            assert response.status_code == 401
        assert (await client.put("/v1/web/settings", json={})).status_code == 401
        assert (
            await client.post(
                "/v1/web/quick-add",
                json={},
                headers={
                    "Origin": "https://attacker.example",
                },
            )
        ).status_code == 403
        assert (
            await client.post(
                "/v1/web/quick-add",
                json={},
                headers={
                    "Sec-Fetch-Site": "cross-site",
                },
            )
        ).status_code == 403
        # The public destination stays protected even with forged forwarding.
        response = await client.post(
            "https://larder.example.test/v1/web/quick-add",
            json={},
            headers={
                "Origin": "https://larder.example.test",
                "X-Forwarded-Host": "larder.house.internal",
            },
        )
        assert response.status_code == 401
