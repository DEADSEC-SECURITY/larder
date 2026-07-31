from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import case, delete, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from larder.config import Settings
from larder.domain.retry import retry_delay_seconds
from larder.enrichment.mapper import category_display_name, map_product
from larder.models import (
    EnrichmentJob,
    FoodCategory,
    InventoryEvent,
    InventoryItem,
    Product,
    ProductCategory,
    ProductNutrition,
    ProductProviderRecord,
)
from larder.models.enums import EnrichmentStatus, InventoryEventType, InventoryStatus
from larder.schemas.enrichment import FetchDisposition, OffFetchResult, ProviderAttempt
from larder.services.brands import sync_product_brands
from larder.services.stock_matching import match_product_to_stock_groups
from larder.services.store_routing import apply_default_store_to_unassigned_items


class EnrichmentWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        client,
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.client = client
        self.settings = settings

    async def run_once(self, *, barcode: str | None = None) -> bool:
        claimed = await self._claim(barcode=barcode)
        if claimed is None:
            return False
        job_id, claimed_barcode, lease_time = claimed
        result = await self.client.fetch_product(claimed_barcode)
        await self._persist(job_id, claimed_barcode, lease_time, result)
        return True

    async def _claim(
        self, *, barcode: str | None = None
    ) -> tuple[UUID, str, datetime] | None:
        async with self.session_factory() as session, session.begin():
            now = await session.scalar(select(func.clock_timestamp()))
            assert now is not None
            stale_before = now - timedelta(
                seconds=self.settings.enrichment_lease_seconds
            )
            conditions = [
                or_(
                    (
                        EnrichmentJob.status.in_(
                            [
                                EnrichmentStatus.PENDING,
                                EnrichmentStatus.RETRY_WAIT,
                                EnrichmentStatus.NOT_FOUND,
                                EnrichmentStatus.SUCCEEDED,
                            ]
                        )
                        & (EnrichmentJob.next_attempt_at <= now)
                    ),
                    (
                        (EnrichmentJob.status == EnrichmentStatus.RUNNING)
                        & (EnrichmentJob.locked_at < stale_before)
                    ),
                )
            ]
            if barcode is not None:
                conditions.append(EnrichmentJob.product_barcode == barcode)
            job = await session.scalar(
                select(EnrichmentJob)
                .where(*conditions)
                .order_by(EnrichmentJob.next_attempt_at, EnrichmentJob.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            job.status = EnrichmentStatus.RUNNING
            job.locked_at = now
            job.locked_by = self.settings.worker_id
            job.attempt_count += 1
            return job.id, job.product_barcode, now

    async def _persist(
        self, job_id: UUID, barcode: str, lease_time: datetime, result: OffFetchResult
    ) -> None:
        async with self.session_factory() as session, session.begin():
            now = await session.scalar(select(func.clock_timestamp()))
            assert now is not None
            product = await session.scalar(
                select(Product).where(Product.barcode == barcode).with_for_update()
            )
            job = await session.scalar(
                select(EnrichmentJob)
                .where(EnrichmentJob.id == job_id)
                .with_for_update()
            )
            if job is None or product is None:
                return
            if (
                job.status != EnrichmentStatus.RUNNING
                or job.locked_by != self.settings.worker_id
                or job.locked_at != lease_time
            ):
                return
            job.last_http_status = result.http_status
            job.last_error = result.error[:1000] if result.error else None
            await self._record_provider_attempts(session, barcode, result, now)

            if result.disposition == FetchDisposition.FOUND:
                assert result.product is not None
                await self._apply_found(session, product, result, now)
                job.status = EnrichmentStatus.SUCCEEDED
                job.next_attempt_at = now + timedelta(days=30)
                product.enrichment_status = EnrichmentStatus.SUCCEEDED
                product.enriched_at = now
            elif result.disposition == FetchDisposition.NOT_FOUND:
                job.status = EnrichmentStatus.NOT_FOUND
                job.next_attempt_at = now + timedelta(days=30)
                product.enrichment_status = EnrichmentStatus.NOT_FOUND
                product.enriched_at = now
            elif result.disposition == FetchDisposition.RETRY:
                invalid_response = "response" in (result.error or "").lower()
                if invalid_response and job.attempt_count >= 3:
                    job.status = EnrichmentStatus.FAILED
                    product.enrichment_status = EnrichmentStatus.FAILED
                else:
                    job.status = EnrichmentStatus.RETRY_WAIT
                    job.next_attempt_at = now + timedelta(
                        seconds=retry_delay_seconds(job.attempt_count)
                    )
                    product.enrichment_status = EnrichmentStatus.RETRY_WAIT
            else:
                job.status = EnrichmentStatus.FAILED
                product.enrichment_status = EnrichmentStatus.FAILED

            # Release the lease only after leaving RUNNING. _apply_found performs
            # queries that can autoflush the job while enrichment is in progress.
            job.locked_at = None
            job.locked_by = None

    async def _record_provider_attempts(
        self, session, barcode: str, result: OffFetchResult, now
    ) -> None:
        attempts = result.attempts or (
            ProviderAttempt(
                provider=result.provider,
                disposition=result.disposition,
                http_status=result.http_status,
                product_type=result.product_type,
                product=result.product,
                error=result.error,
            ),
        )
        for attempt in attempts:
            raw = attempt.product
            if raw and isinstance(raw.get("_provider_payload"), dict):
                raw = raw["_provider_payload"]
            statement = insert(ProductProviderRecord).values(
                product_barcode=barcode,
                provider=attempt.provider,
                product_type=attempt.product_type,
                disposition=attempt.disposition.value,
                http_status=attempt.http_status,
                raw_payload=raw,
                error=attempt.error[:1000] if attempt.error else None,
                fetched_at=now,
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[
                        ProductProviderRecord.product_barcode,
                        ProductProviderRecord.provider,
                    ],
                    set_={
                        "product_type": statement.excluded.product_type,
                        "disposition": statement.excluded.disposition,
                        "http_status": statement.excluded.http_status,
                        "raw_payload": statement.excluded.raw_payload,
                        "error": statement.excluded.error,
                        "fetched_at": statement.excluded.fetched_at,
                    },
                )
            )

    async def _apply_found(
        self, session, product: Product, result: OffFetchResult, now
    ) -> None:
        source = result.product
        assert source is not None
        mapping = map_product(source)
        raw_brand = mapping.values.get("brand")
        field_sources = dict(product.field_sources or {})
        for key, value in mapping.values.items():
            if value is not None and field_sources.get(key) != "USER":
                setattr(product, key, value)
                field_sources[key] = result.provider
        product.field_sources = field_sources
        if raw_brand and field_sources.get("brand") != "USER":
            await sync_product_brands(session, product, raw_brand, source=result.provider)
        product.identification_source = result.provider
        product.provider_metadata = {
            **(product.provider_metadata or {}),
            result.provider: {
                "productType": result.product_type,
                "fetchedAt": now.isoformat(),
            },
        }
        product.data_quality_notes = {"notes": mapping.quality_notes}
        product.raw_source_payload = source.get("_provider_payload") or source

        for category_id in mapping.categories:
            parent_id = mapping.category_parents.get(category_id)
            parent_source = (
                "OPEN_FOOD_FACTS_TAXONOMY"
                if result.provider == "OPEN_FACTS" and category_id in mapping.category_parents
                else "UPCITEMDB"
                if result.provider == "UPCITEMDB" and category_id in mapping.category_parents
                else None
            )
            statement = insert(FoodCategory).values(
                id=category_id,
                display_name=mapping.category_labels.get(
                    category_id, category_display_name(category_id)
                ),
                parent_id=parent_id,
                parent_source=parent_source,
                product_kind=mapping.values.get("product_kind", "OTHER"),
                active=True,
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[FoodCategory.id],
                    set_={
                        "display_name": mapping.category_labels.get(
                            category_id, category_display_name(category_id)
                        ),
                        "product_kind": statement.excluded.product_kind,
                        "parent_id": case(
                            (
                                FoodCategory.parent_source == "MANUAL",
                                FoodCategory.parent_id,
                            ),
                            (
                                statement.excluded.parent_source.is_not(None),
                                statement.excluded.parent_id,
                            ),
                            else_=FoodCategory.parent_id,
                        ),
                        "parent_source": case(
                            (
                                FoodCategory.parent_source == "MANUAL",
                                FoodCategory.parent_source,
                            ),
                            (
                                statement.excluded.parent_source.is_not(None),
                                statement.excluded.parent_source,
                            ),
                            else_=FoodCategory.parent_source,
                        ),
                        "source_updated_at": now,
                    },
                )
            )

        await session.execute(
            delete(ProductCategory).where(
                ProductCategory.product_barcode == product.barcode
            )
        )
        for rank, category_id in enumerate(mapping.categories):
            session.add(
                ProductCategory(
                    product_barcode=product.barcode,
                    category_id=category_id,
                    source_rank=rank,
                    is_primary=False,
                )
            )
        await session.flush()

        # Display classification and expiration policy are separate concerns.
        # OFF hierarchies run broad -> specific, so the final mapped category
        # is the best human-facing primary even when it has no expiration rule.
        display_category = (
            mapping.categories[-1]
            if mapping.categories
            else self.settings.fallback_category_id
        )
        if not mapping.categories:
            await session.execute(
                insert(FoodCategory)
                .values(
                    id=display_category,
                    display_name=category_display_name(display_category),
                    product_kind=mapping.values.get("product_kind", "OTHER"),
                    active=True,
                )
                .on_conflict_do_nothing(index_elements=[FoodCategory.id])
            )
            session.add(
                ProductCategory(
                    product_barcode=product.barcode,
                    category_id=display_category,
                    source_rank=None,
                    is_primary=True,
                )
            )
        else:
            association = await session.get(
                ProductCategory, (product.barcode, display_category)
            )
            assert association is not None
            association.is_primary = True

        expiration_category = await session.scalar(
            select(FoodCategory.id)
            .join(ProductCategory, ProductCategory.category_id == FoodCategory.id)
            .where(
                ProductCategory.product_barcode == product.barcode,
                FoodCategory.active.is_(True),
                FoodCategory.default_expiration_days.is_not(None),
            )
            .order_by(ProductCategory.source_rank.desc(), FoodCategory.id)
            .limit(1)
        )
        if expiration_category is None:
            expiration_category = self.settings.fallback_category_id

        nutrition_values = {
            **mapping.nutrition.values,
            "basis_unit": mapping.nutrition.basis_unit,
            "serving_size_text": mapping.nutrition.serving_size_text,
            "serving_amount": mapping.nutrition.serving_amount,
            "serving_unit": mapping.nutrition.serving_unit,
            "source": result.provider,
            "updated_at": now,
        }
        await session.execute(
            insert(ProductNutrition)
            .values(product_barcode=product.barcode, **nutrition_values)
            .on_conflict_do_update(
                index_elements=[ProductNutrition.product_barcode],
                set_=nutrition_values,
            )
        )
        await session.flush()
        await self._backfill_expiration(
            session, product.barcode, expiration_category, now
        )
        await apply_default_store_to_unassigned_items(session, [product.barcode])
        await match_product_to_stock_groups(session, product)

    async def _backfill_expiration(
        self, session: AsyncSession, barcode: str, category_id: str, now
    ) -> None:
        category = await session.get(FoodCategory, category_id)
        if (
            category is None
            or category.default_expiration_days is None
            or not category.active
        ):
            return
        edited = exists().where(
            InventoryEvent.inventory_item_id == InventoryItem.id,
            InventoryEvent.event_type == InventoryEventType.EXPIRATION_CHANGED,
        )
        items = (
            await session.scalars(
                select(InventoryItem)
                .where(
                    InventoryItem.product_barcode == barcode,
                    InventoryItem.status == InventoryStatus.IN_STOCK,
                    InventoryItem.expiration_date.is_(None),
                    ~edited,
                )
                .with_for_update()
            )
        ).all()
        timezone = ZoneInfo(self.settings.household_timezone)
        for item in items:
            local_date = item.date_added.astimezone(timezone).date()
            expiration_date = local_date + timedelta(
                days=category.default_expiration_days
            )
            item.expiration_date = expiration_date
            item.expiration_is_estimated = True
            item.expiration_category_id = category.id
            item.expiration_offset_days = category.default_expiration_days
            session.add(
                InventoryEvent(
                    inventory_item_id=item.id,
                    event_type=InventoryEventType.EXPIRATION_CHANGED,
                    occurred_at=now,
                    actor_type="SYSTEM",
                    actor_id="off-enrichment",
                    before_state={"expiration_date": None},
                    after_state={
                        "expiration_date": expiration_date.isoformat(),
                        "expiration_is_estimated": True,
                        "expiration_category_id": category.id,
                        "expiration_offset_days": category.default_expiration_days,
                    },
                    reason="POST_ENRICHMENT_BACKFILL",
                )
            )
