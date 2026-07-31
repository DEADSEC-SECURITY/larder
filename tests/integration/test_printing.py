import httpx
import pytest

from larder.api import create_app
from larder.config import Settings

pytestmark = pytest.mark.integration


def app_for(session_factory, database_url):
    settings = Settings(database_url=database_url, scan_api_token="print-test-token")
    app = create_app(settings)
    app.state.settings = settings
    app.state.session_factory = session_factory
    return app


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://print-test")


@pytest.mark.asyncio
async def test_print_job_queue_claim_and_complete(session_factory, database_url):
    app = app_for(session_factory, database_url)
    headers = {"Authorization": "Bearer print-test-token"}
    async with client_for(app) as client:
        created = await client.post(
            "/v1/web/print-jobs",
            json={
                "kind": "product",
                "value": "0096619120291",
                "title": "cOLOMBIAN SUPREMO COFFEE",
                "subtitle": "product barcode",
            },
        )
        assert created.status_code == 201, created.text
        job_id = created.json()["id"]
        assert created.json()["title"] == "Colombian Supremo Coffee"
        assert created.json()["subtitle"] == "Product Barcode"
        assert created.json()["status"] == "pending"

        unauthorized = await client.post("/v1/print-jobs/claim", json={"bridge_id": "test-bridge"})
        assert unauthorized.status_code == 401

        claimed = await client.post(
            "/v1/print-jobs/claim",
            json={"bridge_id": "test-bridge"},
            headers=headers,
        )
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()["job_id"] == job_id
        assert claimed.json()["value"] == "0096619120291"

        completed = await client.post(
            f"/v1/print-jobs/{job_id}/complete",
            json={"bridge_id": "test-bridge"},
            headers=headers,
        )
        assert completed.json()["status"] == "printed"
        status = await client.get(f"/v1/web/print-jobs/{job_id}")
        assert status.json()["status"] == "printed"
        assert status.json()["completedAt"] is not None

        empty = await client.post(
            "/v1/print-jobs/claim",
            json={"bridge_id": "test-bridge"},
            headers=headers,
        )
        assert empty.status_code == 204


@pytest.mark.asyncio
async def test_print_label_text_is_canonical_for_every_barcode_kind(session_factory, database_url):
    app = app_for(session_factory, database_url)
    async with client_for(app) as client:
        add_mode = (
            await client.post(
                "/v1/web/print-jobs",
                json={
                    "kind": "control",
                    "value": "CMD:MODE:ADD",
                    "title": "wrong title",
                    "subtitle": "wrong subtitle",
                },
            )
        ).json()
        assert (add_mode["title"], add_mode["subtitle"]) == (
            "Add Item",
            "Scanner Command",
        )

        list_mode = (
            await client.post(
                "/v1/web/print-jobs",
                json={
                    "kind": "control",
                    "value": "CMD:MODE:LIST",
                    "title": "wrong title",
                },
            )
        ).json()
        assert (list_mode["title"], list_mode["subtitle"]) == (
            "Add To List",
            "Scanner Command",
        )

        await client.post("/v1/web/stores", json={"name": "cOSTCO"})
        commands = (await client.get("/v1/web/commands")).json()["commands"]
        store_command = next(row for row in commands if row["commandType"] == "SET_STORE")
        store_label = (
            await client.post(
                "/v1/web/print-jobs",
                json={
                    "kind": "control",
                    "value": store_command["payload"],
                    "title": "wrong title",
                },
            )
        ).json()
        assert (store_label["title"], store_label["subtitle"]) == (
            "Costco",
            "Store Control",
        )

        location = (
            await client.post("/v1/web/storage-locations", json={"name": "fREEZER"})
        ).json()
        location_control = await client.post(
            f"/v1/web/storage-locations/{location['id']}/control-barcode"
        )
        assert location_control.status_code == 200, location_control.text
        assert location_control.json()["payload"] == "CMD:LOC:freezer"

        commands = (await client.get("/v1/web/commands")).json()["commands"]
        location_command = next(
            row for row in commands if row["commandType"] == "SET_LOCATION"
        )
        assert location_command["storageLocationName"] == "Freezer"
        assert location_command["storageLocationActive"] is True

        location_label = (
            await client.post(
                "/v1/web/print-jobs",
                json={
                    "kind": "control",
                    "value": location_control.json()["payload"],
                    "title": "wrong title",
                },
            )
        ).json()
        assert (location_label["title"], location_label["subtitle"]) == (
            "Freezer",
            "Location Control",
        )

        generic = (
            await client.post(
                "/v1/web/print-jobs",
                json={
                    "kind": "generic",
                    "value": "CUSTOM-123",
                    "title": "mY custom BARCODE",
                    "subtitle": "ignored",
                },
            )
        ).json()
        assert (generic["title"], generic["subtitle"]) == (
            "My Custom Barcode",
            "Barcode",
        )


@pytest.mark.asyncio
async def test_commands_listing_includes_seeded_and_store_commands(session_factory, database_url):
    app = app_for(session_factory, database_url)
    async with client_for(app) as client:
        await client.post("/v1/web/stores", json={"name": "Costco"})

        listed = await client.get("/v1/web/commands")
        assert listed.status_code == 200, listed.text
        commands = {row["payload"]: row for row in listed.json()["commands"]}

        assert commands["CMD:MODE:ADD"]["commandType"] == "SET_MODE"
        assert commands["CMD:MODE:ADD"]["mode"] == "ADD"
        assert commands["CMD:MODE:LIST"]["mode"] == "LIST"
        assert commands["CMD:MODE:CLEAR"]["commandType"] == "CLEAR_MODE"

        store_commands = [row for row in commands.values() if row["commandType"] == "SET_STORE"]
        assert any(
            row["storeName"] == "Costco" and row["storeActive"] is True for row in store_commands
        )
        # Mode commands sort ahead of per-store commands.
        payloads = [row["payload"] for row in listed.json()["commands"]]
        assert payloads.index("CMD:MODE:ADD") < payloads.index(store_commands[0]["payload"])


@pytest.mark.asyncio
async def test_nonretryable_print_failure_is_visible(session_factory, database_url):
    app = app_for(session_factory, database_url)
    headers = {"Authorization": "Bearer print-test-token"}
    async with client_for(app) as client:
        created = (
            await client.post(
                "/v1/web/print-jobs",
                json={"kind": "control", "value": "CMD:MODE:ADD", "title": "Add Item"},
            )
        ).json()
        await client.post(
            "/v1/print-jobs/claim",
            json={"bridge_id": "test-bridge"},
            headers=headers,
        )
        failed = await client.post(
            f"/v1/print-jobs/{created['id']}/fail",
            json={
                "bridge_id": "test-bridge",
                "error": "invalid label",
                "retryable": False,
            },
            headers=headers,
        )
        assert failed.json()["status"] == "failed"
        status = (await client.get(f"/v1/web/print-jobs/{created['id']}")).json()
        assert status["lastError"] == "invalid label"
