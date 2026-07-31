from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import typer
import uvicorn
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from larder.config import get_settings
from larder.daemon import run_daemon
from larder.db import create_engine, create_session_factory
from larder.domain.names import standardize_name
from larder.enrichment.mapper import category_display_name
from larder.enrichment.off_client import OpenFoodFactsClient
from larder.enrichment.provider_chain import ProductProviderChain
from larder.enrichment.upcitemdb_client import UpcItemDbClient
from larder.enrichment.worker import EnrichmentWorker
from larder.logging import configure_logging
from larder.models import (
    EnrichmentJob,
    FoodCategory,
    HouseholdSettings,
    Product,
    ProductStockPolicy,
    Scanner,
    ScannerContext,
    StockGroupPolicy,
)
from larder.models.enums import EnrichmentStatus
from larder.models.settings import (
    HOUSEHOLD_SETTINGS_ID,
    SCANNER_CONTEXT_TIMEOUT_SECONDS_DEFAULT,
    SCANNER_MODE_TIMEOUT_SECONDS_DEFAULT,
)
from larder.scanner.parser import normalize_gtin, parse_frame
from larder.scanner.serial import SerialScannerSource
from larder.services.recovery import (
    recover_ignored_adds as recover_ignored_adds_service,
)
from larder.services.stock_policy import (
    recalculate_stock_group_policy,
    recalculate_stock_policy,
)

app = typer.Typer(no_args_is_help=True, help="Larder inventory daemon and administration CLI.")


def _run(coroutine) -> None:
    asyncio.run(coroutine)


@app.command()
def daemon() -> None:
    """Run scanner readers and background enrichment."""
    settings = get_settings()
    configure_logging(settings.log_level)
    _run(run_daemon(settings))


@app.command()
def api() -> None:
    """Serve the authenticated scanner-ingestion API."""
    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        "larder.api:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )


@app.command()
def healthcheck() -> None:
    """Check database readiness for container health probes."""

    async def execute() -> None:
        engine = create_engine(get_settings().database_url)
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    try:
        _run(execute())
    except Exception as exc:
        typer.echo(f"not ready: {type(exc).__name__}", err=True)
        raise typer.Exit(1) from exc
    typer.echo("ready")


@app.command("scanner-add")
def scanner_add(
    code: str,
    name: str,
    device_path: str | None = typer.Option(None),
    mode_timeout_seconds: int | None = typer.Option(None, min=5),
    store_timeout_seconds: int | None = typer.Option(None, min=30),
) -> None:
    """Create or update a scanner and its durable context row."""

    async def execute() -> None:
        settings = get_settings()
        engine = create_engine(settings.database_url)
        sessions = create_session_factory(engine)
        async with sessions() as session, session.begin():
            prefs = await session.get(HouseholdSettings, HOUSEHOLD_SETTINGS_ID)
            effective_mode_timeout = mode_timeout_seconds or (
                prefs.scanner_mode_timeout_seconds
                if prefs is not None
                else SCANNER_MODE_TIMEOUT_SECONDS_DEFAULT
            )
            effective_context_timeout = store_timeout_seconds or (
                prefs.scanner_context_timeout_seconds
                if prefs is not None
                else SCANNER_CONTEXT_TIMEOUT_SECONDS_DEFAULT
            )
            scanner_id = await session.scalar(
                insert(Scanner)
                .values(
                    id=uuid4(),
                    code=code,
                    name=standardize_name(name),
                    device_path=device_path,
                    mode_timeout_seconds=effective_mode_timeout,
                    store_timeout_seconds=effective_context_timeout,
                )
                .on_conflict_do_update(
                    index_elements=[Scanner.code],
                    set_={
                        "name": standardize_name(name),
                        "device_path": device_path,
                        "mode_timeout_seconds": effective_mode_timeout,
                        "store_timeout_seconds": effective_context_timeout,
                        "enabled": True,
                    },
                )
                .returning(Scanner.id)
            )
            await session.execute(
                insert(ScannerContext)
                .values(scanner_id=scanner_id)
                .on_conflict_do_nothing(index_elements=[ScannerContext.scanner_id])
            )
        await engine.dispose()
        typer.echo(f"scanner {code} ready")

    _run(execute())


@app.command("recover-ignored-adds")
def recover_ignored_adds_command(
    scanner_code: str = typer.Option(..., help="Scanner whose ignored reads should be recovered."),
    failed_command: str = typer.Option(
        "CMD_MODE_ADD", help="Failed ADD payload that anchors the recovery window."
    ),
    since_minutes: int = typer.Option(60, min=1),
    apply: bool = typer.Option(False, "--apply", help="Create inventory items for candidates."),
) -> None:
    """Preview or idempotently recover no-mode reads after a failed ADD command."""

    async def execute() -> dict:
        settings = get_settings()
        engine = create_engine(settings.database_url)
        sessions = create_session_factory(engine)
        try:
            result = await recover_ignored_adds_service(
                sessions,
                settings,
                scanner_code=scanner_code,
                failed_command=failed_command,
                since=datetime.now(UTC) - timedelta(minutes=since_minutes),
                apply=apply,
            )
            return result.as_dict()
        finally:
            await engine.dispose()

    try:
        result = asyncio.run(execute())
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))
    if result["failures"]:
        raise typer.Exit(1)


