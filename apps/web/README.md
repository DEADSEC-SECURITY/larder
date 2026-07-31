# Larder web interface

This directory contains the Angular frontend for Larder. It uses the FastAPI backend through the `/v1/web` API. When the API is running locally, its interactive documentation is available at `http://127.0.0.1:8080/docs`.

The interface covers inventory, products, shopping, barcode entry, stores, tax settings, product routing, scanner status, activity, and household preferences. It can also be installed as a progressive web app.

## Development

Install dependencies from the repository root because this project is part of the pnpm workspace.

```bash
pnpm install
pnpm --filter web start
pnpm --filter web run build
pnpm --filter web test
```

The development server listens on port 4200 and proxies `/api/*` to `http://127.0.0.1:8080`. Start the API before starting the web development server.

The production image is built with `apps/web/Dockerfile`. Nginx serves the compiled application and proxies the same relative API paths to the backend container.

## Structure

Application services and API contracts live in `src/app/core`. Reusable controls live in `src/app/ui`, dialogs live in `src/app/modals`, and each routed page has its own folder under `src/app/screens`.

Shared colors, spacing, typography, and themes are defined in `src/styles/_tokens.scss`. Reusable layout and component classes are in `src/styles/_base.scss`. The responsive breakpoint is 760 pixels.

The application uses Angular signals, computed state, `OnPush` change detection, standalone components, and lazy routes. `LiveService` listens for server-sent events so changes made by scanners or background workers appear without a full page refresh.

Hashed assets are cached for a year by Nginx. The application shell always revalidates, and API responses, writes, credentials, and integration settings are not stored in the browser cache.
