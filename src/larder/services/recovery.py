from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from larder.config import Settings
from larder.models import ScanEvent, Scanner
from larder.models.enums import ScanKind, ScanResult
from larder.schemas.scans import ScanEnvelope
from larder.services.command_router import CommandRouter


@dataclass(frozen=True, slots=True)
class RecoveryCandidate:
    event_id: UUID
    received_at: datetime
    payload: str
    barcode: str


@dataclass(frozen=True, slots=True)
class IgnoredAddRecovery:
    scanner_code: str
    anchor_event_id: UUID
    anchor_received_at: datetime
    candidates: tuple[RecoveryCandidate, ...]
    applied: int = 0
    duplicates: int = 0
    failures: tuple[dict[str, str], ...] = ()

    def as_dict(self) -> dict:
        counts = Counter(candidate.barcode for candidate in self.candidates)
        return {
            "scannerCode": self.scanner_code,
            "anchorEventId": str(self.anchor_event_id),
            "anchorReceivedAt": self.anchor_received_at.isoformat(),
            "candidateCount": len(self.candidates),
            "barcodeCounts": dict(sorted(counts.items())),
            "applied": self.applied,
            "duplicates": self.duplicates,
            "failures": list(self.failures),
        }


async def recover_ignored_adds(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    scanner_code: str,
    failed_command: str,
    since: datetime,
    apply: bool = False,
) -> IgnoredAddRecovery:
    async with session_factory() as session:
        scanner = await session.scalar(select(Scanner).where(Scanner.code == scanner_code))
        if scanner is None:
            raise ValueError(f"scanner {scanner_code} does not exist")
        anchor = await session.scalar(
            select(ScanEvent)
            .where(
                ScanEvent.scanner_id == scanner.id,
                ScanEvent.payload_raw == failed_command,
                ScanEvent.received_at >= since,
                ScanEvent.result == ScanResult.REJECTED_BAD_BARCODE,
            )
            .order_by(ScanEvent.received_at)
            .limit(1)
        )
        if anchor is None:
            raise ValueError(
                f"no failed {failed_command} scan found for {scanner_code} "
                f"since {since.isoformat()}"
            )
        events = (
            await session.scalars(
                select(ScanEvent)
                .where(
                    ScanEvent.scanner_id == scanner.id,
                    ScanEvent.received_at > anchor.received_at,
                    ScanEvent.kind == ScanKind.PRODUCT,
                    ScanEvent.result == ScanResult.IGNORED_NO_MODE,
                    ScanEvent.barcode.is_not(None),
                )
                .order_by(ScanEvent.received_at, ScanEvent.id)
            )
        ).all()

    candidates = tuple(
        RecoveryCandidate(
            event_id=event.id,
            received_at=event.received_at,
            payload=event.payload_raw or event.barcode or "",
            barcode=event.barcode or "",
        )
        for event in events
    )
    result = IgnoredAddRecovery(
        scanner_code=scanner_code,
        anchor_event_id=anchor.id,
        anchor_received_at=anchor.received_at,
        candidates=candidates,
    )
    if not apply or not candidates:
        return result

    router = CommandRouter(session_factory, settings)
    applied = 0
    duplicates = 0
    failures: list[dict[str, str]] = []
    for index, candidate in enumerate(candidates):
        if index % 10 == 0:
            mode = await router.process(
                ScanEnvelope(
                    raw_frame=f"LDR:{scanner_code}|CMD:MODE:ADD\r\n".encode(),
                    port=f"recovery:{scanner_code}",
                    expected_scanner_code=scanner_code,
                )
            )
            if mode.result != ScanResult.APPLIED:
                raise RuntimeError(f"failed to set ADD mode: {mode.result.value}")
        outcome = await router.process(
            ScanEnvelope(
                id=uuid5(NAMESPACE_URL, f"larder:recover:add:{candidate.event_id}"),
                raw_frame=f"LDR:{scanner_code}|{candidate.payload}\r\n".encode(),
                port=f"recovery:{scanner_code}",
                expected_scanner_code=scanner_code,
            )
        )
        if outcome.duplicate:
            duplicates += 1
        elif outcome.result == ScanResult.APPLIED:
            applied += 1
        else:
            failures.append(
                {
                    "eventId": str(candidate.event_id),
                    "barcode": candidate.barcode,
                    "result": outcome.result.value,
                }
            )

    return IgnoredAddRecovery(
        scanner_code=scanner_code,
        anchor_event_id=anchor.id,
        anchor_received_at=anchor.received_at,
        candidates=candidates,
        applied=applied,
        duplicates=duplicates,
        failures=tuple(failures),
    )