def _off_client(settings):
    return OpenFoodFactsClient(
        base_url=settings.off_base_url,
        api_version=settings.off_api_version,
        user_agent=settings.off_user_agent,
        requests_per_minute=settings.off_requests_per_minute,
        connect_timeout=settings.off_connect_timeout_seconds,
        total_timeout=settings.off_total_timeout_seconds,
    )


def _product_client(settings):
    primary = _off_client(settings)
    fallbacks = []
    if settings.upcitemdb_enabled:
        fallbacks.append(
            UpcItemDbClient(
                base_url=settings.upcitemdb_base_url,
                requests_per_minute=settings.upcitemdb_requests_per_minute,
                connect_timeout=settings.off_connect_timeout_seconds,
                total_timeout=settings.off_total_timeout_seconds,
                user_key=settings.upcitemdb_user_key,
                key_type=settings.upcitemdb_key_type,
            )
        )
    return ProductProviderChain(primary, fallbacks)


@app.command()
def enrich(
    barcode: str = typer.Option(..., help="GTIN to enqueue and process once."),
) -> None:
    """Enqueue one product and immediately run one enrichment job."""

    async def execute() -> None:
        settings = get_settings()
        normalized = normalize_gtin(
            barcode, accept_invalid_check_digit=settings.accept_invalid_check_digit
        )
        engine = create_engine(settings.database_url)
        sessions = create_session_factory(engine)
        async with sessions() as session, session.begin():
            await session.execute(
                insert(Product)
                .values(
                    barcode=normalized.barcode,
                    barcode_format=normalized.barcode_format,
                    enrichment_status=EnrichmentStatus.PENDING,
                )
                .on_conflict_do_nothing(index_elements=[Product.barcode])
            )
            await session.execute(
                insert(EnrichmentJob)
                .values(
                    id=uuid4(),
                    product_barcode=normalized.barcode,
                    status=EnrichmentStatus.PENDING,
                    next_attempt_at=func.now(),
                )
                .on_conflict_do_update(
                    index_elements=[EnrichmentJob.product_barcode],
                    set_={
                        "status": EnrichmentStatus.PENDING,
                        "next_attempt_at": func.now(),
                        "locked_at": None,
                        "locked_by": None,
                    },
                )
            )
        client = _product_client(settings)
        try:
            worked = await EnrichmentWorker(sessions, client, settings).run_once(
                barcode=normalized.barcode
            )
            typer.echo("enrichment processed" if worked else "no due enrichment job")
        finally:
            await client.aclose()
            await engine.dispose()

    _run(execute())


@app.command("sync-category-taxonomy")
def sync_category_taxonomy() -> None:
    """Replace inferred category parents with Open Food Facts taxonomy parents."""

    async def execute() -> dict[str, int]:
        settings = get_settings()
        engine = create_engine(settings.database_url)
        sessions = create_session_factory(engine)
        client = _off_client(settings)
        try:
            async with sessions() as session:
                category_ids = list(
                    await session.scalars(
                        select(FoodCategory.id).where(
                            FoodCategory.active.is_(True),
                            FoodCategory.id.like("en:%"),
                        )
                    )
                )
            parents: dict[str, str | None] = {}
            for offset in range(0, len(category_ids), 50):
                parents.update(
                    await client.fetch_category_parents(category_ids[offset : offset + 50])
                )
            updated = 0
            manual = 0
            unresolved = 0
            async with sessions() as session, session.begin():
                existing_ids = set(await session.scalars(select(FoodCategory.id)))
                for category_id in category_ids:
                    if category_id not in parents:
                        unresolved += 1
                        continue
                    category = await session.get(FoodCategory, category_id)
                    assert category is not None
                    if category.parent_source == "MANUAL":
                        manual += 1
                        continue
                    parent_id = parents[category_id]
                    if parent_id is not None and parent_id not in existing_ids:
                        session.add(
                            FoodCategory(
                                id=parent_id,
                                display_name=category_display_name(parent_id),
                                active=True,
                            )
                        )
                        existing_ids.add(parent_id)
                    category.parent_id = parent_id
                    category.parent_source = "OPEN_FOOD_FACTS_TAXONOMY"
                    updated += 1
            return {
                "requested": len(category_ids),
                "updated": updated,
                "manual": manual,
                "unresolved": unresolved,
            }
        finally:
            await client.aclose()
            await engine.dispose()

    typer.echo(json.dumps(asyncio.run(execute()), indent=2, sort_keys=True))


