from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from larder.domain.names import standardize_name
from larder.models import ControlBarcode, PrintJob, Product, StorageLocation, Store

_MODE_TITLES = {
    "ADD": "Add Item",
    "LIST": "Add To List",
    "REMOVE": "Remove Item",
    "TRASH": "Trash Item",
}


async def resolve_print_label(
    session: AsyncSession,
    *,
    kind: str,
    value: str,
    title: str,
) -> tuple[str, str]:
    """Return canonical label text independently of the requesting UI."""
    fallback_title = standardize_name(title)
    normalized_kind = kind.upper()

    if normalized_kind == "PRODUCT":
        product = await session.get(Product, value)
        canonical_title = product.name if product and product.name else fallback_title
        return standardize_name(canonical_title), "Product Barcode"

    if normalized_kind == "CONTROL":
        control = await session.get(ControlBarcode, value)
        if control is not None:
            if control.command_type == "SET_STORE" and control.store_id is not None:
                store = await session.get(Store, control.store_id)
                canonical_title = store.name if store is not None else fallback_title
                return standardize_name(canonical_title), "Store Control"
            if control.command_type == "CLEAR_STORE":
                return "Clear Store", "Store Control"
            if (
                control.command_type == "SET_LOCATION"
                and control.storage_location_id is not None
            ):
                location = await session.get(StorageLocation, control.storage_location_id)
                canonical_title = location.name if location is not None else fallback_title
                return standardize_name(canonical_title), "Location Control"
            if control.command_type == "CLEAR_LOCATION":
                return "Clear Location", "Location Control"
            if control.command_type == "CLEAR_MODE":
                return "Clear Mode", "Scanner Command"
            if control.command_type == "SET_MODE" and control.mode is not None:
                mode = control.mode.value
                return _MODE_TITLES.get(mode, standardize_name(mode)), "Scanner Command"

        if value.startswith("CMD:STORE:"):
            return fallback_title, "Store Control"
        if value.startswith("CMD:LOC:"):
            return fallback_title, "Location Control"
        return fallback_title, "Scanner Command"

    return fallback_title, "Barcode"


async def claim_print_job(
    session: AsyncSession, bridge_id: str, *, lease_seconds: int = 60
) -> PrintJob | None:
    now = await session.scalar(select(func.clock_timestamp()))
    assert now is not None
    job = await session.scalar(
        select(PrintJob)
        .where(
            or_(
                (PrintJob.status == "PENDING") & (PrintJob.available_at <= now),
                (PrintJob.status == "PRINTING") & (PrintJob.lease_expires_at <= now),
            )
        )
        .order_by(PrintJob.available_at, PrintJob.created_at, PrintJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    job.status = "PRINTING"
    job.claimed_by = bridge_id
    job.claimed_at = now
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    job.attempt_count += 1
    job.last_error = None
    await session.flush()
    return job


async def complete_print_job(session: AsyncSession, job_id: UUID, bridge_id: str) -> PrintJob:
    job = await session.get(PrintJob, job_id, with_for_update=True)
    if job is None:
        raise LookupError("print job not found")
    if job.status == "PRINTED":
        return job
    if job.status != "PRINTING" or job.claimed_by != bridge_id:
        raise PermissionError("print job is not claimed by this bridge")
    now = await session.scalar(select(func.clock_timestamp()))
    assert now is not None
    job.status = "PRINTED"
    job.completed_at = now
    job.lease_expires_at = None
    job.last_error = None
    await session.flush()
    return job


async def fail_print_job(
    session: AsyncSession,
    job_id: UUID,
    bridge_id: str,
    error: str,
    *,
    retryable: bool,
) -> PrintJob:
    job = await session.get(PrintJob, job_id, with_for_update=True)
    if job is None:
        raise LookupError("print job not found")
    if job.status != "PRINTING" or job.claimed_by != bridge_id:
        raise PermissionError("print job is not claimed by this bridge")
    now = await session.scalar(select(func.clock_timestamp()))
    assert now is not None
    job.last_error = error[:1000]
    job.claimed_by = None
    job.claimed_at = None
    job.lease_expires_at = None
    if retryable:
        job.status = "PENDING"
        job.available_at = now + timedelta(seconds=min(300, 2 ** min(job.attempt_count, 8)))
    else:
        job.status = "FAILED"
    await session.flush()
    return job
