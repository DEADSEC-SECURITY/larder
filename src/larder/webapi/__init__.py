"""Read/write REST API consumed by the Larder web UI (apps/web)."""

from larder.webapi.router import build_web_router

__all__ = ["build_web_router"]
