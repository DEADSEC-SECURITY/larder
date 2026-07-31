from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from larder.config import Settings
from larder.domain.context import ContextValues, expire_context
from larder.models import (
    ControlBarcode,
    EnrichmentJob,
    GroceryListItem,
    HouseholdSettings,
    IntakeBatch,
    InventoryEvent,
    InventoryItem,
    Product,
    ScanEvent,
    Scanner,
    ScannerContext,
    StockGroupProduct,
    StockGroupShoppingItem,
    StorageLocation,
    Store,
)
from larder.models.enums import (
    EnrichmentStatus,
    InventoryEventType,
    InventoryStatus,
    ScanKind,
    ScannerMode,
    ScanResult,
)
from larder.models.settings import (
    HOUSEHOLD_SETTINGS_ID,
    INTAKE_SCAN_GROUP_TIMEOUT_SECONDS_DEFAULT,
)
from larder.scanner.parser import (
    BarcodeError,
    FrameError,
    audit_text,
    normalize_gtin,
    parse_frame,
)
from larder.schemas.scans import ParsedFrame, ScanEnvelope
from larder.services.expiration import resolve_expiration
from larder.services.stock_policy import recalculate_stock_policy
from larder.services.storage import default_storage_location_for_product
from larder.services.store_routing import rule_assignment_for_product


@dataclass(frozen=True, slots=True)
class ScanOutcome:
    scan_event_id: UUID
    result: ScanResult
    barcode: str | None = None
    item_id: UUID | None = None
    intake_batch_id: UUID | None = None
    error_code: str | None = None
    duplicate: bool = False


