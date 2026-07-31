from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from larder.api import create_app
from larder.config import Settings
from larder.models import (
    HouseholdSettings,
    IntakeBatch,
    InventoryEvent,
    InventoryItem,
    ProductStoreObservation,
    Scanner,
    ScannerContext,
    Store,
)
from larder.models.enums import InventoryEventType
from larder.schemas.scans import ScanEnvelope
from larder.services.command_router import CommandRouter

pytestmark = pytest.mark.integration

GTIN = "4006381333931"
OTHER_GTIN = "5901234123457"


async def add_scanner(session_factory, code: str = "SCN01"):
    scanner_id = uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            Scanner(
                id=scanner_id,
                code=code,
                name="Kitchen Scanner",
                mode_timeout_seconds=30,
                store_timeout_seconds=1800,
            )
        )
        session.add(ScannerContext(scanner_id=scanner_id))
    return scanner_id


def envelope(payload: str, code: str = "SCN01", event_id=None) -> ScanEnvelope:
    return ScanEnvelope(
        id=event_id or uuid4(),
        raw_frame=f"LDR:{code}|{payload}\r\n".encode(),
        port=f"fake:{code}",
        expected_scanner_code=code,
        received_at=datetime.now(UTC),
    )


def service(session_factory, database_url) -> CommandRouter:
    return CommandRouter(session_factory, Settings(database_url=database_url))


def web_app(session_factory, database_url):
    settings = Settings(database_url=database_url, scan_api_token="test-token")
    app = create_app(settings)
    app.state.settings = settings
    app.state.session_factory = session_factory
    return app


@pytest.mark.asyncio
async def test_consecutive_same_product_scans_share_one_batch(session_factory, database_url):
    await add_scanner(session_factory)
    router = service(session_factory, database_url)
    await router.process(envelope("CMD:MODE:ADD"))
    scan = envelope(GTIN)
    first = await router.process(scan)
    duplicate = await router.process(scan)
    second = await router.process(envelope(GTIN))

    assert first.intake_batch_id is not None
    assert duplicate.intake_batch_id == first.intake_batch_id
    assert second.intake_batch_id == first.intake_batch_id
    async with session_factory() as session:
        batch = await session.get(IntakeBatch, first.intake_batch_id)
        assert batch.quantity == 2
        items = (
            await session.scalars(
                select(InventoryItem).where(InventoryItem.intake_batch_id == first.intake_batch_id)
            )
        ).all()
        assert len(items) == 2


@pytest.mark.asyncio
async def test_same_product_after_group_timeout_starts_new_batch(session_factory, database_url):
    await add_scanner(session_factory)
    async with session_factory() as session, session.begin():
        prefs = await session.get(HouseholdSettings, "household")
        if prefs is None:
            session.add(HouseholdSettings(id="household", intake_scan_group_timeout_seconds=5))
        else:
            prefs.intake_scan_group_timeout_seconds = 5

    router = service(session_factory, database_url)
    await router.process(envelope("CMD:MODE:ADD"))
    first = await router.process(envelope(GTIN))

    async with session_factory() as session, session.begin():
        batch = await session.get(IntakeBatch, first.intake_batch_id)
        assert batch is not None
        batch.last_scan_at = datetime.now(UTC) - timedelta(seconds=6)

    second = await router.process(envelope(GTIN))

    assert second.intake_batch_id != first.intake_batch_id
    async with session_factory() as session:
        quantities = list(
            await session.scalars(select(IntakeBatch.quantity).order_by(IntakeBatch.created_at))
        )
        assert quantities == [1, 1]


@pytest.mark.asyncio
async def test_different_product_breaks_consecutive_batch(session_factory, database_url):
    await add_scanner(session_factory)
    router = service(session_factory, database_url)
    await router.process(envelope("CMD:MODE:ADD"))

    first = await router.process(envelope(GTIN))
    second = await router.process(envelope(OTHER_GTIN))
    third = await router.process(envelope(GTIN))

    assert len({first.intake_batch_id, second.intake_batch_id, third.intake_batch_id}) == 3
    async with session_factory() as session:
        quantities = list(await session.scalars(select(IntakeBatch.quantity)))
        assert quantities == [1, 1, 1]


