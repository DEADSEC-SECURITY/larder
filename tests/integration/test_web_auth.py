import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from larder.api import create_app
from larder.config import Settings
from larder.models import WebSession
from larder.webapi.auth import SESSION_COOKIE, read_token, safe_next, sign_token

pytestmark = pytest.mark.integration

SECRET = "0123456789abcdef" * 4


def signed_in_settings(database_url: str, **overrides) -> Settings:
    return Settings(
        database_url=database_url,
        scan_api_token="test-token",
        oidc_enabled=True,
        oidc_issuer="https://auth.example.test/application/o/larder/",
        oidc_client_id="larder-web",
        oidc_client_secret="client-secret",
        session_secret=SECRET,
        public_base_url="https://larder.example.test",
        **overrides,
    )


def app_for(session_factory, settings: Settings):
    app = create_app(settings)
    app.state.settings = settings
    app.state.session_factory = session_factory
    return app


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://web-test")


async def make_session(session_factory, settings: Settings, **overrides) -> str:
    """Insert a live session row and return the cookie that addresses it."""
    session_id = uuid4()
    now = datetime.now(UTC)
    fields = {
        "subject": "ak-user-1",
        "claims": {
            "sub": "ak-user-1",
            "name": "Household Admin",
            "email": "admin@example.test",
            "username": "admin",
            "groups": ["larder-users"],
        },
        "refresh_token": None,
        "expires_at": now + timedelta(seconds=settings.session_ttl_seconds),
        "revalidate_after": None,
        "last_seen_at": now,
        "user_agent": "iPhone",
        **overrides,
    }
    async with session_factory() as session, session.begin():
        session.add(WebSession(id=session_id, **fields))
    return sign_token(
        {"sid": str(session_id), "exp": int(time.time()) + settings.session_ttl_seconds},
        settings.session_secret,
    )


@pytest.mark.asyncio
async def test_web_routes_are_open_when_sign_in_is_disabled(session_factory, database_url):
    settings = Settings(database_url=database_url, scan_api_token="test-token")
    async with client_for(app_for(session_factory, settings)) as client:
        assert (await client.get("/v1/web/overview")).status_code == 200
        assert (await client.get("/v1/web/auth/me")).json() == {
            "enabled": False,
            "authenticated": True,
            "user": None,
            "loginUrl": None,
        }


@pytest.mark.asyncio
async def test_guard_rejects_anonymous_and_forged_cookies(session_factory, database_url):
    settings = signed_in_settings(database_url)
    async with client_for(app_for(session_factory, settings)) as client:
        assert (await client.get("/v1/web/overview")).status_code == 401

        forged = {
            "garbage": "not-a-token",
            # Correct shape, wrong signing key.
            "wrong-key": sign_token(
                {"sid": str(uuid4()), "exp": int(time.time()) + 60}, "an-entirely-different-key"
            ),
            # Validly signed, but names a session that does not exist.
            "unknown-session": sign_token(
                {"sid": str(uuid4()), "exp": int(time.time()) + 60}, SECRET
            ),
            # Validly signed and expired.
            "expired": sign_token({"sid": str(uuid4()), "exp": int(time.time()) - 1}, SECRET),
        }
        for label, cookie in forged.items():
            client.cookies.set(SESSION_COOKIE, cookie)
            response = await client.get("/v1/web/overview")
            assert response.status_code == 401, label
            assert response.json()["loginUrl"] == "/v1/web/auth/login"


