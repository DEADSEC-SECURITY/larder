from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel
from sqlalchemy.ext.asyncio import AsyncSession

from larder.models import PrintJob
from larder.services.printing import resolve_print_label
from larder.webapi.common import get_session, web_api_router

router = web_api_router(tags=["printing"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class PrintJobCreateRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    kind: Literal["product", "control", "generic"] = "generic"
    value: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    subtitle: str | None = Field(default=None, max_length=200)
    copies: int = Field(default=1, ge=1, le=20)

    @field_validator("value")
    @classmethod
    def printable_value(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) > 126 for character in value):
            raise ValueError("barcode value must contain printable ASCII only")
        return value


def _payload(job: PrintJob) -> dict:
    return {
        "id": str(job.id),
        "kind": job.kind.lower(),
        "value": job.value,
        "title": job.title,
        "subtitle": job.subtitle,
        "copies": job.copies,
        "status": job.status.lower(),
        "attempts": job.attempt_count,
        "lastError": job.last_error,
        "createdAt": job.created_at.isoformat(),
        "completedAt": job.completed_at.isoformat() if job.completed_at else None,
    }


@router.post("/print-jobs", status_code=status.HTTP_201_CREATED)
async def create_print_job(payload: PrintJobCreateRequest, session: SessionDep) -> dict:
    title, subtitle = await resolve_print_label(
        session,
        kind=payload.kind,
        value=payload.value,
        title=payload.title,
    )
    job = PrintJob(
        kind=payload.kind.upper(),
        value=payload.value,
        title=title,
        subtitle=subtitle,
        copies=payload.copies,
    )
    session.add(job)
    await session.flush()
    return _payload(job)


@router.get("/print-jobs/{job_id}")
async def get_print_job(job_id: UUID, session: SessionDep) -> dict:
    job = await session.get(PrintJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "print job not found")
    return _payload(job)
