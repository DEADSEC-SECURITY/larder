import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from larder.config import Settings
from larder.enrichment.worker import EnrichmentWorker
from larder.models import (
    Brand,
    ControlBarcode,
    EnrichmentJob,
    FoodCategory,
    GroceryListItem,
    InventoryEvent,
    InventoryItem,
    Product,
    ProductBrand,
    ProductCategory,
    ProductStockPolicy,
    ScanEvent,
    Scanner,
    ScannerContext,
    StorageLocation,
    Store,
    StoreRoutingRule,
)
from larder.models.enums import (
    EnrichmentStatus,
    InventoryEventType,
    ScanKind,
    ScannerMode,
    ScanResult,
)
from larder.schemas.enrichment import FetchDisposition, OffFetchResult
from larder.schemas.scans import ScanEnvelope
from larder.services.command_router import CommandRouter
from larder.services.recovery import recover_ignored_adds
from larder.services.stock_policy import recalculate_stock_policy

pytestmark = pytest.mark.integration
GTIN = "4006381333931"


async def add_scanner(session_factory, code="SCN01"):
    scanner_id = uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            Scanner(
                id=scanner_id,
                code=code,
                name=code,
                mode_timeout_seconds=30,
                store_timeout_seconds=1800,
            )
        )
        session.add(ScannerContext(scanner_id=scanner_id))
    return scanner_id


def envelope(payload, code="SCN01", event_id=None):
    return ScanEnvelope(
        id=event_id or uuid4(),
        raw_frame=f"LDR:{code}|{payload}\r\n".encode(),
        port=f"fake:{code}",
        expected_scanner_code=code,
    )


def router(session_factory, database_url):
    return CommandRouter(session_factory, Settings(database_url=database_url))


@pytest.mark.asyncio
async def test_add_is_atomic_physical_and_idempotent(session_factory, database_url):
    await add_scanner(session_factory)
    service = router(session_factory, database_url)
    await service.process(envelope("CMD:MODE:ADD"))
    product_scan = envelope(GTIN)
    first = await service.process(product_scan)
    duplicate = await service.process(product_scan)
    second = await service.process(envelope(GTIN))

    assert first.result == ScanResult.APPLIED
    assert duplicate.duplicate is True
    assert duplicate.item_id == first.item_id
    assert second.item_id != first.item_id
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Product)) == 1
        assert (
            await session.scalar(select(func.count()).select_from(EnrichmentJob)) == 1
        )
        assert (
            await session.scalar(select(func.count()).select_from(InventoryItem)) == 2
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(InventoryEvent)
                .where(InventoryEvent.event_type == InventoryEventType.ADDED)
            )
            == 2
        )


@pytest.mark.asyncio
async def test_location_control_assigns_added_items_and_can_be_cleared(
    session_factory, database_url
):
    scanner_id = await add_scanner(session_factory)
    location_id = uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            StorageLocation(
                id=location_id,
                code="freezer",
                name="Freezer",
                active=True,
            )
        )
        await session.flush()
        session.add(
            ControlBarcode(
                payload="CMD:LOC:freezer",
                command_type="SET_LOCATION",
                storage_location_id=location_id,
                enabled=True,
                description="Store scanned items in Freezer",
            )
        )

    service = router(session_factory, database_url)
    await service.process(envelope("CMD:MODE:ADD"))
    selected = await service.process(envelope("CMD:LOC:freezer"))
    added = await service.process(envelope(GTIN))

    assert selected.result == ScanResult.APPLIED
    assert added.result == ScanResult.APPLIED
    async with session_factory() as session:
        item = await session.get(InventoryItem, added.item_id)
        context = await session.get(ScannerContext, scanner_id)
        assert item is not None
        assert item.storage_location_id == location_id
        assert context.storage_location_id == location_id
        assert context.storage_location_expires_at is not None

    cleared = await service.process(envelope("CMD:LOC:CLEAR"))
    assert cleared.result == ScanResult.APPLIED
    async with session_factory() as session:
        context = await session.get(ScannerContext, scanner_id)
        assert context.storage_location_id is None
        assert context.storage_location_expires_at is None


