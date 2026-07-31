"""Live-update fan-out: PostgreSQL LISTEN/NOTIFY → Server-Sent Events.

Migration 0003 installs statement triggers that `pg_notify('larder_changes',
<table>)` on every relevant write, so changes from the scanner daemon and the
enrichment worker are pushed to browsers even though they run in separate
processes. One listener connection per API process feeds an in-memory
broadcaster; `/v1/web/events` streams coalesced change batches to clients.
"""

from __future__ import annotations

import asyncio
import json

import asyncpg
import structlog
from fastapi import Request
from fastapi.responses import StreamingResponse

from larder.db import connection_options
from larder.webapi.common import web_api_router

CHANNEL = "larder_changes"
COALESCE_SECONDS = 0.2
KEEPALIVE_SECONDS = 25
RECONNECT_SECONDS = 3

logger = structlog.get_logger(__name__)

router = web_api_router(tags=["events"])


class ChangeBroadcaster:
    """Fans change batches out to every connected SSE subscriber."""

    def __init__(self, *, queue_size: int = 64) -> None:
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[set[str]]] = set()

    def subscribe(self) -> asyncio.Queue[set[str]]:
        queue: asyncio.Queue[set[str]] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[set[str]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, tables: set[str]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(set(tables))
            except asyncio.QueueFull:
                # A slow client already has a full refresh pending; dropping
                # this batch loses nothing.
                pass


async def _listen_once(url, connect_args: dict, broadcaster: ChangeBroadcaster) -> None:
    connection = await asyncpg.connect(
        host=url.host,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        database=url.database,
        timeout=10,
        **connect_args,
    )
    pending: set[str] = set()
    arrived = asyncio.Event()

    def on_notify(_conn, _pid, _channel, payload: str) -> None:
        pending.add(payload)
        arrived.set()

    try:
        await connection.add_listener(CHANNEL, on_notify)
        logger.info("web_events_listener_connected")
        while True:
            await arrived.wait()
            # Let a burst (e.g. one scan touching four tables) coalesce into a
            # single client refresh.
            await asyncio.sleep(COALESCE_SECONDS)
            arrived.clear()
            batch = set(pending)
            pending.clear()
            if batch:
                broadcaster.publish(batch)
    finally:
        # terminate() is synchronous and immediate — a graceful close() can
        # block during task cancellation.
        try:
            connection.terminate()
        except Exception:  # noqa: BLE001
            pass


async def listen_for_changes(database_url: str, broadcaster: ChangeBroadcaster) -> None:
    """Hold a LISTEN connection open forever, reconnecting on failure."""
    url, connect_args = connection_options(database_url)
    while True:
        try:
            await _listen_once(url, connect_args, broadcaster)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — any failure means reconnect
            logger.warning("web_events_listener_error", error=str(exc))
            await asyncio.sleep(RECONNECT_SECONDS)


def format_event(tables: set[str]) -> bytes:
    return f"data: {json.dumps({'tables': sorted(tables)})}\n\n".encode()


@router.get("/events")
async def change_events(request: Request) -> StreamingResponse:
    broadcaster: ChangeBroadcaster = request.app.state.broadcaster

    async def stream():
        queue = broadcaster.subscribe()
        try:
            yield b": connected\n\n"
            while True:
                try:
                    tables = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                    yield format_event(tables)
                except TimeoutError:
                    yield b": ping\n\n"
        finally:
            broadcaster.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # nginx honors this even where proxy buffering is on.
            "X-Accel-Buffering": "no",
        },
    )
