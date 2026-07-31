from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from larder.models import ScannerContext


async def expire_contexts(session: AsyncSession) -> int:
    now = await session.scalar(select(func.clock_timestamp()))
    contexts = (
        await session.scalars(
            select(ScannerContext).where(
                (ScannerContext.mode_expires_at <= now)
                | (ScannerContext.store_expires_at <= now)
                | (ScannerContext.storage_location_expires_at <= now)
            )
        )
    ).all()
    changed = 0
    for context in contexts:
        if context.mode_expires_at is not None and context.mode_expires_at <= now:
            context.mode = None
            context.mode_expires_at = None
            changed += 1
        if context.store_expires_at is not None and context.store_expires_at <= now:
            context.store_id = None
            context.store_expires_at = None
            changed += 1
        if (
            context.storage_location_expires_at is not None
            and context.storage_location_expires_at <= now
        ):
            context.storage_location_id = None
            context.storage_location_expires_at = None
            changed += 1
        context.updated_at = now
        context.version += 1
    return changed
