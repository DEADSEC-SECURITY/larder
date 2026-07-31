# Larder

Larder is a self-hosted household inventory and shopping system built around barcodes. It keeps track of what is at home, what is running low, what belongs on the shopping list, and what was actually bought.

The project started with a simple goal: scanning a product should be quick even when an external catalog is slow or unavailable. Larder records the scan in PostgreSQL first, then enriches the product in the background with Open Food Facts or UPCitemdb. A temporary catalog problem never has to hold up the person standing in the kitchen.

Larder includes a FastAPI backend, an Angular web interface, a PostgreSQL schema managed by Alembic, a scanner daemon, and optional bridges for remote barcode scanners and receipt or label printers. The web interface can be installed on a phone as a progressive web app.

## What it can do

Inventory is tracked as individual units with product, store, price, expiration, and history. Products can be grouped when several brands or package sizes satisfy the same household need. Shopping mode supports repeated scans for quantities, unplanned purchases, store selection, and checkout directly into inventory.

The application also includes product enrichment, nutrition details, brand management, store routing rules, tax profiles, expiration policies, printable control barcodes, live browser updates, and an append-only inventory event ledger. Scanner submissions use stable event IDs, so retrying after a network or container restart does not apply the same scan twice.

Authentication is optional. Local installations can run without a sign-in screen. Public or shared installations can use an OpenID Connect provider such as Authentik while the hardware scan and print APIs continue to use bearer tokens.

## Quick start

You need Docker with Compose support. Copy the example environment file and replace the development passwords and contact address before exposing the service beyond your own machine.

```bash
cp .env.example .env
docker compose up --build -d
docker compose ps
```

On PowerShell, use `Copy-Item .env.example .env` for the first command.

The web interface opens at `http://127.0.0.1:8081`. The API documentation is at `http://127.0.0.1:8080/docs`. PostgreSQL only listens on the local machine by default.

Database migrations run as a separate one-time Compose service. The API and daemon wait for that service to finish successfully before they start.

To register a scanner, run:

```bash
docker compose exec daemon larder scanner-add SCN01 "Kitchen scanner" \
  --device-path /dev/serial/by-id/usb-example-scanner
```

Copy `compose.hardware.yaml.example` to a local Compose override and replace the example device path with the stable path for your scanner. Most keyboard-style scanners can send the barcode followed by Enter. Larder also understands framed input such as `LDR:SCN01|4006381333931` from a bridge that already knows its scanner identity.

## Windows scanner setup

Docker Desktop cannot pass a Windows COM port directly into a Linux container. Leave PostgreSQL, the API, and the web interface in Compose, stop the containerized daemon, then run the daemon from a Windows virtual environment.

```powershell
docker compose stop daemon
$env:LARDER_DATABASE_URL = "postgresql+asyncpg://larder:replace-this@127.0.0.1:5432/larder"
$env:LARDER_HOUSEHOLD_TIMEZONE = "America/New_York"
$env:LARDER_SCANNERS__SCN01__DEVICE = "COM3"
$env:LARDER_SCANNERS__SCN01__BAUDRATE = "9600"
.\.venv-win\Scripts\larder.exe scanner-test --device COM3 --scanner-code SCN01
.\.venv-win\Scripts\larder.exe daemon
```

Replace `COM3` with the port shown in Windows Device Manager.

## Web development

The Python backend lives in `src/larder`. The Angular application lives in `apps/web` and is part of the root pnpm workspace.

```bash
pnpm install
pnpm --filter web start
pnpm --filter web run build
pnpm --filter web test
```

The development server runs on port 4200 and proxies `/api` requests to the local FastAPI service. Production builds are served by Nginx. Fonts are included in the web bundle, so the interface does not depend on a third-party font service.

The browser receives live updates through server-sent events. PostgreSQL notifications tell the API when inventory, enrichment, settings, or shopping data changes, and the open screen refreshes the relevant data.

## Python development

Larder requires Python 3.12 or newer.

```bash
python -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest tests/unit
.venv/bin/pytest -m integration
```

On Windows, the executables are under `.venv\Scripts`. Integration tests use Testcontainers and require a running Docker daemon.

Useful operator commands include:

```bash
larder healthcheck
larder enrich --barcode 4006381333931
larder retry-enrichment --failed
larder reconcile-stock-policies
larder validate-invariants --json
larder scanner-test --device /dev/serial/by-id/...
```

## Sign-in and reverse proxies

OpenID Connect is disabled by default. The required settings and callback behavior are documented in [docs/AUTHENTIK_SIGN_IN.md](docs/AUTHENTIK_SIGN_IN.md). The example uses Authentik, but the application follows standard discovery, authorization code, PKCE, and refresh token flows.

If Larder is placed behind a reverse proxy, keep the web application and API on the same browser origin. The frontend calls relative `/api` paths, and the callback URL must match the URL registered with the identity provider exactly.

Do not commit `.env` files, database passwords, scan API tokens, client secrets, or session keys. The example values are for local development only.

## Project notes

More detailed behavior is recorded in the `docs` directory. These documents cover stock groups, brands and store routing, household inventory, taxes, intake, and authentication. Some files are implementation handoffs and are more detailed than the main README.

Bug reports and focused pull requests are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a change. Security issues should be reported using the process in [SECURITY.md](SECURITY.md).

Larder is licensed under the Apache License 2.0. See [LICENSE](LICENSE).