@pytest.mark.asyncio
async def test_list_mode_adds_manual_grocery_quantity_without_inventory(
    session_factory, database_url
):
    scanner_id = await add_scanner(session_factory)
    service = router(session_factory, database_url)

    command = await service.process(envelope("CMD:MODE:LIST"))
    first = await service.process(envelope(GTIN))
    second = await service.process(envelope(GTIN))

    assert command.result == ScanResult.APPLIED
    assert first.result == ScanResult.APPLIED
    assert second.result == ScanResult.APPLIED
    assert first.item_id is None
    assert second.item_id is None
    async with session_factory() as session:
        grocery = await session.get(GroceryListItem, GTIN)
        context = await session.get(ScannerContext, scanner_id)
        assert grocery is not None
        assert grocery.manual_quantity == 2
        assert grocery.automatic_quantity == 0
        assert context.mode == ScannerMode.LIST
        assert await session.scalar(select(func.count()).select_from(InventoryItem)) == 0
        assert await session.scalar(select(func.count()).select_from(Product)) == 1
        assert await session.scalar(select(func.count()).select_from(EnrichmentJob)) == 1


@pytest.mark.asyncio
async def test_scanner_add_defaults_unassigned_item_to_brand_rule(
    session_factory, database_url
):
    await add_scanner(session_factory)
    brand_id = uuid4()
    store_id = uuid4()
    async with session_factory() as session, session.begin():
        session.add_all(
            [
                Product(
                    barcode=GTIN,
                    barcode_format="GTIN_13",
                    name="Kirkland test product",
                    brand="Kirkland Signature",
                ),
                Brand(
                    id=brand_id,
                    name="Kirkland Signature",
                    normalized_name="kirkland signature",
                    active=True,
                ),
                Store(id=store_id, code="COSTCO", name="Costco", active=True),
            ]
        )
        await session.flush()
        session.add_all(
            [
                ProductBrand(
                    product_barcode=GTIN,
                    brand_id=brand_id,
                    is_primary=True,
                    source="TEST",
                ),
                StoreRoutingRule(
                    name="Kirkland to Costco",
                    match_type="BRAND",
                    brand_id=brand_id,
                    store_id=store_id,
                    behavior="PREFER",
                    priority=0,
                    enabled=True,
                ),
            ]
        )

    service = router(session_factory, database_url)
    await service.process(envelope("CMD:MODE:ADD"))
    added = await service.process(envelope(GTIN))

    async with session_factory() as session:
        item = await session.get(InventoryItem, added.item_id)
        assert item.store_id == store_id


@pytest.mark.asyncio
async def test_underscore_add_command_is_canonicalized_and_applied(
    session_factory, database_url
):
    await add_scanner(session_factory)
    service = router(session_factory, database_url)

    mode = await service.process(envelope("CMD_MODE_ADD"))
    product = await service.process(envelope(GTIN))

    assert mode.result == ScanResult.APPLIED
    assert product.result == ScanResult.APPLIED


@pytest.mark.asyncio
async def test_ignored_add_recovery_is_previewable_and_idempotent(
    session_factory, database_url
):
    scanner_id = await add_scanner(session_factory)
    now = datetime.now(UTC)
    anchor_id = uuid4()
    ignored_ids = [uuid4(), uuid4()]
    async with session_factory() as session, session.begin():
        session.add(
            ScanEvent(
                id=anchor_id,
                idempotency_key=str(anchor_id),
                scanner_id=scanner_id,
                received_at=now,
                raw_frame="LDR:SCN01|CMD_MODE_ADD",
                payload_raw="CMD_MODE_ADD",
                kind=ScanKind.PRODUCT,
                result=ScanResult.REJECTED_BAD_BARCODE,
                error_code="GTIN_NOT_DECIMAL",
                processed_at=now,
            )
        )
        for offset, event_id in enumerate(ignored_ids, start=1):
            received_at = now + timedelta(seconds=offset)
            session.add(
                ScanEvent(
                    id=event_id,
                    idempotency_key=str(event_id),
                    scanner_id=scanner_id,
                    received_at=received_at,
                    raw_frame=f"LDR:SCN01|{GTIN}",
                    payload_raw=GTIN,
                    barcode=GTIN,
                    normalization_version=1,
                    kind=ScanKind.PRODUCT,
                    result=ScanResult.IGNORED_NO_MODE,
                    processed_at=received_at,
                )
            )

    settings = Settings(database_url=database_url)
    preview = await recover_ignored_adds(
        session_factory,
        settings,
        scanner_code="SCN01",
        failed_command="CMD_MODE_ADD",
        since=now - timedelta(minutes=1),
    )
    applied = await recover_ignored_adds(
        session_factory,
        settings,
        scanner_code="SCN01",
        failed_command="CMD_MODE_ADD",
        since=now - timedelta(minutes=1),
        apply=True,
    )
    repeated = await recover_ignored_adds(
        session_factory,
        settings,
        scanner_code="SCN01",
        failed_command="CMD_MODE_ADD",
        since=now - timedelta(minutes=1),
        apply=True,
    )

    assert len(preview.candidates) == 2
    assert applied.applied == 2
    assert applied.failures == ()
    assert repeated.applied == 0
    assert repeated.duplicates == 2
    async with session_factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(InventoryItem)) == 2
        )