class CommandRouter:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        transaction_attempts: int = 3,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.transaction_attempts = transaction_attempts

    async def process(self, envelope: ScanEnvelope) -> ScanOutcome:
        try:
            parsed = parse_frame(
                envelope.raw_frame,
                expected_scanner_code=envelope.expected_scanner_code,
                max_frame_bytes=self.settings.max_frame_bytes,
            )
            frame_error = None
        except FrameError as exc:
            parsed = None
            frame_error = exc

        for attempt in range(1, self.transaction_attempts + 1):
            try:
                return await self._process_once(envelope, parsed, frame_error)
            except DBAPIError as exc:
                sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
                if sqlstate not in {"40001", "40P01"} or attempt == self.transaction_attempts:
                    raise
                await asyncio.sleep(0.025 * attempt)
        raise AssertionError("unreachable")

    async def _process_once(
        self,
        envelope: ScanEnvelope,
        parsed: ParsedFrame | None,
        frame_error: FrameError | None,
    ) -> ScanOutcome:
        async with self.session_factory() as session, session.begin():
            event, inserted = await self._insert_receipt(session, envelope, parsed, frame_error)
            if not inserted:
                intake_batch_id = None
                if event.created_item_id is not None:
                    intake_batch_id = await session.scalar(
                        select(InventoryItem.intake_batch_id).where(
                            InventoryItem.id == event.created_item_id
                        )
                    )
                return ScanOutcome(
                    scan_event_id=event.id,
                    result=event.result,
                    barcode=event.barcode,
                    item_id=event.created_item_id or event.affected_item_id,
                    intake_batch_id=intake_batch_id,
                    error_code=event.error_code,
                    duplicate=True,
                )

            if frame_error is not None:
                self._finish(
                    event,
                    ScanResult.REJECTED_BAD_FRAME,
                    error_code=frame_error.code,
                    error_detail=frame_error.detail,
                )
                return self._outcome(event)

            assert parsed is not None
            scanner = await session.scalar(
                select(Scanner).where(
                    Scanner.code == parsed.scanner_code, Scanner.enabled.is_(True)
                )
            )
            if scanner is None:
                self._finish(
                    event,
                    ScanResult.REJECTED_BAD_FRAME,
                    error_code="UNKNOWN_SCANNER",
                    error_detail="scanner is unknown or disabled",
                )
                return self._outcome(event)
            event.scanner_id = scanner.id

            await session.execute(
                insert(ScannerContext)
                .values(scanner_id=scanner.id)
                .on_conflict_do_nothing(index_elements=[ScannerContext.scanner_id])
            )
            context = await session.scalar(
                select(ScannerContext)
                .where(ScannerContext.scanner_id == scanner.id)
                .with_for_update()
            )
            assert context is not None
            now = await session.scalar(select(func.clock_timestamp()))
            assert now is not None
            self._clear_expired_context(context, now)
            event.mode_at_processing = context.mode
            event.store_id_at_processing = context.store_id
            event.processing_attempts += 1

            if parsed.kind == ScanKind.CONTROL:
                await self._apply_control(session, event, scanner, context, parsed.payload, now)
                return self._outcome(event)

            try:
                normalized = normalize_gtin(
                    parsed.payload,
                    accept_invalid_check_digit=self.settings.accept_invalid_check_digit,
                )
            except BarcodeError as exc:
                self._finish(
                    event,
                    ScanResult.REJECTED_BAD_BARCODE,
                    error_code=exc.code,
                    error_detail=exc.detail,
                    processed_at=now,
                )
                return self._outcome(event)

            event.barcode = normalized.barcode
            event.normalization_version = normalized.normalization_version
            if context.mode is None:
                self._finish(event, ScanResult.IGNORED_NO_MODE, processed_at=now)
                return self._outcome(event)

            if context.mode == ScannerMode.ADD:
                intake_batch_id = await self._add(session, event, scanner, context, normalized, now)
                return self._outcome(event, intake_batch_id=intake_batch_id)
            elif context.mode == ScannerMode.LIST:
                await self._add_to_list(session, event, scanner, context, normalized, now)
                return self._outcome(event)
            else:
                await session.scalar(
                    select(Product.barcode)
                    .where(Product.barcode == normalized.barcode)
                    .with_for_update()
                )
                await self._remove_or_trash(
                    session, event, scanner, context, normalized.barcode, now
                )
            return self._outcome(event)

    async def _insert_receipt(
        self,
        session: AsyncSession,
        envelope: ScanEnvelope,
        parsed: ParsedFrame | None,
        frame_error: FrameError | None,
    ) -> tuple[ScanEvent, bool]:
        idempotency_key = str(envelope.id)
        if parsed is not None:
            raw_frame = parsed.raw_frame
        elif frame_error is not None:
            raw_frame = frame_error.raw_frame
        else:
            raw_frame = audit_text(envelope.raw_frame)
        values = {
            "id": envelope.id,
            "idempotency_key": idempotency_key,
            "received_at": envelope.received_at,
            "raw_frame": raw_frame,
            "payload_raw": parsed.payload if parsed else None,
            "kind": parsed.kind if parsed else ScanKind.MALFORMED,
            "result": ScanResult.RECEIVED,
        }
        inserted_id = await session.scalar(
            insert(ScanEvent).values(**values).on_conflict_do_nothing().returning(ScanEvent.id)
        )
        if inserted_id is None:
            existing = await session.scalar(
                select(ScanEvent).where(
                    or_(
                        ScanEvent.id == envelope.id,
                        ScanEvent.idempotency_key == idempotency_key,
                    )
                )
            )
            assert existing is not None
            return existing, False
        event = await session.get(ScanEvent, inserted_id)
        assert event is not None
        return event, True

    async def _apply_control(
        self,
        session: AsyncSession,
        event: ScanEvent,
        scanner: Scanner,
        context: ScannerContext,
        payload: str,
        now,
    ) -> None:
        control = await session.scalar(
            select(ControlBarcode).where(
                ControlBarcode.payload == payload,
                ControlBarcode.enabled.is_(True),
            )
        )
        if control is None:
            self._finish(
                event,
                ScanResult.REJECTED_UNKNOWN_COMMAND,
                error_code="UNKNOWN_COMMAND",
                processed_at=now,
            )
            return
        if control.command_type == "SET_MODE":
            context.mode = control.mode
            context.mode_expires_at = now + timedelta(seconds=scanner.mode_timeout_seconds)
        elif control.command_type == "CLEAR_MODE":
            context.mode = None
            context.mode_expires_at = None
        elif control.command_type == "SET_STORE":
            store = await session.get(Store, control.store_id)
            if store is None or not store.active:
                self._finish(
                    event,
                    ScanResult.REJECTED_UNKNOWN_COMMAND,
                    error_code="STORE_INACTIVE",
                    processed_at=now,
                )
                return
            context.store_id = store.id
            context.store_expires_at = now + timedelta(seconds=scanner.store_timeout_seconds)
        elif control.command_type == "CLEAR_STORE":
            context.store_id = None
            context.store_expires_at = None
        elif control.command_type == "SET_LOCATION":
            location = await session.get(StorageLocation, control.storage_location_id)
            if location is None or not location.active:
                self._finish(
                    event,
                    ScanResult.REJECTED_UNKNOWN_COMMAND,
                    error_code="LOCATION_INACTIVE",
                    processed_at=now,
                )
                return
            context.storage_location_id = location.id
            # Locations share the store timeout: both are "where am I right now"
            # context that should fade at the same pace.
            context.storage_location_expires_at = now + timedelta(
                seconds=scanner.store_timeout_seconds
            )
        elif control.command_type == "CLEAR_LOCATION":
            context.storage_location_id = None
            context.storage_location_expires_at = None
        context.updated_at = now
        context.version += 1
        self._finish(event, ScanResult.APPLIED, processed_at=now)

    async def _add(self, session, event, scanner, context, normalized, now) -> UUID:
        await session.execute(
            insert(Product)
            .values(
                barcode=normalized.barcode,
                barcode_format=normalized.barcode_format,
                enrichment_status=EnrichmentStatus.PENDING,
            )
            .on_conflict_do_nothing(index_elements=[Product.barcode])
        )
        await session.scalar(
            select(Product.barcode).where(Product.barcode == normalized.barcode).with_for_update()
        )
        await session.execute(
            insert(EnrichmentJob)
            .values(
                id=uuid4(),
                product_barcode=normalized.barcode,
                status=EnrichmentStatus.PENDING,
                next_attempt_at=now,
            )
            .on_conflict_do_update(
                index_elements=[EnrichmentJob.product_barcode],
                set_={
                    "status": EnrichmentStatus.PENDING,
                    "next_attempt_at": now,
                    "locked_at": None,
                    "locked_by": None,
                },
                where=EnrichmentJob.status == EnrichmentStatus.FAILED,
            )
        )
        snapshot = await resolve_expiration(
            session,
            normalized.barcode,
            now,
            self.settings.household_timezone,
            self.settings.fallback_category_id,
        )
        assignment = None
        if context.store_id is None:
            assignment = await rule_assignment_for_product(session, normalized.barcode)
        store_id = context.store_id or (assignment.store_id if assignment else None)
        batch = await session.scalar(
            select(IntakeBatch)
            .where(
                IntakeBatch.scanner_id == scanner.id,
                IntakeBatch.status == "OPEN",
            )
            .order_by(IntakeBatch.last_scan_at.desc(), IntakeBatch.created_at.desc())
            .with_for_update()
            .limit(1)
        )
        prefs = await session.get(HouseholdSettings, HOUSEHOLD_SETTINGS_ID)
        group_timeout_seconds = (
            prefs.intake_scan_group_timeout_seconds
            if prefs is not None
            else INTAKE_SCAN_GROUP_TIMEOUT_SECONDS_DEFAULT
        )
        batch_is_stale = bool(
            batch is not None
            and batch.last_scan_at <= now - timedelta(seconds=group_timeout_seconds)
        )
        if batch is None or batch.product_barcode != normalized.barcode or batch_is_stale:
            batch = IntakeBatch(
                scanner_id=scanner.id,
                product_barcode=normalized.barcode,
                status="OPEN",
                quantity=1,
                last_scan_at=now,
            )
            session.add(batch)
            await session.flush()
        else:
            batch.quantity += 1
            batch.last_scan_at = now
        item_id = uuid4()
        item = InventoryItem(
            id=item_id,
            product_barcode=normalized.barcode,
            intake_batch_id=batch.id,
            status=InventoryStatus.IN_STOCK,
            date_added=now,
            expiration_date=snapshot.expiration_date if snapshot else None,
            expiration_is_estimated=snapshot is not None,
            expiration_category_id=snapshot.category_id if snapshot else None,
            expiration_offset_days=snapshot.offset_days if snapshot else None,
            store_id=store_id,
            # A scanned CMD:LOC context wins over the product/category default.
            storage_location_id=context.storage_location_id
            or await default_storage_location_for_product(session, normalized.barcode),
            price_pending=True,
            add_scan_event_id=event.id,
        )
        session.add(item)
        # Flush the item before its append-only event. These mappers intentionally
        # have no ORM relationship, so SQLAlchemy cannot infer the FK insert order.
        await session.flush()
        session.add(
            InventoryEvent(
                inventory_item_id=item_id,
                event_type=InventoryEventType.ADDED,
                occurred_at=now,
                scan_event_id=event.id,
                actor_type="SCANNER",
                actor_id=scanner.code,
                after_state={
                    "status": InventoryStatus.IN_STOCK.value,
                    "product_barcode": normalized.barcode,
                    "expiration_date": snapshot.expiration_date.isoformat() if snapshot else None,
                    "store_id": str(store_id) if store_id else None,
                    "store_source": assignment.source if assignment and store_id else None,
                    "intake_batch_id": str(batch.id),
                },
            )
        )
        await session.flush()
        event.created_item_id = item_id
        self._renew_context(scanner, context, now)
        await recalculate_stock_policy(session, normalized.barcode)
        self._finish(event, ScanResult.APPLIED, processed_at=now)
        return batch.id

    async def _add_to_list(self, session, event, scanner, context, normalized, now) -> None:
        await session.execute(
            insert(Product)
            .values(
                barcode=normalized.barcode,
                barcode_format=normalized.barcode_format,
                enrichment_status=EnrichmentStatus.PENDING,
            )
            .on_conflict_do_nothing(index_elements=[Product.barcode])
        )
        await session.scalar(
            select(Product.barcode).where(Product.barcode == normalized.barcode).with_for_update()
        )
        await session.execute(
            insert(EnrichmentJob)
            .values(
                id=uuid4(),
                product_barcode=normalized.barcode,
                status=EnrichmentStatus.PENDING,
                next_attempt_at=now,
            )
            .on_conflict_do_update(
                index_elements=[EnrichmentJob.product_barcode],
                set_={
                    "status": EnrichmentStatus.PENDING,
                    "next_attempt_at": now,
                    "locked_at": None,
                    "locked_by": None,
                },
                where=EnrichmentJob.status == EnrichmentStatus.FAILED,
            )
        )
        stock_group_id = await session.scalar(
            select(StockGroupProduct.stock_group_id).where(
                StockGroupProduct.product_barcode == normalized.barcode
            )
        )
        if stock_group_id is not None:
            group_row = await session.get(
                StockGroupShoppingItem, stock_group_id, with_for_update=True
            )
            if group_row is None:
                group_row = StockGroupShoppingItem(
                    stock_group_id=stock_group_id,
                    automatic_quantity=0,
                    manual_quantity=1,
                    checked_quantity=0,
                    preferred_store_id=context.store_id,
                )
                session.add(group_row)
            else:
                group_row.manual_quantity += 1
                if context.store_id is not None:
                    group_row.preferred_store_id = context.store_id
        else:
            row = await session.get(GroceryListItem, normalized.barcode, with_for_update=True)
            if row is None:
                row = GroceryListItem(
                    product_barcode=normalized.barcode,
                    automatic_quantity=0,
                    manual_quantity=1,
                    checked_quantity=0,
                    preferred_store_id=context.store_id,
                )
                session.add(row)
            else:
                row.manual_quantity += 1
                if context.store_id is not None:
                    row.preferred_store_id = context.store_id
        await session.flush()
        self._renew_context(scanner, context, now)
        self._finish(event, ScanResult.APPLIED, processed_at=now)

    async def _remove_or_trash(self, session, event, scanner, context, barcode: str, now) -> None:
        item = await session.scalar(
            select(InventoryItem)
            .where(
                InventoryItem.product_barcode == barcode,
                InventoryItem.status == InventoryStatus.IN_STOCK,
            )
            .order_by(
                InventoryItem.expiration_date.asc().nulls_last(),
                InventoryItem.date_added.asc(),
                InventoryItem.id.asc(),
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if item is None:
            self._finish(event, ScanResult.IGNORED_NO_STOCK, processed_at=now)
            return
        new_status = (
            InventoryStatus.REMOVED
            if context.mode == ScannerMode.REMOVE
            else InventoryStatus.TRASHED
        )
        event_type = (
            InventoryEventType.REMOVED
            if new_status == InventoryStatus.REMOVED
            else InventoryEventType.TRASHED
        )
        item.status = new_status
        item.removed_at = now
        item.terminal_scan_event_id = event.id
        session.add(
            InventoryEvent(
                inventory_item_id=item.id,
                event_type=event_type,
                occurred_at=now,
                scan_event_id=event.id,
                actor_type="SCANNER",
                actor_id=scanner.code,
                before_state={"status": InventoryStatus.IN_STOCK.value},
                after_state={"status": new_status.value, "removed_at": now.isoformat()},
            )
        )
        event.affected_item_id = item.id
        self._renew_context(scanner, context, now)
        await session.flush()
        await recalculate_stock_policy(session, barcode)
        self._finish(event, ScanResult.APPLIED, processed_at=now)

    @staticmethod
    def _clear_expired_context(context: ScannerContext, now) -> None:
        previous = ContextValues(
            context.mode,
            context.mode_expires_at,
            context.store_id,
            context.store_expires_at,
            context.storage_location_id,
            context.storage_location_expires_at,
        )
        current = expire_context(previous, now)
        if current != previous:
            context.mode = current.mode
            context.mode_expires_at = current.mode_expires_at
            context.store_id = current.store_id
            context.store_expires_at = current.store_expires_at
            context.storage_location_id = current.storage_location_id
            context.storage_location_expires_at = current.storage_location_expires_at
            context.updated_at = now
            context.version += 1

    @staticmethod
    def _renew_context(scanner: Scanner, context: ScannerContext, now) -> None:
        context.mode_expires_at = now + timedelta(seconds=scanner.mode_timeout_seconds)
        if context.store_id is not None:
            context.store_expires_at = now + timedelta(seconds=scanner.store_timeout_seconds)
        if context.storage_location_id is not None:
            context.storage_location_expires_at = now + timedelta(
                seconds=scanner.store_timeout_seconds
            )
        context.updated_at = now
        context.version += 1

    @staticmethod
    def _finish(
        event: ScanEvent,
        result: ScanResult,
        *,
        error_code: str | None = None,
        error_detail: str | None = None,
        processed_at=None,
    ) -> None:
        event.result = result
        event.error_code = error_code
        event.error_detail = error_detail[:1000] if error_detail else None
        event.processed_at = processed_at or event.received_at

    @staticmethod
    def _outcome(event: ScanEvent, *, intake_batch_id: UUID | None = None) -> ScanOutcome:
        return ScanOutcome(
            scan_event_id=event.id,
            result=event.result,
            barcode=event.barcode,
            item_id=event.created_item_id or event.affected_item_id,
            intake_batch_id=intake_batch_id,
            error_code=event.error_code,
        )
