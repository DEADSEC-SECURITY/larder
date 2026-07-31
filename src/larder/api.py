from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

from larder.config import Settings, get_settings
from larder.db import create_engine, create_session_factory
from larder.enrichment.factory import build_product_client
from larder.enrichment.worker import EnrichmentWorker
from larder.schemas.scans import ScanEnvelope
from larder.services.command_router import CommandRouter
from larder.services.printing import claim_print_job, complete_print_job, fail_print_job
from larder.webapi import build_web_router
from larder.webapi.auth import install_web_auth
from larder.webapi.events import ChangeBroadcaster, listen_for_changes


class ScanRequest(BaseModel):
    event_id: UUID
    scanner_code: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    payload: str = Field(min_length=1, max_length=4096)
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("received_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("received_at must include a timezone")
        return value

    @field_validator("payload")
    @classmethod
    def reject_newlines(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("payload cannot contain a newline")
        return value


class ScanResponse(BaseModel):
    event_id: UUID
    result: str
    barcode: str | None
    item_id: UUID | None
    intake_batch_id: UUID | None
    error_code: str | None
    duplicate: bool


class PrintBridgeRequest(BaseModel):
    bridge_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")


class PrintFailureRequest(PrintBridgeRequest):
    error: str = Field(min_length=1, max_length=1000)
    retryable: bool = True


def build_envelope(scan: ScanRequest) -> ScanEnvelope:
    raw_frame = f"LDR:{scan.scanner_code}|{scan.payload}\n".encode()
    return ScanEnvelope(
        id=scan.event_id,
        raw_frame=raw_frame,
        port=f"bridge:{scan.scanner_code}",
        expected_scanner_code=scan.scanner_code,
        received_at=scan.received_at,
    )


def verify_bearer(authorization: str | None, expected_token: str) -> None:
    if not expected_token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "scan API token is not configured")
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token, expected_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid scanner credential")


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = create_engine(configured.database_url)
        app.state.engine = engine
        app.state.settings = configured
        app.state.session_factory = create_session_factory(engine)
        app.state.router = CommandRouter(app.state.session_factory, configured)
        product_client = build_product_client(configured)
        app.state.enrichment_worker = EnrichmentWorker(
            app.state.session_factory, product_client, configured
        )
        app.state.broadcaster = ChangeBroadcaster()
        listener = asyncio.create_task(
            listen_for_changes(configured.database_url, app.state.broadcaster)
        )
        yield
        listener.cancel()
        with suppress(asyncio.CancelledError):
            await listener
        await product_client.aclose()
        await engine.dispose()

    application = FastAPI(title="Larder API", version="1", lifespan=lifespan)

    # Added before CORS so it ends up *inside* it: a 401 from the guard still
    # carries the CORS headers a cross-origin dev server needs to read it.
    install_web_auth(application, configured)

    cors_origins = [o.strip() for o in configured.web_cors_origins.split(",") if o.strip()]
    if cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
            # The session cookie has to survive a cross-origin dev server.
            allow_credentials=True,
        )

    application.include_router(build_web_router())

    @application.get("/healthz")
    async def health(request: Request) -> dict[str, str]:
        async with request.app.state.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return {"status": "ready"}

    @application.post("/v1/scans", response_model=ScanResponse)
    async def receive_scan(
        scan: ScanRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> ScanResponse:
        verify_bearer(authorization, configured.scan_api_token)
        outcome = await request.app.state.router.process(build_envelope(scan))
        return ScanResponse(
            event_id=outcome.scan_event_id,
            result=outcome.result.value,
            barcode=outcome.barcode,
            item_id=outcome.item_id,
            intake_batch_id=outcome.intake_batch_id,
            error_code=outcome.error_code,
            duplicate=outcome.duplicate,
        )

    @application.post("/v1/print-jobs/claim", response_model=None)
    async def claim_print(
        payload: PrintBridgeRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict | Response:
        verify_bearer(authorization, configured.scan_api_token)
        async with request.app.state.session_factory() as session, session.begin():
            job = await claim_print_job(session, payload.bridge_id)
            if job is None:
                return Response(status_code=status.HTTP_204_NO_CONTENT)
            return {
                "job_id": str(job.id),
                "kind": job.kind.lower(),
                "value": job.value,
                "title": job.title,
                "subtitle": job.subtitle,
                "copies": job.copies,
                "attempt": job.attempt_count,
            }

    @application.post("/v1/print-jobs/{job_id}/complete")
    async def complete_print(
        job_id: UUID,
        payload: PrintBridgeRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict:
        verify_bearer(authorization, configured.scan_api_token)
        async with request.app.state.session_factory() as session, session.begin():
            try:
                job = await complete_print_job(session, job_id, payload.bridge_id)
            except LookupError as error:
                raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
            except PermissionError as error:
                raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
            return {"job_id": str(job.id), "status": job.status.lower()}

    @application.post("/v1/print-jobs/{job_id}/fail")
    async def fail_print(
        job_id: UUID,
        payload: PrintFailureRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict:
        verify_bearer(authorization, configured.scan_api_token)
        async with request.app.state.session_factory() as session, session.begin():
            try:
                job = await fail_print_job(
                    session,
                    job_id,
                    payload.bridge_id,
                    payload.error,
                    retryable=payload.retryable,
                )
            except LookupError as error:
                raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
            except PermissionError as error:
                raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
            return {"job_id": str(job.id), "status": job.status.lower()}

    return application


app = create_app()
