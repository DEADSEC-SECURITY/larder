import httpx
import pytest
from sqlalchemy import select

from larder.api import create_app
from larder.config import Settings
from larder.models import EnrichmentJob, Product

pytestmark = pytest.mark.integration


def web_app(session_factory, database_url):
    settings = Settings(database_url=database_url, scan_api_token="test-token")
    app = create_app(settings)
    app.state.settings = settings
    app.state.session_factory = session_factory
    return app


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://web-test")


@pytest.mark.asyncio
async def test_resolve_registers_unknown_barcode_and_queues_enrichment(
    session_factory, database_url
):
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        response = await client.post(
            "/v1/web/barcodes/resolve", json={"barcode": "012000161155"}
        )
        assert response.status_code == 200, response.text
        body = response.json()

    # A 12-digit UPC-A is stored zero-padded to its 13-digit canonical form.
    assert body["barcode"] == "0012000161155"
    assert body["raw"] == "012000161155"
    assert body["barcodeFormat"] == "UPC-A"
    assert body["created"] is True
    assert body["idStatus"] == "pending"
    assert body["inStock"] == 0

    async with session_factory() as session:
        product = await session.get(Product, "0012000161155")
        assert product is not None
        job = (
            await session.execute(
                select(EnrichmentJob).where(EnrichmentJob.product_barcode == "0012000161155")
            )
        ).scalar_one()
        assert job.status == "PENDING"


@pytest.mark.asyncio
async def test_resolve_is_idempotent_across_equivalent_forms(session_factory, database_url):
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        first = await client.post("/v1/web/barcodes/resolve", json={"barcode": "012000161155"})
        # Same GTIN, already-canonical 13-digit spelling.
        second = await client.post("/v1/web/barcodes/resolve", json={"barcode": "0012000161155"})

    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert first.json()["barcode"] == second.json()["barcode"]

    async with session_factory() as session:
        products = (await session.execute(select(Product.barcode))).scalars().all()
        assert products == ["0012000161155"]


@pytest.mark.asyncio
async def test_resolve_rejects_bad_input_with_a_machine_readable_code(
    session_factory, database_url
):
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        cases = {
            "0012000161150": "GTIN_BAD_CHECK_DIGIT",
            "ABC123": "GTIN_NOT_DECIMAL",
            "12345": "GTIN_BAD_LENGTH",
            # MANUAL-* ids are internal handles, never scannable barcodes.
            "MANUAL-A1B2C3D4E5": "MANUAL_BARCODE",
        }
        for barcode, expected in cases.items():
            response = await client.post("/v1/web/barcodes/resolve", json={"barcode": barcode})
            assert response.status_code == 422, (barcode, response.text)
            assert response.json()["detail"]["code"] == expected, barcode

    async with session_factory() as session:
        assert (await session.execute(select(Product.barcode))).scalars().all() == []


@pytest.mark.asyncio
async def test_resolved_barcode_can_be_quick_added(session_factory, database_url):
    """The whole point: quick-add 404s on a barcode the catalogue has not seen."""
    app = web_app(session_factory, database_url)
    async with client_for(app) as client:
        missing = await client.post("/v1/web/quick-add", json={"barcode": "0012000161155"})
        assert missing.status_code == 404

        resolved = await client.post(
            "/v1/web/barcodes/resolve", json={"barcode": "0012000161155"}
        )
        assert resolved.status_code == 200

        added = await client.post(
            "/v1/web/quick-add", json={"barcode": "0012000161155", "quantity": 2}
        )
        assert added.status_code == 201, added.text
        assert added.json()["created"] == 2

        after = await client.post("/v1/web/barcodes/resolve", json={"barcode": "0012000161155"})
        assert after.json()["inStock"] == 2


@pytest.mark.asyncio
async def test_resolve_honours_the_check_digit_override(session_factory, database_url):
    settings = Settings(
        database_url=database_url,
        scan_api_token="test-token",
        accept_invalid_check_digit=True,
    )
    app = create_app(settings)
    app.state.settings = settings
    app.state.session_factory = session_factory

    async with client_for(app) as client:
        # Configured leniency applies by default...
        lenient = await client.post(
            "/v1/web/barcodes/resolve", json={"barcode": "0012000161150"}
        )
        assert lenient.status_code == 200, lenient.text

        # ...and the request can still opt back into strict validation.
        strict = await client.post(
            "/v1/web/barcodes/resolve",
            json={"barcode": "0012000161151", "acceptInvalidCheckDigit": False},
        )
        assert strict.status_code == 422
        assert strict.json()["detail"]["code"] == "GTIN_BAD_CHECK_DIGIT"
