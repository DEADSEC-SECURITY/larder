"""Authentik (OpenID Connect) sign-in for the web UI.

Authorization-code flow with PKCE against Authentik. Deliberately
dependency-free: `hmac`/`hashlib`/`secrets` cover the cookie, and the ID token
needs no local signature check because it is read straight off the token
endpoint over TLS with the client secret — the confidential-client carve-out in
OIDC Core 3.1.3.7. Issuer, audience, expiry, and nonce are still validated.

Sessions live a year by default so an installed phone app never interrupts a
shop to re-authenticate. That is only safe because the session is a server-side
row (`web_sessions`): the cookie carries an opaque id and nothing else, the
refresh token never reaches the browser, and every session is re-proved against
Authentik on `session_revalidate_seconds`. When the password changes Authentik
revokes the refresh token, the next revalidation fails, and the row is deleted.

Scope note: this guards `/v1/web/*` only. `/v1/scans` and `/v1/print-jobs/*` are
machine endpoints that keep their bearer token and stay off the public origin.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

import httpx
import structlog
from fastapi import Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from larder.config import Settings
from larder.models import WebSession
from larder.webapi.common import web_api_router

log = structlog.get_logger(__name__)

router = web_api_router(tags=["auth"])

SESSION_COOKIE = "larder_session"
# Short-lived holder for state/nonce/PKCE verifier between login and callback.
TRANSACTION_COOKIE = "larder_oidc_tx"
TRANSACTION_TTL_SECONDS = 600
DISCOVERY_TTL_SECONDS = 3600
AUTH_PATH_PREFIX = "/v1/web/auth/"
LOGIN_PATH = "/v1/web/auth/login"


# --------------------------------------------------------------------------
# Signed cookie values
# --------------------------------------------------------------------------


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign_token(payload: dict[str, Any], secret: str) -> str:
    body = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    mac = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64encode(mac)}"


def read_token(token: str | None, secret: str) -> dict[str, Any] | None:
    """Verify signature and expiry. Returns None for anything untrustworthy."""
    if not token or not secret or token.count(".") != 1:
        return None
    body, mac = token.split(".")
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(expected, _b64decode(mac)):
            return None
        payload = json.loads(_b64decode(body))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    expires = payload.get("exp")
    if not isinstance(expires, int | float) or expires < time.time():
        return None
    return payload


def decode_jwt_claims(token: str) -> dict[str, Any]:
    """Read a JWT payload WITHOUT verifying its signature.

    Only ever called on a token handed back by the token endpoint over an
    authenticated TLS channel; see the module docstring.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("malformed JWT")
    claims = json.loads(_b64decode(parts[1]))
    if not isinstance(claims, dict):
        raise ValueError("JWT payload is not an object")
    return claims


def safe_next(target: str | None) -> str:
    """Only same-origin absolute paths — never an attacker-supplied redirect."""
    if not isinstance(target, str) or not target.startswith("/") or target.startswith("//"):
        return "/"
    return target


# --------------------------------------------------------------------------
# Provider plumbing
# --------------------------------------------------------------------------


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _cookie_kwargs(settings: Settings) -> dict[str, Any]:
    return {
        "httponly": True,
        "secure": settings.session_cookie_secure,
        # Home Assistant renders Larder in an iframe. When HA is reached on a
        # different site, both the OIDC callback and later API requests are a
        # third-party context; Lax cookies disappear and the SPA loops through
        # login -> expired callback -> 401 forever. None is valid only with
        # Secure, so local HTTP development deliberately keeps Lax.
        "samesite": "none" if settings.session_cookie_secure else "lax",
        "path": "/",
    }


def _delete_cookie(response: Response, name: str, settings: Settings) -> None:
    kwargs = _cookie_kwargs(settings)
    response.delete_cookie(
        name,
        path=kwargs["path"],
        secure=kwargs["secure"],
        httponly=kwargs["httponly"],
        samesite=kwargs["samesite"],
    )


def _trusted_browser_write(request: Request, settings: Settings) -> bool:
    """Keep SameSite=None from turning session-authenticated writes into CSRF."""
    configured_url = settings.public_base_url.strip() or settings.oidc_callback_url
    configured = urlsplit(configured_url)
    expected_origin = f"{configured.scheme}://{configured.netloc}"
    origin = (request.headers.get("origin") or "").rstrip("/")
    if origin and origin != expected_origin:
        return False

    # Requests made by Larder's own document remain same-origin even when that
    # document is embedded by Home Assistant. Cross-site form/fetch attempts do
    # not. Older clients omit this header, so Origin remains the primary check.
    fetch_site = (request.headers.get("sec-fetch-site") or "").lower()
    return fetch_site in {"", "same-origin", "none"}


