from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from uuid import UUID, uuid4

from larder.domain.enums import ScanKind


@dataclass(frozen=True, slots=True)
class ScanEnvelope:
    raw_frame: bytes
    port: str
    expected_scanner_code: str | None = None
    id: UUID = field(default_factory=uuid4)
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    received_monotonic: float = field(default_factory=monotonic)


@dataclass(frozen=True, slots=True)
class ParsedFrame:
    scanner_code: str
    payload: str
    raw_frame: str
    kind: ScanKind


@dataclass(frozen=True, slots=True)
class NormalizedBarcode:
    raw: str
    barcode: str
    barcode_format: str
    normalization_version: int = 1
