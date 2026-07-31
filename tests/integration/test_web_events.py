import asyncio

import httpx
import pytest

from larder.api import create_app
from larder.config import Settings
from larder.webapi.events import ChangeBroadcaster, format_event, listen_for_changes

pytestmark = pytest.mark.integration


def ipv4_url(database_url: str) -> str:
    # Docker Desktop on Windows can resolve `localhost` to ::1 where the
    # published port does not deliver LISTEN traffic; pin IPv4 for the test.
    return database_url.replace("localhost", "127.0.0.1")


def web_app(session_factory, database_url):
    settings = Settings(database_url=database_url, scan_api_token="test-token")
    app = create_app(settings)
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.broadcaster = ChangeBroadcaster()
    return app


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_pg_triggers_reach_broadcaster(session_factory, database_url):
    """A write through the web API must surface on the LISTEN channel."""
    app = web_app(session_factory, database_url)
    broadcaster = app.state.broadcaster
    listener = asyncio.create_task(listen_for_changes(ipv4_url(database_url), broadcaster))
    try:
        await asyncio.sleep(1.0)  # let the LISTEN connection establish
        queue = broadcaster.subscribe()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.post(
                "/v1/web/quick-add", json={"name": "Live Milk", "quantity": 1}
            )
            assert response.status_code == 201, response.text

        changed: set[str] = set()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 10
        while loop.time() < deadline and not ({"inventory_items", "products"} <= changed):
            remaining = max(0.1, deadline - loop.time())
            try:
                changed |= await asyncio.wait_for(queue.get(), timeout=remaining)
            except TimeoutError:
                break
        assert "inventory_items" in changed
        assert "products" in changed
    finally:
        listener.cancel()
        try:
            await listener
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_sse_stream_emits_batches(session_factory, database_url):
    """The /events stream yields a hello comment, then coalesced batches.

    The generator is driven directly: httpx's ASGITransport buffers streaming
    responses, so an endless SSE body cannot be consumed through it. The HTTP
    layer itself is plain StreamingResponse plumbing.
    """
    from starlette.requests import Request

    app = web_app(session_factory, database_url)
    broadcaster = app.state.broadcaster

    scope = {"type": "http", "method": "GET", "path": "/v1/web/events", "app": app}
    from larder.webapi.events import change_events

    response = await change_events(Request(scope))
    assert response.media_type == "text/event-stream"
    assert response.headers["x-accel-buffering"] == "no"

    iterator = response.body_iterator
    first = await anext(iterator)
    assert first == b": connected\n\n"

    broadcaster.publish({"scan_events", "inventory_items"})
    second = await asyncio.wait_for(anext(iterator), timeout=5)
    assert second == format_event({"scan_events", "inventory_items"})

    await iterator.aclose()
    # Closing the stream unsubscribes the client.
    broadcaster.publish({"products"})