async def discovery_document(app, settings: Settings) -> dict[str, Any]:
    cached = getattr(app.state, "oidc_discovery", None)
    if cached is not None and cached[0] > time.time():
        return cached[1]

    url = settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=settings.oidc_http_timeout_seconds) as client:
        response = await client.get(url)
        response.raise_for_status()
    document = response.json()
    app.state.oidc_discovery = (time.time() + DISCOVERY_TTL_SECONDS, document)
    return document


def _user_payload(claims: dict[str, Any]) -> dict[str, Any]:
    groups = claims.get("groups")
    return {
        "sub": claims.get("sub"),
        "name": claims.get("name") or claims.get("preferred_username"),
        "email": claims.get("email"),
        "username": claims.get("preferred_username"),
        "groups": [str(g) for g in groups] if isinstance(groups, list) else [],
    }


def _group_allowed(settings: Settings, user: dict[str, Any]) -> bool:
    allowed = settings.oidc_allowed_group_set
    return not allowed or bool(allowed.intersection(user.get("groups") or []))


async def _fetch_userinfo(
    client: httpx.AsyncClient, document: dict[str, Any], access_token: str
) -> dict[str, Any]:
    endpoint = document.get("userinfo_endpoint")
    if not endpoint or not access_token:
        return {}
    response = await client.get(
        endpoint, headers={"Authorization": f"Bearer {access_token}"}
    )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def _error_redirect(settings: Settings, code: str) -> RedirectResponse:
    """Bounce back to the SPA, which surfaces ?authError= as a toast."""
    base = settings.public_base_url.strip().rstrip("/")
    response = RedirectResponse(f"{base}/?authError={code}", status_code=302)
    _delete_cookie(response, TRANSACTION_COOKIE, settings)
    return response


# --------------------------------------------------------------------------
# Session rows
# --------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


async def load_session(
    db: AsyncSession, cookie: str | None, settings: Settings
) -> WebSession | None:
    """Resolve the cookie to a live session row, or None."""
    payload = read_token(cookie, settings.session_secret)
    if payload is None:
        return None
    try:
        session_id = UUID(str(payload.get("sid")))
    except (TypeError, ValueError):
        return None

    row = await db.get(WebSession, session_id)
    if row is None:
        return None
    if row.expires_at <= _now():
        await db.delete(row)
        return None
    return row


async def revalidate(app, db: AsyncSession, row: WebSession, settings: Settings) -> bool:
    """Re-prove the account against Authentik using the stored refresh token.

    Returns False when the session must be destroyed — the refresh token was
    revoked (password change, admin action) or the user lost group access.
    """
    if row.refresh_token is None:
        # No offline_access grant, so liveness cannot be checked. The session
        # still expires on its own; the operator was warned at sign-in.
        return True

    try:
        document = await discovery_document(app, settings)
        async with httpx.AsyncClient(timeout=settings.oidc_http_timeout_seconds) as client:
            response = await client.post(
                document["token_endpoint"],
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": row.refresh_token,
                    "client_id": settings.oidc_client_id,
                },
                auth=(settings.oidc_client_id, settings.oidc_client_secret),
                headers={"Accept": "application/json"},
            )
            if response.status_code >= 400:
                log.info(
                    "oidc.revalidation_rejected",
                    subject=row.subject,
                    status=response.status_code,
                )
                return False
            tokens = response.json()
            claims = await _fetch_userinfo(client, document, tokens.get("access_token", ""))
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        # Provider unreachable is not proof of revocation; keep the session and
        # retry on the next request rather than signing everyone out.
        log.warning("oidc.revalidation_unavailable", error=str(exc))
        return True

    if isinstance(tokens.get("refresh_token"), str):
        row.refresh_token = tokens["refresh_token"]
    if claims:
        user = _user_payload({**row.claims, **claims})
        if not _group_allowed(settings, user):
            log.info("oidc.group_revoked", subject=row.subject)
            return False
        row.claims = user
    row.revalidate_after = _now() + timedelta(seconds=settings.session_revalidate_seconds)
    return True


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.get("/auth/me")
async def me(request: Request) -> dict:
    settings = _settings(request)
    if not settings.oidc_enabled:
        # Sign-in is off: report an open door so the SPA gates nothing.
        return {"enabled": False, "authenticated": True, "user": None, "loginUrl": None}

    user = getattr(request.state, "user", None)
    if user is None:
        factory = request.app.state.session_factory
        async with factory() as db:
            row = await load_session(db, request.cookies.get(SESSION_COOKIE), settings)
            user = row.claims if row else None
            await db.commit()
    return {
        "enabled": True,
        "authenticated": user is not None,
        "user": user,
        "loginUrl": LOGIN_PATH,
    }


