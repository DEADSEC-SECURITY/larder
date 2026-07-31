from __future__ import annotations

from fastapi import APIRouter

from larder.webapi import (
    activity,
    admin,
    auth,
    barcodes,
    brands,
    catalog,
    categories,
    commands,
    events,
    grocery,
    intake,
    integrations,
    mutations,
    overview,
    printing,
    routing,
    scanners,
    stock_groups,
    storage_locations,
    stores,
    taxes,
)


def build_web_router() -> APIRouter:
    router = APIRouter(prefix="/v1/web")
    # Auth first: these routes stay reachable while signed out.
    router.include_router(auth.router)
    router.include_router(overview.router)
    router.include_router(catalog.router)
    router.include_router(barcodes.router)
    router.include_router(mutations.router)
    router.include_router(grocery.router)
    router.include_router(intake.router)
    router.include_router(categories.router)
    router.include_router(stores.router)
    router.include_router(storage_locations.router)
    router.include_router(stock_groups.router)
    router.include_router(taxes.router)
    router.include_router(integrations.router)
    router.include_router(brands.router)
    router.include_router(routing.router)
    router.include_router(activity.router)
    router.include_router(scanners.router)
    router.include_router(commands.router)
    router.include_router(printing.router)
    router.include_router(admin.router)
    router.include_router(events.router)
    return router