@pytest.mark.asyncio
async def test_intake_api_lists_and_confirms_all_scanned_units(session_factory, database_url):
    await add_scanner(session_factory)
    store_id = uuid4()
    async with session_factory() as session, session.begin():
        session.add(Store(id=store_id, code="COSTCO", name="Costco", active=True))

    router = service(session_factory, database_url)
    await router.process(envelope("CMD:MODE:ADD"))
    await router.process(envelope(GTIN))
    added = await router.process(envelope(GTIN))

    app = web_app(session_factory, database_url)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://web-test"
    ) as client:
        response = await client.get("/v1/web/intake-batches")
        assert response.status_code == 200, response.text
        open_batch = response.json()["batches"][0]
        assert open_batch["id"] == str(added.intake_batch_id)
        assert open_batch["quantity"] == 2
        assert open_batch["scannerName"] == "Kitchen Scanner"
        assert open_batch["barcode"] == GTIN

        response = await client.post(
            f"/v1/web/intake-batches/{added.intake_batch_id}/confirm",
            json={
                "storeId": str(store_id),
                "unitPrice": 4.99,
                "expirationDate": "2026-08-15",
            },
        )
        assert response.status_code == 200, response.text
        confirmed = response.json()
        assert confirmed["status"] == "confirmed"
        assert confirmed["quantity"] == 2
        assert confirmed["storeName"] == "Costco"
        assert confirmed["unitPrice"] == 4.99
        assert confirmed["expirationDate"] == "2026-08-15"
        assert confirmed["expirationKind"] == "confirmed"

        response = await client.get("/v1/web/intake-batches")
        assert response.json() == {"batches": []}

    async with session_factory() as session:
        items = (
            await session.scalars(
                select(InventoryItem).where(InventoryItem.intake_batch_id == added.intake_batch_id)
            )
        ).all()
        assert len(items) == 2
        assert all(item.store_id == store_id for item in items)
        assert all(float(item.price_paid) == 4.99 for item in items)
        assert all(item.expiration_date.isoformat() == "2026-08-15" for item in items)
        assert all(item.expiration_is_estimated is False for item in items)
        assert await session.scalar(select(func.count()).select_from(ProductStoreObservation)) == 1
        for event_type in (
            InventoryEventType.STORE_CHANGED,
            InventoryEventType.PRICE_CHANGED,
            InventoryEventType.EXPIRATION_CHANGED,
        ):
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(InventoryEvent)
                    .where(InventoryEvent.event_type == event_type)
                )
                == 2
            )


@pytest.mark.asyncio
async def test_intake_total_price_is_split_across_all_scanned_units(
    session_factory, database_url
):
    await add_scanner(session_factory)
    router = service(session_factory, database_url)
    await router.process(envelope("CMD:MODE:ADD"))
    scans = [await router.process(envelope(GTIN)) for _ in range(10)]
    batch_id = scans[-1].intake_batch_id

    app = web_app(session_factory, database_url)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://web-test"
    ) as client:
        response = await client.post(
            f"/v1/web/intake-batches/{batch_id}/confirm",
            json={"totalPrice": 49.90},
        )

    assert response.status_code == 200, response.text
    confirmed = response.json()
    assert confirmed["quantity"] == 10
    assert confirmed["editableQuantity"] == 10
    assert confirmed["unitPrice"] == 4.99

    async with session_factory() as session:
        items = (
            await session.scalars(
                select(InventoryItem).where(InventoryItem.intake_batch_id == batch_id)
            )
        ).all()
        assert len(items) == 10
        assert all(float(item.price_paid) == 4.99 for item in items)


@pytest.mark.asyncio
async def test_explicit_nulls_clear_defaults_and_dismiss_keeps_inventory(
    session_factory, database_url
):
    await add_scanner(session_factory)
    router = service(session_factory, database_url)
    await router.process(envelope("CMD:MODE:ADD"))
    added = await router.process(envelope(GTIN))

    app = web_app(session_factory, database_url)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://web-test"
    ) as client:
        response = await client.post(
            f"/v1/web/intake-batches/{added.intake_batch_id}/confirm",
            json={"storeId": None, "unitPrice": None, "expirationDate": None},
        )
        assert response.status_code == 200, response.text
        assert response.json()["expirationKind"] == "unknown"

        next_batch = await router.process(envelope(OTHER_GTIN))
        response = await client.post(
            f"/v1/web/intake-batches/{next_batch.intake_batch_id}/dismiss", json={}
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "dismissed"

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(InventoryItem)) == 2