@router.get("/auth/login")
async def login(request: Request, next_url: str = Query(default="/", alias="next")) -> Response:
    settings = _settings(request)
    if not settings.oidc_enabled:
        return JSONResponse({"detail": "sign-in is not configured"}, status_code=404)

    try:
        document = await discovery_document(request.app, settings)
        authorize_endpoint = document["authorization_endpoint"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        log.warning("oidc.discovery_failed", error=str(exc))
        return JSONResponse({"detail": "identity provider is unreachable"}, status_code=503)

    verifier = secrets.token_urlsafe(64)
    challenge = _b64encode(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)

    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.oidc_client_id,
            "redirect_uri": settings.oidc_callback_url,
            "scope": settings.oidc_scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    response = RedirectResponse(f"{authorize_endpoint}?{query}", status_code=302)
    response.set_cookie(
        TRANSACTION_COOKIE,
        sign_token(
            {
                "state": state,
                "nonce": nonce,
                "verifier": verifier,
                "next": safe_next(next_url),
                "exp": int(time.time()) + TRANSACTION_TTL_SECONDS,
            },
            settings.session_secret,
        ),
        max_age=TRANSACTION_TTL_SECONDS,
        **_cookie_kwargs(settings),
    )
    return response


@router.get("/auth/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    settings = _settings(request)
    if not settings.oidc_enabled:
        return JSONResponse({"detail": "sign-in is not configured"}, status_code=404)

    if error:
        log.warning("oidc.provider_error", error=error)
        return _error_redirect(settings, "provider")

    transaction = read_token(request.cookies.get(TRANSACTION_COOKIE), settings.session_secret)
    if transaction is None or not code or not state:
        return _error_redirect(settings, "expired")
    if not hmac.compare_digest(str(transaction.get("state", "")), state):
        log.warning("oidc.state_mismatch")
        return _error_redirect(settings, "state")

    try:
        document = await discovery_document(request.app, settings)
        async with httpx.AsyncClient(timeout=settings.oidc_http_timeout_seconds) as client:
            token_response = await client.post(
                document["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.oidc_callback_url,
                    "client_id": settings.oidc_client_id,
                    "code_verifier": transaction["verifier"],
                },
                auth=(settings.oidc_client_id, settings.oidc_client_secret),
                headers={"Accept": "application/json"},
            )
            token_response.raise_for_status()
            tokens = token_response.json()

            claims: dict[str, Any] = {}
            if isinstance(tokens.get("id_token"), str):
                claims = decode_jwt_claims(tokens["id_token"])
            # userinfo is the authoritative claim source; Authentik puts group
            # membership there rather than in the ID token by default.
            userinfo = await _fetch_userinfo(client, document, tokens.get("access_token", ""))
            claims = {**claims, **userinfo}
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        log.warning("oidc.exchange_failed", error=str(exc))
        return _error_redirect(settings, "exchange")

    issuer = str(claims.get("iss", "")).rstrip("/")
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if issuer and issuer != settings.oidc_issuer.rstrip("/"):
        log.warning("oidc.issuer_mismatch", issuer=issuer)
        return _error_redirect(settings, "issuer")
    if audience is not None and settings.oidc_client_id not in audiences:
        log.warning("oidc.audience_mismatch")
        return _error_redirect(settings, "audience")
    if claims.get("nonce") is not None and not hmac.compare_digest(
        str(claims["nonce"]), str(transaction.get("nonce", ""))
    ):
        log.warning("oidc.nonce_mismatch")
        return _error_redirect(settings, "nonce")

    user = _user_payload(claims)
    if not user["sub"]:
        return _error_redirect(settings, "claims")
    if not _group_allowed(settings, user):
        log.warning("oidc.group_denied", subject=user["sub"])
        return _error_redirect(settings, "forbidden")

    refresh_token = tokens.get("refresh_token")
    if not isinstance(refresh_token, str):
        refresh_token = None
        log.warning(
            "oidc.no_refresh_token",
            detail="add offline_access to LARDER_OIDC_SCOPES so sessions can be revalidated",
        )

    now = _now()
    session_id = uuid4()
    factory = request.app.state.session_factory
    async with factory() as db:
        db.add(
            WebSession(
                id=session_id,
                subject=user["sub"],
                claims=user,
                refresh_token=refresh_token,
                expires_at=now + timedelta(seconds=settings.session_ttl_seconds),
                revalidate_after=(
                    now + timedelta(seconds=settings.session_revalidate_seconds)
                    if refresh_token
                    else None
                ),
                last_seen_at=now,
                user_agent=(request.headers.get("user-agent") or "")[:500] or None,
            )
        )
        await db.commit()

    base = settings.public_base_url.strip().rstrip("/")
    response = RedirectResponse(f"{base}{safe_next(transaction.get('next'))}", status_code=302)
    response.set_cookie(
        SESSION_COOKIE,
        sign_token(
            {"sid": str(session_id), "exp": int(now.timestamp()) + settings.session_ttl_seconds},
            settings.session_secret,
        ),
        max_age=settings.session_ttl_seconds,
        **_cookie_kwargs(settings),
    )
    _delete_cookie(response, TRANSACTION_COOKIE, settings)
    log.info("oidc.signed_in", subject=user["sub"])
    return response


@router.post("/auth/logout")
async def logout(request: Request, everywhere: bool = False) -> JSONResponse:
    settings = _settings(request)
    end_session_url: str | None = None

    if settings.oidc_enabled:
        factory = request.app.state.session_factory
        async with factory() as db:
            row = await load_session(db, request.cookies.get(SESSION_COOKIE), settings)
            if row is not None:
                if everywhere:
                    await db.execute(delete(WebSession).where(WebSession.subject == row.subject))
                else:
                    await db.delete(row)
            await db.commit()

        try:
            document = await discovery_document(request.app, settings)
            endpoint = document.get("end_session_endpoint")
            if endpoint:
                base = settings.public_base_url.strip().rstrip("/")
                query = urlencode(
                    {
                        "client_id": settings.oidc_client_id,
                        **({"post_logout_redirect_uri": f"{base}/"} if base else {}),
                    }
                )
                end_session_url = f"{endpoint}?{query}"
        except (httpx.HTTPError, ValueError) as exc:
            # The local session is already gone; only the IdP round-trip is lost.
            log.warning("oidc.discovery_failed", error=str(exc))

    response = JSONResponse({"endSessionUrl": end_session_url})
    _delete_cookie(response, SESSION_COOKIE, settings)
    _delete_cookie(response, TRANSACTION_COOKIE, settings)
    return response


@router.get("/auth/sessions")
async def list_sessions(request: Request) -> dict:
    """Other browsers signed in as the same user — year-long sessions deserve a list."""
    settings = _settings(request)
    if not settings.oidc_enabled:
        return {"sessions": []}

    factory = request.app.state.session_factory
    async with factory() as db:
        current = await load_session(db, request.cookies.get(SESSION_COOKIE), settings)
        if current is None:
            await db.commit()
            return {"sessions": []}
        rows = (
            await db.execute(
                select(WebSession)
                .where(WebSession.subject == current.subject)
                .order_by(WebSession.created_at.desc())
            )
        ).scalars().all()
        payload = [
            {
                "id": str(row.id),
                "current": row.id == current.id,
                "createdAt": row.created_at.isoformat(),
                "lastSeenAt": row.last_seen_at.isoformat() if row.last_seen_at else None,
                "expiresAt": row.expires_at.isoformat(),
                "userAgent": row.user_agent,
            }
            for row in rows
        ]
        await db.commit()
    return {"sessions": payload}


# --------------------------------------------------------------------------
# Guard
# --------------------------------------------------------------------------


def install_web_auth(application, settings: Settings) -> None:
    """Require a live session on /v1/web/* when sign-in is enabled."""

    unauthorized = {"detail": "authentication required", "loginUrl": LOGIN_PATH}

    @application.middleware("http")
    async def require_session(request: Request, call_next):
        if not settings.oidc_enabled:
            return await call_next(request)

        path = request.url.path
        if (
            path.startswith("/v1/web/")
            and request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and not _trusted_browser_write(request, settings)
        ):
            return JSONResponse({"detail": "cross-origin request rejected"}, status_code=403)

        # Machine APIs (/v1/scans, /v1/print-jobs) keep their bearer token, and
        # the auth routes themselves must stay reachable while signed out.
        if not path.startswith("/v1/web/") or path.startswith(AUTH_PATH_PREFIX):
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)

        factory = request.app.state.session_factory
        async with factory() as db:
            row = await load_session(db, request.cookies.get(SESSION_COOKIE), settings)
            if row is None:
                await db.commit()
                return JSONResponse(unauthorized, status_code=401)

            now = _now()
            if row.revalidate_after is not None and row.revalidate_after <= now:
                if not await revalidate(request.app, db, row, settings):
                    await db.delete(row)
                    await db.commit()
                    response = JSONResponse(unauthorized, status_code=401)
                    _delete_cookie(response, SESSION_COOKIE, settings)
                    return response

            # Cheap enough to keep roughly current without writing every request.
            if row.last_seen_at is None or (now - row.last_seen_at) > timedelta(hours=1):
                row.last_seen_at = now
            user = dict(row.claims)
            await db.commit()

        request.state.user = user
        return await call_next(request)
