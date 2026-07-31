from __future__ import annotations

from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScannerDeviceSettings(BaseSettings):
    device: str
    baudrate: int = Field(default=9600, gt=0)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LARDER_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://larder:larder@localhost:5432/larder"
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8080, ge=1, le=65535)
    scan_api_token: str = "larder-development-only"
    household_timezone: str = "UTC"
    log_level: str = "INFO"
    queue_size: int = Field(default=500, gt=0)
    max_frame_bytes: int = Field(default=128, ge=32, le=4096)
    accept_invalid_check_digit: bool = False
    fallback_category_id: str = "local:uncategorized"
    currency_code: str = "USD"
    web_cors_origins: str = "http://localhost:4200"
    off_base_url: str = "https://world.openfoodfacts.org"
    off_api_version: str = "v3.6"
    off_user_agent: str = "Larder/0.1 (larder@example.invalid)"
    off_requests_per_minute: int = Field(default=12, ge=1, le=15)
    off_connect_timeout_seconds: float = Field(default=5, gt=0)
    off_total_timeout_seconds: float = Field(default=15, gt=0)
    upcitemdb_enabled: bool = True
    upcitemdb_base_url: str = "https://api.upcitemdb.com/prod/trial"
    upcitemdb_requests_per_minute: int = Field(default=6, ge=1, le=30)
    upcitemdb_user_key: str | None = None
    upcitemdb_key_type: str = "3scale"
    enrichment_lease_seconds: int = Field(default=300, ge=30)
    ziptax_api_key: str | None = None
    ziptax_base_url: str = "https://api.zip-tax.com/request/v60"
    tax_lookup_timeout_seconds: float = Field(default=15, gt=0, le=60)
    worker_id: str = "larder-1"
    scanners: dict[str, ScannerDeviceSettings] = Field(default_factory=dict)

    # --- Authentik / OpenID Connect sign-in for the web UI ---
    # Off by default: local `docker compose up` and the existing internal
    # deployment keep working untouched. The hardware scan API (/v1/scans) is
    # never covered by this and keeps its bearer token.
    oidc_enabled: bool = False
    # Authentik provider issuer, for example:
    # https://auth.example.com/application/o/larder/
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    # offline_access is what makes Authentik issue a refresh token. Without it
    # the year-long session still works but can no longer be revalidated, so a
    # password change would not sign the user out until the session expires.
    oidc_scopes: str = "openid profile email offline_access"
    # Comma-separated Authentik group names. Empty = any user Authentik lets
    # through the application's own policies.
    oidc_allowed_groups: str = ""
    # Public origin the browser reaches, for example https://larder.example.com
    public_base_url: str = ""
    # Defaults to {public_base_url}/api/v1/web/auth/callback — nginx proxies
    # /api/ to the API and strips the prefix. Must match Authentik exactly.
    oidc_redirect_url: str = ""
    oidc_http_timeout_seconds: float = Field(default=10, gt=0, le=60)
    # HMAC key for the session cookie. Rotating it signs everyone out.
    session_secret: str = ""
    # A year: an installed phone app should never interrupt a shop to re-auth.
    # Safe because sessions are server-side rows that can be revoked, and are
    # revalidated against Authentik on the interval below.
    session_ttl_seconds: int = Field(default=60 * 60 * 24 * 365, ge=300)
    # How long a signed-in browser may go without proving the account is still
    # valid. Bounds how late a password change or group removal signs it out.
    session_revalidate_seconds: int = Field(default=60 * 60 * 24, ge=60)
    # Allows http:// origins for local testing; production must stay True.
    session_cookie_secure: bool = True

    @field_validator("household_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {value}") from exc
        return value

    @field_validator("off_user_agent")
    @classmethod
    def validate_off_user_agent(cls, value: str) -> str:
        if "/" not in value or "(" not in value or not value.endswith(")"):
            raise ValueError("OFF User-Agent must look like AppName/Version (contact)")
        return value

    @model_validator(mode="after")
    def validate_oidc(self) -> Settings:
        """Fail fast on a half-configured provider rather than at first sign-in."""
        if not self.oidc_enabled:
            return self
        missing = [
            name
            for name in (
                "oidc_issuer",
                "oidc_client_id",
                "oidc_client_secret",
                "session_secret",
            )
            if not getattr(self, name).strip()
        ]
        if not self.oidc_redirect_url.strip() and not self.public_base_url.strip():
            missing.append("public_base_url (or oidc_redirect_url)")
        if missing:
            joined = ", ".join(f"LARDER_{name.upper()}" for name in missing)
            raise ValueError(f"oidc_enabled requires: {joined}")
        return self

    @property
    def oidc_allowed_group_set(self) -> frozenset[str]:
        return frozenset(g.strip() for g in self.oidc_allowed_groups.split(",") if g.strip())

    @property
    def oidc_callback_url(self) -> str:
        """Exact redirect_uri; must be registered on the Authentik provider."""
        if self.oidc_redirect_url.strip():
            return self.oidc_redirect_url.strip()
        # nginx proxies /api/ to the API and strips the prefix, so the browser
        # sees the callback one level deeper than the API route.
        return f"{self.public_base_url.strip().rstrip('/')}/api/v1/web/auth/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()