@app.command("retry-enrichment")
def retry_enrichment(failed: bool = typer.Option(False, "--failed")) -> None:
    """Move failed enrichment jobs back to the pending queue."""
    if not failed:
        raise typer.BadParameter("specify --failed")

    async def execute() -> None:
        settings = get_settings()
        engine = create_engine(settings.database_url)
        sessions = create_session_factory(engine)
        async with sessions() as session, session.begin():
            barcodes = list(
                await session.scalars(
                    select(EnrichmentJob.product_barcode).where(
                        EnrichmentJob.status == EnrichmentStatus.FAILED
                    )
                )
            )
            result = await session.execute(
                update(EnrichmentJob)
                .where(EnrichmentJob.status == EnrichmentStatus.FAILED)
                .values(
                    status=EnrichmentStatus.PENDING,
                    next_attempt_at=func.now(),
                    locked_at=None,
                    locked_by=None,
                    last_error=None,
                )
            )
            await session.execute(
                update(Product)
                .where(Product.barcode.in_(barcodes))
                .values(enrichment_status=EnrichmentStatus.PENDING)
            )
        await engine.dispose()
        typer.echo(f"queued {result.rowcount or 0} failed job(s)")

    _run(execute())


@app.command("reconcile-stock-policies")
def reconcile_stock_policies() -> None:
    """Rebuild automatic grocery demand from current physical stock."""

    async def execute() -> None:
        settings = get_settings()
        engine = create_engine(settings.database_url)
        sessions = create_session_factory(engine)
        async with sessions() as session:
            barcodes = list(await session.scalars(select(ProductStockPolicy.product_barcode)))
            group_ids = list(await session.scalars(select(StockGroupPolicy.stock_group_id)))
        for barcode in barcodes:
            async with sessions() as session, session.begin():
                await recalculate_stock_policy(session, barcode)
        for group_id in group_ids:
            async with sessions() as session, session.begin():
                await recalculate_stock_group_policy(session, group_id)
        await engine.dispose()
        typer.echo(
            f"reconciled {len(barcodes)} product and {len(group_ids)} stock-group policy/policies"
        )

    _run(execute())


INVARIANT_QUERIES = {
    "applied_add_without_one_item": """
        SELECT count(*) FROM scan_events s
        WHERE s.kind = 'PRODUCT' AND s.mode_at_processing = 'ADD' AND s.result = 'APPLIED'
          AND (SELECT count(*) FROM inventory_items i WHERE i.add_scan_event_id = s.id) <> 1
    """,
    "item_without_one_added_event": """
        SELECT count(*) FROM inventory_items i
        WHERE (SELECT count(*) FROM inventory_events e
               WHERE e.inventory_item_id = i.id AND e.event_type = 'ADDED') <> 1
    """,
    "item_with_multiple_terminal_events": """
        SELECT count(*) FROM inventory_items i
        WHERE (SELECT count(*) FROM inventory_events e
               WHERE e.inventory_item_id = i.id
                 AND e.event_type IN ('REMOVED', 'TRASHED')) > 1
    """,
    "terminal_state_mismatch": """
        SELECT count(*) FROM inventory_items
        WHERE (status = 'IN_STOCK'
               AND (removed_at IS NOT NULL OR terminal_scan_event_id IS NOT NULL))
           OR (status IN ('REMOVED', 'TRASHED')
               AND (removed_at IS NULL OR terminal_scan_event_id IS NULL))
    """,
    "estimated_expiration_without_snapshot": """
        SELECT count(*) FROM inventory_items
        WHERE expiration_is_estimated
          AND (expiration_date IS NULL OR expiration_category_id IS NULL
               OR expiration_offset_days IS NULL)
    """,
    "grouped_product_with_exact_policy": """
        SELECT count(*)
        FROM stock_group_products membership
        JOIN product_stock_policies policy
          ON policy.product_barcode = membership.product_barcode
    """,
}


@app.command("validate-invariants")
def validate_invariants(json_output: bool = typer.Option(False, "--json")) -> None:
    """Check ledger invariants and exit nonzero when violations exist."""

    async def execute() -> None:
        settings = get_settings()
        engine = create_engine(settings.database_url)
        violations = {}
        async with engine.connect() as connection:
            for name, query in INVARIANT_QUERIES.items():
                count = await connection.scalar(text(query))
                if count:
                    violations[name] = count
        await engine.dispose()
        if json_output:
            typer.echo(json.dumps({"ok": not violations, "violations": violations}, sort_keys=True))
        elif violations:
            for name, count in violations.items():
                typer.echo(f"{name}: {count}", err=True)
        else:
            typer.echo("all invariants hold")
        if violations:
            raise typer.Exit(1)

    _run(execute())


@app.command("scanner-test")
def scanner_test(
    device: str = typer.Option(...),
    scanner_code: str | None = typer.Option(None),
    baudrate: int = typer.Option(9600, min=1),
) -> None:
    """Read and validate frames from one serial device until interrupted."""

    async def execute() -> None:
        source = SerialScannerSource(device, baudrate)
        try:
            async for raw in source.frames():
                try:
                    parsed = parse_frame(raw, expected_scanner_code=scanner_code)
                    typer.echo(f"{parsed.scanner_code}\t{parsed.kind.value}\t{parsed.payload}")
                except ValueError as exc:
                    typer.echo(f"REJECTED\t{exc}", err=True)
        finally:
            await source.close()

    try:
        _run(execute())
    except KeyboardInterrupt:
        pass