@pytest.mark.asyncio
async def test_guard_never_covers_the_machine_apis(session_factory, database_url):
    """The hardware scanner and printer bridge keep their own bearer token."""
    settings = signed_in_settings(database_url)
    async with client_for(app_for(session_factory, settings)) as client:
        unauthenticated = await client.post(
            "/v1/scans",
            json={
                "event_id": str(uuid4()),
                "scanner_code": "SCN01",
                "payload": "0012000161155",
            },
        )
        assert unauthenticated.status_code == 401

        # The bearer token still works with sign-in switched on.
        accepted = await client.post(
            "/v1/print-jobs/claim",
            json={"bridge_id": "bridge-1"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert accepted.status_code == 204


@pytest.mark.asyncio
async def test_valid_session_passes_and_logout_revokes_it(session_factory, database_url):
    settings = signed_in_settings(database_url)
    app = app_for(session_factory, settings)
    cookie = await make_session(session_factory, settings)

    async with client_for(app) as client:
        client.cookies.set(SESSION_COOKIE, cookie)
        assert (await client.get("/v1/web/overview")).status_code == 200

        me = (await client.get("/v1/web/auth/me")).json()
        assert me["authenticated"] is True
        assert me["user"]["email"] == "admin@example.test"

        sessions = (await client.get("/v1/web/auth/sessions")).json()["sessions"]
        assert [row["current"] for row in sessions] == [True]
        assert sessions[0]["userAgent"] == "iPhone"

        assert (await client.post("/v1/web/auth/logout")).status_code == 200
        # The cookie is cleared, but even replaying it must fail.
        client.cookies.set(SESSION_COOKIE, cookie)
        assert (await client.get("/v1/web/overview")).status_code == 401

    async with session_factory() as session:
        assert (await session.execute(select(WebSession.id))).scalars().all() == []


@pytest.mark.asyncio
async def test_cross_site_browser_write_is_rejected(session_factory, database_url):
    settings = signed_in_settings(database_url)
    app = app_for(session_factory, settings)
    app.state.oidc_discovery = (time.time() + 3600, {})
    cookie = await make_session(session_factory, settings)

    async with client_for(app) as client:
        client.cookies.set(SESSION_COOKIE, cookie)
        response = await client.post(
            "/v1/web/auth/logout",
            headers={
                "Origin": "https://attacker.example",
                "Sec-Fetch-Site": "cross-site",
            },
        )
        assert response.status_code == 403
        assert response.json() == {"detail": "cross-origin request rejected"}
        # The rejected request did not revoke the valid Larder session.
        assert (await client.get("/v1/web/overview")).status_code == 200

        allowed = await client.post(
            "/v1/web/auth/logout",
            headers={
                "Origin": "https://larder.example.test",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_logout_everywhere_drops_every_session_for_the_subject(
    session_factory, database_url
):
    settings = signed_in_settings(database_url)
    app = app_for(session_factory, settings)
    phone = await make_session(session_factory, settings, user_agent="iPhone")
    await make_session(session_factory, settings, user_agent="Windows")
    other_person = await make_session(session_factory, settings, subject="ak-user-2")

    async with client_for(app) as client:
        client.cookies.set(SESSION_COOKIE, phone)
        assert len((await client.get("/v1/web/auth/sessions")).json()["sessions"]) == 2
        assert (await client.post("/v1/web/auth/logout?everywhere=true")).status_code == 200

    async with session_factory() as session:
        remaining = (await session.execute(select(WebSession.subject))).scalars().all()
    # The other account is untouched.
    assert remaining == ["ak-user-2"]
    assert other_person


@pytest.mark.asyncio
async def test_expired_session_row_is_rejected_and_cleaned_up(session_factory, database_url):
    settings = signed_in_settings(database_url)
    app = app_for(session_factory, settings)
    # Cookie still in date, but the authoritative row has lapsed.
    cookie = await make_session(
        session_factory, settings, expires_at=datetime.now(UTC) - timedelta(minutes=1)
    )

    async with client_for(app) as client:
        client.cookies.set(SESSION_COOKIE, cookie)
        assert (await client.get("/v1/web/overview")).status_code == 401

    async with session_factory() as session:
        assert (await session.execute(select(WebSession.id))).scalars().all() == []


@pytest.mark.asyncio
async def test_login_redirects_to_the_provider_with_pkce(session_factory, database_url):
    settings = signed_in_settings(database_url)
    app = app_for(session_factory, settings)
    # Pre-seed the discovery cache so the test never reaches the network.
    app.state.oidc_discovery = (
        time.time() + 3600,
        {
            "authorization_endpoint": "https://auth.example.test/application/o/authorize/",
            "token_endpoint": "https://auth.example.test/application/o/token/",
            "userinfo_endpoint": "https://auth.example.test/application/o/userinfo/",
        },
    )

    async with client_for(app) as client:
        response = await client.get("/v1/web/auth/login?next=/inventory")
        assert response.status_code == 302
        location = response.headers["location"]
        assert location.startswith("https://auth.example.test/application/o/authorize/")
        assert "code_challenge_method=S256" in location
        assert "client_id=larder-web" in location
        assert (
            "redirect_uri=https%3A%2F%2Flarder.example.test%2Fapi%2Fv1%2Fweb%2Fauth%2Fcallback"
            in location
        )
        assert "offline_access" in location

        set_cookie = response.headers["set-cookie"].lower()
        assert "samesite=none" in set_cookie
        assert "secure" in set_cookie
        assert "httponly" in set_cookie

        # The PKCE verifier and state ride in a signed, short-lived cookie.
        transaction = read_token(
            response.cookies["larder_oidc_tx"], settings.session_secret
        )
        assert transaction["next"] == "/inventory"
        assert transaction["verifier"] and transaction["state"] and transaction["nonce"]


@pytest.mark.asyncio
async def test_callback_rejects_a_mismatched_state(session_factory, database_url):
    settings = signed_in_settings(database_url)
    app = app_for(session_factory, settings)
    transaction = sign_token(
        {
            "state": "the-real-state",
            "nonce": "n",
            "verifier": "v",
            "next": "/",
            "exp": int(time.time()) + 600,
        },
        SECRET,
    )

    async with client_for(app) as client:
        client.cookies.set("larder_oidc_tx", transaction)
        response = await client.get("/v1/web/auth/callback?code=abc&state=an-attackers-state")
        assert response.status_code == 302
        assert response.headers["location"].endswith("/?authError=state")

    async with session_factory() as session:
        assert (await session.execute(select(WebSession.id))).scalars().all() == []


def test_safe_next_refuses_offsite_redirects():
    assert safe_next("/inventory") == "/inventory"
    assert safe_next("/settings?tab=account") == "/settings?tab=account"
    for hostile in ("//evil.test", "https://evil.test", "javascript:alert(1)", None, ""):
        assert safe_next(hostile) == "/"


def test_session_cookies_are_tamper_evident():
    token = sign_token({"sid": "abc", "exp": int(time.time()) + 60}, SECRET)
    assert read_token(token, SECRET)["sid"] == "abc"
    assert read_token(token, "another-secret-entirely") is None
    assert read_token(token[:-2] + ("AA" if not token.endswith("AA") else "BB"), SECRET) is None
    assert read_token(sign_token({"exp": int(time.time()) - 1}, SECRET), SECRET) is None
    assert read_token(None, SECRET) is None
    # An empty secret must never validate anything.
    assert read_token(sign_token({"exp": int(time.time()) + 60}, ""), "") is None
