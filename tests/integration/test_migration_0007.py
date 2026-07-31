import asyncio
from uuid import UUID

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine

from larder.models.enums import EnrichmentStatus


def test_0007_upgrades_existing_schema_and_backfills_brands(database_url):
    config = Config("alembic.ini")
    command.downgrade(config, "0006")

    async def insert_legacy_product() -> None:
        engine = create_async_engine(database_url)
        async with engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "INSERT INTO products "
                    "(barcode, barcode_format, name, brand, enrichment_status, "
                    "data_quality_notes, created_at, updated_at) "
                    "VALUES (:barcode, 'UPC_A', 'Legacy item', :brand, :status, "
                    "CAST('{}' AS jsonb), now(), now())"
                ),
                {
                    "barcode": "012345678905",
                    "brand": "Kirkland Signature, Costco",
                    "status": EnrichmentStatus.SUCCEEDED.value,
                },
            )
        await engine.dispose()

    async def read_brands() -> list:
        engine = create_async_engine(database_url)
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    sa.text(
                        "SELECT b.name, b.normalized_name, pb.is_primary "
                        "FROM brands b JOIN product_brands pb ON pb.brand_id = b.id "
                        "WHERE pb.product_barcode = :barcode ORDER BY pb.source_rank"
                    ),
                    {"barcode": "012345678905"},
                )
            ).all()
        await engine.dispose()
        return rows

    asyncio.run(insert_legacy_product())
    command.upgrade(config, "head")
    assert asyncio.run(read_brands()) == [
        ("Kirkland Signature", "kirkland signature", True),
        ("Costco", "costco", False),
    ]


def test_0008_backfills_unassigned_units_from_existing_rules(database_url):
    config = Config("alembic.ini")
    command.downgrade(config, "0007")

    brand_id = UUID("10000000-0000-0000-0000-000000000001")
    store_id = UUID("20000000-0000-0000-0000-000000000001")
    rule_id = UUID("30000000-0000-0000-0000-000000000001")
    scan_id = UUID("40000000-0000-0000-0000-000000000001")
    item_id = UUID("50000000-0000-0000-0000-000000000001")

    async def seed_legacy_state() -> None:
        engine = create_async_engine(database_url)
        async with engine.begin() as connection:
            statements = [
                (
                    "INSERT INTO stores (id, code, name, active, created_at, updated_at) "
                    "VALUES (:id, 'COSTCO', 'Costco', true, now(), now())",
                    {"id": store_id},
                ),
                (
                    "INSERT INTO brands "
                    "(id, name, normalized_name, active, created_at, updated_at) "
                    "VALUES (:id, 'Kirkland Signature', 'kirkland signature', "
                    "true, now(), now())",
                    {"id": brand_id},
                ),
                (
                    "INSERT INTO products "
                    "(barcode, barcode_format, name, brand, enrichment_status, "
                    "data_quality_notes, created_at, updated_at) "
                    "VALUES ('0096619120291', 'EAN_13', 'Coffee', 'Kirkland Signature', "
                    "'SUCCEEDED', CAST('{}' AS jsonb), now(), now())",
                    {},
                ),
                (
                    "INSERT INTO product_brands "
                    "(product_barcode, brand_id, is_primary, source, source_rank) "
                    "VALUES ('0096619120291', :brand_id, true, 'MIGRATION', 0)",
                    {"brand_id": brand_id},
                ),
                (
                    "INSERT INTO scan_events "
                    "(id, idempotency_key, received_at, raw_frame, barcode, kind, result, "
                    "processing_attempts, created_at) "
                    "VALUES (:id, 'migration-0008', now(), 'TEST', '0096619120291', "
                    "'PRODUCT', 'APPLIED', 1, now())",
                    {"id": scan_id},
                ),
                (
                    "INSERT INTO inventory_items "
                    "(id, product_barcode, status, date_added, expiration_is_estimated, "
                    "store_id, price_pending, add_scan_event_id, created_at, updated_at) "
                    "VALUES (:id, '0096619120291', 'IN_STOCK', now(), false, NULL, true, "
                    ":scan_id, now(), now())",
                    {"id": item_id, "scan_id": scan_id},
                ),
                (
                    "INSERT INTO store_routing_rules "
                    "(id, name, match_type, brand_id, store_id, behavior, priority, enabled, "
                    "created_at, updated_at) "
                    "VALUES (:id, 'Kirkland to Costco', 'BRAND', :brand_id, :store_id, "
                    "'PREFER', 0, true, now(), now())",
                    {"id": rule_id, "brand_id": brand_id, "store_id": store_id},
                ),
            ]
            for statement, params in statements:
                await connection.execute(sa.text(statement), params)
        await engine.dispose()

    async def read_result() -> tuple:
        engine = create_async_engine(database_url)
        async with engine.connect() as connection:
            result = (
                await connection.execute(
                    sa.text(
                        "SELECT item.store_id, event.actor_type, event.after_state "
                        "FROM inventory_items AS item "
                        "JOIN inventory_events AS event ON event.inventory_item_id = item.id "
                        "WHERE item.id = :item_id AND event.event_type = 'STORE_CHANGED'"
                    ),
                    {"item_id": item_id},
                )
            ).one()
        await engine.dispose()
        return result

    asyncio.run(seed_legacy_state())
    command.upgrade(config, "head")
    assigned_store, actor_type, after_state = asyncio.run(read_result())
    assert assigned_store == store_id
    assert actor_type == "SYSTEM"
    assert after_state["rule_id"] == str(rule_id)