@pytest.mark.asyncio
async def test_hardware_scanner_prefix_is_removed_before_gtin_normalization(
    session_factory, database_url
):
    await add_scanner(session_factory)
    service = router(session_factory, database_url)
    await service.process(envelope("CMD:MODE:ADD"))

    outcome = await service.process(envelope("SCN01613008725914"))

    assert outcome.result == ScanResult.APPLIED
    assert outcome.barcode == "0613008725914"
    async with session_factory() as session:
        product = await session.get(Product, "0613008725914")
        assert product is not None
        job = await session.scalar(
            select(EnrichmentJob).where(
                EnrichmentJob.product_barcode == "0613008725914"
            )
        )
        assert job is not None


@pytest.mark.asyncio
async def test_add_failure_rolls_back_receipt_product_job_and_item(
    session_factory, database_url
):
    await add_scanner(session_factory)
    service = router(session_factory, database_url)
    await service.process(envelope("CMD:MODE:ADD"))
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                """
                CREATE FUNCTION reject_test_inventory_add() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'test rollback'; END; $$
                """
            )
        )
        await session.execute(
            text(
                """
                CREATE TRIGGER reject_test_inventory_add
                BEFORE INSERT ON inventory_items
                FOR EACH ROW EXECUTE FUNCTION reject_test_inventory_add()
                """
            )
        )
    try:
        with pytest.raises(Exception, match="test rollback"):
            await service.process(envelope(GTIN))
    finally:
        async with session_factory() as session, session.begin():
            await session.execute(
                text("DROP TRIGGER reject_test_inventory_add ON inventory_items")
            )
            await session.execute(text("DROP FUNCTION reject_test_inventory_add()"))
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Product)) == 0
        assert (
            await session.scalar(select(func.count()).select_from(EnrichmentJob)) == 0
        )
        assert (
            await session.scalar(select(func.count()).select_from(InventoryItem)) == 0
        )
        product_scans = await session.scalar(
            select(func.count())
            .select_from(ScanEvent)
            .where(ScanEvent.kind == "PRODUCT")
        )
        assert product_scans == 0


@pytest.mark.asyncio
async def test_fefo_and_null_expiration_last(session_factory, database_url):
    await add_scanner(session_factory)
    service = router(session_factory, database_url)
    await service.process(envelope("CMD:MODE:ADD"))
    outcomes = [await service.process(envelope(GTIN)) for _ in range(3)]
    async with session_factory() as session, session.begin():
        now = await session.scalar(select(func.clock_timestamp()))
        items = list(
            await session.scalars(
                select(InventoryItem).order_by(
                    InventoryItem.date_added, InventoryItem.id
                )
            )
        )
        items[0].expiration_date = None
        items[1].expiration_date = (now + timedelta(days=3)).date()
        items[2].expiration_date = (now + timedelta(days=1)).date()
        expected = items[2].id

    await service.process(envelope("CMD:MODE:REMOVE"))
    removed = await service.process(envelope(GTIN))
    assert removed.item_id == expected
    assert removed.item_id in {outcome.item_id for outcome in outcomes}


@pytest.mark.asyncio
async def test_two_scanners_cannot_remove_one_item_twice(session_factory, database_url):
    await add_scanner(session_factory, "SCN01")
    await add_scanner(session_factory, "SCN02")
    service = router(session_factory, database_url)
    await service.process(envelope("CMD:MODE:ADD", "SCN01"))
    await service.process(envelope(GTIN, "SCN01"))
    await service.process(envelope("CMD:MODE:REMOVE", "SCN01"))
    await service.process(envelope("CMD:MODE:REMOVE", "SCN02"))

    results = await asyncio.gather(
        service.process(envelope(GTIN, "SCN01")),
        service.process(envelope(GTIN, "SCN02")),
    )
    assert sorted(result.result.value for result in results) == sorted(
        [ScanResult.APPLIED.value, ScanResult.IGNORED_NO_STOCK.value]
    )
    async with session_factory() as session:
        terminal_events = await session.scalar(
            select(func.count())
            .select_from(InventoryEvent)
            .where(
                InventoryEvent.event_type.in_(
                    [InventoryEventType.REMOVED, InventoryEventType.TRASHED]
                )
            )
        )
        assert terminal_events == 1


