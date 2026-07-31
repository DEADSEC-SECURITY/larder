from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum


class FetchDisposition(StrEnum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    RETRY = "RETRY"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class ProviderAttempt:
    provider: str
    disposition: FetchDisposition
    http_status: int | None = None
    product_type: str | None = None
    product: dict | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class OffFetchResult:
    disposition: FetchDisposition
    http_status: int | None = None
    product: dict | None = None
    error: str | None = None
    provider: str = "OPEN_FACTS"
    product_type: str | None = None
    attempts: tuple[ProviderAttempt, ...] = ()


@dataclass(frozen=True, slots=True)
class NutritionMapping:
    values: dict[str, Decimal | None]
    basis_unit: str = "g"
    serving_size_text: str | None = None
    serving_amount: Decimal | None = None
    serving_unit: str | None = None
    quality_notes: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ProductMapping:
    values: dict
    categories: list[str]
    category_parents: dict[str, str | None]
    category_labels: dict[str, str]
    nutrition: NutritionMapping
    quality_notes: list[str] = field(default_factory=list)
