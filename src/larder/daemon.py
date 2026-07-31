from __future__ import annotations

import asyncio
import signal
import time
from collections import defaultdict

import structlog

from larder.config import Settings
from larder.db import create_engine, create_session_factory
from larder.enrichment.factory import build_product_client
from larder.enrichment.worker import EnrichmentWorker
from larder.scanner.serial import SerialScannerSource
from larder.scanner.supervisor import supervise_reader
from larder.schemas.scans import ScanEnvelope
from larder.services import CommandRouter

log = structlog.get_logger()


async def run_daemon(settings: Settings) -> None:
    engine = create_engine(settings.database_url)
    sessions = create_session_factory(engine)
    router = CommandRouter(sessions, settings)
    product_client = build_product_client(settings)
    enrichment = EnrichmentWorker(sessions, product_client, settings)
    queue: asyncio.Queue[ScanEnvelope] = asyncio.Queue(settings.queue_size)
    stop = asyncio.Event()
    scanner_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
    active: set[asyncio.Task] = set()

    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except NotImplementedError:
            # Windows' default event loop does not implement add_signal_handler.
            signal.signal(signum, lambda *_args: loop.call_soon_threadsafe(stop.set))

    async def handle(envelope: ScanEnvelope) -> None:
        code = envelope.expected_scanner_code or envelope.port
        try:
            async with scanner_locks[code]:
                delay = 1.0
                first_attempt = True
                while first_attempt or not stop.is_set():
                    first_attempt = False
                    try:
                        outcome = await router.process(envelope)
                        log.info(
                            "scan_processed",
                            scan_event_id=str(outcome.scan_event_id),
                            scanner_code=code,
                            barcode=outcome.barcode,
                            result=outcome.result.value,
                            affected_item_id=str(outcome.item_id) if outcome.item_id else None,
                            latency_ms=round(
                                (time.monotonic() - envelope.received_monotonic) * 1000,
                                2,
                            ),
                            error_code=outcome.error_code,
                        )
                        break
                    except Exception as exc:
                        log.error(
                            "scan_database_retry",
                            scan_event_id=str(envelope.id),
                            error=type(exc).__name__,
                        )
                        if stop.is_set():
                            break
                        try:
                            await asyncio.wait_for(stop.wait(), timeout=delay)
                        except TimeoutError:
                            pass
                        delay = min(delay * 2, 30.0)
        finally:
            queue.task_done()

    async def process_scans() -> None:
        while not stop.is_set() or not queue.empty():
            try:
                envelope = await asyncio.wait_for(queue.get(), timeout=0.5)
            except TimeoutError:
                continue
            task = asyncio.create_task(handle(envelope))
            active.add(task)
            task.add_done_callback(active.discard)
            while len(active) >= 32:
                await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
        if active:
            await asyncio.gather(*active, return_exceptions=True)

    async def enrich_loop() -> None:
        while not stop.is_set():
            try:
                worked = await enrichment.run_once()
                if not worked:
                    await asyncio.wait_for(stop.wait(), timeout=5)
            except TimeoutError:
                pass
            except Exception as exc:
                log.error("enrichment_worker_error", error=type(exc).__name__)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=5)
                except TimeoutError:
                    pass

    reader_tasks = [
        asyncio.create_task(
            supervise_reader(
                scanner_code=code,
                port=device.device,
                source_factory=lambda d=device: SerialScannerSource(d.device, d.baudrate),
                queue=queue,
                stop=stop,
            ),
            name=f"reader-{code}",
        )
        for code, device in settings.scanners.items()
    ]
    processor = asyncio.create_task(process_scans(), name="scan-processor")
    enrich_task = asyncio.create_task(enrich_loop(), name="enrichment-worker")
    log.info(
        "daemon_started",
        scanners=list(settings.scanners),
        queue_size=settings.queue_size,
    )
    await stop.wait()
    log.info("daemon_stopping", queue_depth=queue.qsize())
    for task in reader_tasks:
        task.cancel()
    await asyncio.gather(*reader_tasks, return_exceptions=True)
    try:
        await asyncio.wait_for(queue.join(), timeout=15)
    except TimeoutError:
        log.warning("scan_drain_timeout", queue_depth=queue.qsize())
    await processor
    await enrich_task
    await product_client.aclose()
    await engine.dispose()
    log.info("daemon_stopped")