@pytest.mark.asyncio
async def test_rejected_and_no_stock_scans_do_not_renew_mode(
    session_factory, database_url
):
    scanner_id = await add_scanner(session_factory)
    service = router(session_factory, database_url)
    await service.process(envelope("CMD:MODE:REMOVE"))
    async with session_factory() as session:
        initial_expiry = (await session.get(ScannerContext, scanner_id)).mode_expires_at

    rejected = await service.process(envelope("4006381333932"))
    ignored = await service.process(envelope(GTIN))
    async with session_factory() as session:
        final_context = await session.get(ScannerContext, scanner_id)
        assert final_context.mode_expires_at == initial_expiry
    assert rejected.result == ScanResult.REJECTED_BAD_BARCODE
    assert ignored.result == ScanResult.IGNORED_NO_STOCK


@pytest.mark.asyncio
async def test_exact_timeout_boundary_is_inactive(session_factory, database_url):
    scanner_id = await add_scanner(session_factory)
    service = router(session_factory, database_url)
    async with session_factory() as session, session.begin():
        context = await session.get(ScannerContext, scanner_id)
        context.mode = ScannerMode.ADD
        context.mode_expires_at = await session.scalar(select(func.clock_timestamp()))
    outcome = await service.process(envelope(GTIN))
    assert outcome.result == ScanResult.IGNORED_NO_MODE
    async with session_factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(InventoryItem)) == 0
        )


@pytest.mark.asyncio
async def test_manual_grocery_demand_survives_projection(session_factory, database_url):
    await add_scanner(session_factory)
    service = router(session_factory, database_url)
    await service.process(envelope("CMD:MODE:ADD"))
    await service.process(envelope(GTIN))
    async with session_factory() as session, session.begin():
        session.add(
            ProductStockPolicy(product_barcode=GTIN, reorder_point=2, target_stock=5)
        )
        session.add(
            GroceryListItem(
                product_barcode=GTIN,
                automatic_quantity=0,
                manual_quantity=3,
                checked_quantity=0,
            )
        )
    async with session_factory() as session, session.begin():
        assert await recalculate_stock_policy(session, GTIN) == 4
    async with session_factory() as session:
        item = await session.get(GroceryListItem, GTIN)
        assert item.automatic_quantity == 4
        assert item.manual_quantity == 3


@pytest.mark.asyncio
async def test_enrichment_backfills_only_unknown_expiration_and_snapshots_rule(
    session_factory, database_url
):
    await add_scanner(session_factory)
    service = router(session_factory, database_url)
    async with session_factory() as session, session.begin():
        session.add(
            FoodCategory(
                id="en:yogurts",
                display_name="Yogurts",
                default_expiration_days=7,
            )
        )
    await service.process(envelope("CMD:MODE:ADD"))
    added = await service.process(envelope(GTIN))

    class FakeClient:
        async def fetch_product(self, barcode):
            return OffFetchResult(
                FetchDisposition.FOUND,
                http_status=200,
                product={
                    "code": barcode,
                    "product_name": "Yogurt",
                    "categories_tags": ["en:dairies", "en:yogurts"],
                    "nutrition": {
                        "aggregated_set": {
                            "per": "100g",
                            "preparation": "as_sold",
                            "nutrients": {},
                        }
                    },
                },
            )

    worker = EnrichmentWorker(
        session_factory, FakeClient(), Settings(database_url=database_url)
    )
    assert await worker.run_once(barcode=GTIN)
    async with session_factory() as session:
        item = await session.get(InventoryItem, added.item_id)
        original_expiration = item.expiration_date
        assert item.expiration_is_estimated is True
        assert item.expiration_category_id == "en:yogurts"
        assert item.expiration_offset_days == 7
        event_count = await session.scalar(
            select(func.count())
            .select_from(InventoryEvent)
            .where(InventoryEvent.event_type == InventoryEventType.EXPIRATION_CHANGED)
        )
        assert event_count == 1

    async with session_factory() as session, session.begin():
        category = await session.get(FoodCategory, "en:yogurts")
        category.default_expiration_days = 30
        job = await session.scalar(
            select(EnrichmentJob).where(EnrichmentJob.product_barcode == GTIN)
        )
        job.status = EnrichmentStatus.PENDING
        job.next_attempt_at = await session.scalar(select(func.clock_timestamp()))
    assert await worker.run_once(barcode=GTIN)
    async with session_factory() as session:
        item = await session.get(InventoryItem, added.item_id)
        assert item.expiration_date == original_expiration
        assert item.expiration_offset_days == 7