def test_0009_standardizes_existing_display_names(database_url):
    config = Config("alembic.ini")
    command.downgrade(config, "0008")

    brand_id = UUID("10000000-0000-0000-0000-000000000009")
    alias_id = UUID("11000000-0000-0000-0000-000000000009")
    store_id = UUID("20000000-0000-0000-0000-000000000009")
    rule_id = UUID("30000000-0000-0000-0000-000000000009")
    scanner_id = UUID("40000000-0000-0000-0000-000000000009")

    async def seed_names() -> None:
        engine = create_async_engine(database_url)
        async with engine.begin() as connection:
            statements = [
                (
                    "UPDATE food_categories SET display_name = 'uNCATEGORIZED fOODS' "
                    "WHERE id = 'local:uncategorized'",
                    {},
                ),
                (
                    "INSERT INTO stores (id, code, name, active, created_at, updated_at) "
                    "VALUES (:id, 'COSTCO', 'cOSTCO WHOLESALE', true, now(), now())",
                    {"id": store_id},
                ),
                (
                    "INSERT INTO brands "
                    "(id, name, normalized_name, active, created_at, updated_at) "
                    "VALUES (:id, 'kIRKLAND SIGNATURE', 'kirkland signature', "
                    "true, now(), now())",
                    {"id": brand_id},
                ),
                (
                    "INSERT INTO brand_aliases "
                    "(id, brand_id, name, normalized_name, created_at, updated_at) "
                    "VALUES (:id, :brand_id, 'KIRKLAND''S BEST', 'kirkland s best', "
                    "now(), now())",
                    {"id": alias_id, "brand_id": brand_id},
                ),
                (
                    "INSERT INTO products "
                    "(barcode, barcode_format, name, brand, enrichment_status, "
                    "data_quality_notes, created_at, updated_at) "
                    "VALUES ('0096619120299', 'EAN_13', "
                    "'COLOMBIAN sUPREMO WHOLE BEAN COFFEE', 'kIRKLAND SIGNATURE', "
                    "'SUCCEEDED', CAST('{}' AS jsonb), now(), now())",
                    {},
                ),
                (
                    "INSERT INTO store_routing_rules "
                    "(id, name, match_type, brand_id, store_id, behavior, priority, enabled, "
                    "created_at, updated_at) "
                    "VALUES (:id, 'KIRKLAND to COSTCO', 'BRAND', :brand_id, :store_id, "
                    "'PREFER', 0, true, now(), now())",
                    {"id": rule_id, "brand_id": brand_id, "store_id": store_id},
                ),
                (
                    "INSERT INTO scanners "
                    "(id, code, name, enabled, mode_timeout_seconds, store_timeout_seconds, "
                    "created_at, updated_at) "
                    "VALUES (:id, 'SCN09', 'kITCHEN SCANNER', true, 30, 1800, "
                    "now(), now())",
                    {"id": scanner_id},
                ),
            ]
            for statement, params in statements:
                await connection.execute(sa.text(statement), params)
        await engine.dispose()

    async def read_names() -> tuple:
        engine = create_async_engine(database_url)
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    sa.text(
                        "SELECT "
                        "(SELECT name FROM products WHERE barcode = '0096619120299'), "
                        "(SELECT brand FROM products WHERE barcode = '0096619120299'), "
                        "(SELECT name FROM brands WHERE id = :brand_id), "
                        "(SELECT name FROM brand_aliases WHERE id = :alias_id), "
                        "(SELECT name FROM stores WHERE id = :store_id), "
                        "(SELECT display_name FROM food_categories "
                        " WHERE id = 'local:uncategorized'), "
                        "(SELECT name FROM store_routing_rules WHERE id = :rule_id), "
                        "(SELECT name FROM scanners WHERE id = :scanner_id)"
                    ),
                    {
                        "brand_id": brand_id,
                        "alias_id": alias_id,
                        "store_id": store_id,
                        "rule_id": rule_id,
                        "scanner_id": scanner_id,
                    },
                )
            ).one()
        await engine.dispose()
        return row

    asyncio.run(seed_names())
    command.upgrade(config, "head")
    assert asyncio.run(read_names()) == (
        "Colombian Supremo Whole Bean Coffee",
        "Kirkland Signature",
        "Kirkland Signature",
        "Kirkland's Best",
        "Costco Wholesale",
        "Uncategorized Foods",
        "Kirkland To Costco",
        "Kitchen Scanner",
    )
