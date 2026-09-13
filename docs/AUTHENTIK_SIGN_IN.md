# Authentik sign-in

Larder is a household appliance: it has no user table, no password, and no
registration. When it is reachable only on the LAN that is fine. Once it is
published, `LARDER_OIDC_ENABLED=true` puts Authentik in front of the whole web
API and gives the UI an identity to show.

Sign-in is **off by default**. With it off, `/auth/me` reports an open door and
nothing is gated, so local development and existing internal deployments are
unaffected.

## What is and is not protected

| Surface | Protected by |
| --- | --- |
| `/v1/web/*` on the public origin | Authentik session cookie |
| Everyday web operations on the configured internal origin | Private network access |
| Internal settings writes, integrations, and maintenance | Authentik session cookie |
| `/v1/web/auth/*` | nothing — these must be reachable while signed out |
| `/healthz` | nothing — container health check |
| `/v1/scans` | `LARDER_SCAN_API_TOKEN` (hardware scanner) |
| `/v1/print-jobs/*` | `LARDER_SCAN_API_TOKEN` (printer bridge) |

The machine endpoints keep their bearer token and are not meant to be published.
Only the web origin goes through the tunnel.

## Internal access without login

Set `LARDER_INTERNAL_BASE_URL=https://larder.example.internal` alongside the
existing OIDC settings. Leave it empty to require sign-in on all web origins.
This is an exact hostname allowlist, not a rule for every `.internal` address.
Pass this variable through the production Compose environment as well.

On this private origin, inventory, shopping, scan intake, printing, activity,
and live updates work without login. The reference data these screens need
(including household preferences) is readable. Settings writes, catalog rules,
stock policies, integrations, and maintenance require a valid session. Creating
a store through quick-add also requires sign-in. New API operations require
authentication unless explicitly added to the internal operation allowlist.

The Settings screen shows **Sign in to change settings** until authenticated.
The live-update watchdog does not redirect anonymous internal users during a
connection interruption. `/auth/me` reports `internalAccess` separately from
`authenticated`; a local guest is never represented as a signed-in user.

Internal sign-in starts on `LARDER_PUBLIC_BASE_URL` and returns to the requested
page there. This keeps the transaction cookie on the same origin as the
registered OIDC callback, so no additional Authentik redirect URI is needed.

The reverse proxy is part of this access boundary. Only the private listener
may forward the internal Host. The public tunnel must use its separate upstream
route and **overwrite Host with the public hostname**. Larder ignores
`X-Forwarded-Host` and `Forwarded` when granting internal access. Do not expose
the API or a catch-all proxy directly to the internet, where a caller could
supply the private Host. Keep the public tunnel's `.internal` upstream address
distinct from `LARDER_INTERNAL_BASE_URL`.

Browser writes must have the same Origin as the destination (internal or public)
and same-origin Fetch Metadata. Verify internal anonymous reads and routine
writes, anonymous settings rejection, public anonymous rejection (including
forged forwarded-host headers), and successful authenticated settings writes
before releasing a proxy or authentication change.

## Flow

Authorization code with PKCE. The app never sees a password.

1. The SPA gets a `401`, or the user clicks Sign in, and the browser navigates
   to `/api/v1/web/auth/login`. This is a real navigation, not an XHR — the
   service worker is configured to leave `/api/**` alone precisely so this is
   not swallowed and answered with the app shell.
2. Larder stores `state`, `nonce`, and the PKCE verifier in a short-lived signed
   cookie and redirects to Authentik.
3. Authentik redirects back to `/api/v1/web/auth/callback`. Larder checks
   `state`, exchanges the code with its client secret, then reads claims from
   the `userinfo` endpoint.
4. `iss`, `aud`, and `nonce` are validated. The ID token's signature is not
   checked locally, which OIDC Core 3.1.3.7 permits for a confidential client
   reading the token directly off the token endpoint over TLS — that is why no
   JWT library is a dependency.
5. A `web_sessions` row is created and its id goes into an HttpOnly cookie.

## Home Assistant iframe support

Secure deployments mark the OIDC transaction and Larder session cookies
`SameSite=None; Secure`. This is required when Home Assistant embeds Larder
from a different site: `SameSite=Lax` drops the transaction cookie on the
embedded callback, which otherwise produces an `authError=expired` redirect
loop. Local HTTP development retains `SameSite=Lax`, because browsers reject
`SameSite=None` cookies without `Secure`.

Allowing cookies in an embedded context does not allow cross-site writes.
Session-authenticated `POST`, `PUT`, `PATCH`, and `DELETE` requests under
`/v1/web/` reject a mismatched `Origin` or cross-site Fetch Metadata value.
Requests from Larder's own document remain same-origin even while the document
is inside Home Assistant.

After any callback error, the SPA stops automatic redirects and leaves an
explicit **Sign in** retry. This prevents a blocked-cookie browser from
flickering indefinitely. Browsers configured to block all third-party cookies
may still require Larder and Home Assistant to be opened under the same site or
Larder to be used as a top-level page.

## Sessions last a year

`LARDER_SESSION_TTL_SECONDS` defaults to 365 days. An installed phone app that
demands a re-login mid-shop is worse than useless, and this is a household
inventory, not a bank.

That is only safe because the session is server-side:

- the cookie carries an opaque session id, nothing else;
- the refresh token never reaches the browser;
- any session can be revoked by deleting its row;
- **Settings -> Preferences -> Signed-in devices** lists them, with
  "Sign out everywhere".

## Password changes end sessions

Every `LARDER_SESSION_REVALIDATE_SECONDS` (default 24h) the next request on a
session exchanges its refresh token with Authentik. If Authentik rejects it —
which is what happens when the password changes or an admin revokes access —
the row is deleted and the browser is sent back to sign in. Group membership is
re-read at the same time, so removing someone from `LARDER_OIDC_ALLOWED_GROUPS`
also takes effect within that window.

Two things to know:

- **`offline_access` is required.** Without it Authentik issues no refresh
  token, revalidation is skipped, and a session simply lives out its full year.
  Larder logs `oidc.no_refresh_token` at sign-in when this happens.
- **Revocation is not instant.** The window is bounded by
  `LARDER_SESSION_REVALIDATE_SECONDS`. Lower it if that matters more than the
  extra round-trips; to cut every session off immediately, delete from
  `web_sessions` directly.

A provider that is merely *unreachable* does not sign anyone out — that would
turn an Authentik outage into a total lockout. Only an explicit rejection does.

## Configuration

See `.env.example` for the full list. The two that are easy to get wrong:

- `LARDER_OIDC_ISSUER` needs its **trailing slash**; it is compared verbatim
  against the `iss` claim.
- The redirect URI defaults to
  `{LARDER_PUBLIC_BASE_URL}/api/v1/web/auth/callback` — note the `/api` prefix,
  which nginx strips before the API sees it. It must match the provider exactly.
  Override with `LARDER_OIDC_REDIRECT_URL` only if your ingress differs.

`LARDER_SESSION_SECRET` is a local HMAC key (`openssl rand -hex 32`), not the
OIDC client secret. Rotating it invalidates every cookie.

The reverse proxy and identity provider can live on the same host as Larder or
elsewhere on the network. Keep the client secret outside the repository and
register the callback URL exactly as shown above.