@pytest.mark.asyncio
async def test_enrichment_uses_specific_display_category_without_expiration_rule(
    session_factory, database_url
):
    await add_scanner(session_factory)
    service = router(session_factory, database_url)
    await service.process(envelope("CMD:MODE:ADD"))
    added = await service.process(envelope(GTIN))

    class FakeClient:
        async def fetch_product(self, barcode):
            return OffFetchResult(
                FetchDisposition.FOUND,
                http_status=200,
                product={
                    "code": barcode,
                    "product_name": "Fresh Strawberries",
                    "categories_hierarchy": ["en:fruits", "en:strawberries"],
                },
            )

    worker = EnrichmentWorker(
        session_factory, FakeClient(), Settings(database_url=database_url)
    )
    assert await worker.run_once(barcode=GTIN)

    async with session_factory() as session:
        primary = await session.scalar(
            select(ProductCategory).where(
                ProductCategory.product_barcode == GTIN,
                ProductCategory.is_primary.is_(True),
            )
        )
        item = await session.get(InventoryItem, added.item_id)
        assert primary is not None
        assert primary.category_id == "en:strawberries"
        fruits = await session.get(FoodCategory, "en:fruits")
        strawberries = await session.get(FoodCategory, "en:strawberries")
        assert fruits is not None
        assert fruits.parent_id is None
        assert strawberries is not None
        assert strawberries.parent_id == "en:fruits"
        assert item.expiration_date is None
        assert item.expiration_category_id is None


@pytest.mark.asyncio
async def test_enrichment_defaults_unassigned_unit_from_new_brand_match(
    session_factory, database_url
):
    await add_scanner(session_factory)
    service = router(session_factory, database_url)
    brand_id = uuid4()
    store_id = uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            Brand(
                id=brand_id,
                name="Kirkland Signature",
                normalized_name="kirkland signature",
                active=True,
            )
        )
        session.add(Store(id=store_id, code="COSTCO", name="Costco", active=True))
        await session.flush()
        session.add(
            StoreRoutingRule(
                name="Kirkland to Costco",
                match_type="BRAND",
                brand_id=brand_id,
                store_id=store_id,
                behavior="PREFER",
                priority=0,
                enabled=True,
            )
        )

    await service.process(envelope("CMD:MODE:ADD"))
    added = await service.process(envelope(GTIN))
    async with session_factory() as session:
        item = await session.get(InventoryItem, added.item_id)
        assert item.store_id is None

    class FakeClient:
        async def fetch_product(self, barcode):
            return OffFetchResult(
                FetchDisposition.FOUND,
                http_status=200,
                product={
                    "code": barcode,
                    "product_name": "Kirkland Coffee",
                    "brands": "Kirkland Signature",
                    "categories_tags": [],
                },
            )

    worker = EnrichmentWorker(
        session_factory, FakeClient(), Settings(database_url=database_url)
    )
    assert await worker.run_once(barcode=GTIN)
    async with session_factory() as session:
        item = await session.get(InventoryItem, added.item_id)
        assert item.store_id == store_id
        event = await session.scalar(
            select(InventoryEvent).where(
                InventoryEvent.inventory_item_id == item.id,
                InventoryEvent.event_type == InventoryEventType.STORE_CHANGED,
            )
        )
        assert event is not None
        assert event.actor_type == "SYSTEM"
        assert event.after_state["store_id"] == str(store_id)


@pytest.mark.asyncio
async def test_inventory_events_reject_update_and_delete(session_factory, database_url):
    await add_scanner(session_factory)
    service = router(session_factory, database_url)
    await service.process(envelope("CMD:MODE:ADD"))
    await service.process(envelope(GTIN))
    async with session_factory() as session:
        event = await session.scalar(select(InventoryEvent))
        event.reason = "attempted rewrite"
        with pytest.raises(Exception, match="append-only"):
            await session.commit()
