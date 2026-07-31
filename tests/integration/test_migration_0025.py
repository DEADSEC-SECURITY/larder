import asyncio

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine

TIMING_DEFAULTS = {
    "intake_popup_timeout_seconds": 30,
    "intake_scan_group_timeout_seconds": 30,
    "scanner_mode_timeout_seconds": 30,
    "scanner_context_timeout_seconds": 1800,
    "scanner_offline_after_seconds": 172800,
    "toast_duration_seconds": 3,
    "confirmation_timeout_seconds": 3,
    "live_update_debounce_ms": 300,
    "fallback_refresh_seconds": 60,
}


def test_0025_upgrades_existing_household_settings(database_url):
    config = Config("alembic.ini")
    command.downgrade(config, "0024")

    async def seed_existing_settings_row() -> None:
        engine = create_async_engine(database_url)
        async with engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "INSERT INTO household_settings (id) VALUES ('household') "
                    "ON CONFLICT (id) DO NOTHING"
                )
            )
        await engine.dispose()

    async def read_schema_and_values() -> tuple[set[str], dict[str, int] | None]:
        engine = create_async_engine(database_url)
        async with engine.connect() as connection:
            columns = await connection.run_sync(
                lambda sync_connection: {
                    column["name"]
                    for column in sa.inspect(sync_connection).get_columns("household_settings")
                }
            )
            selected = [sa.column(name, sa.Integer()).label(name) for name in TIMING_DEFAULTS]
            values = None
            if set(TIMING_DEFAULTS).issubset(columns):
                row = (
                    (
                        await connection.execute(
                            sa.select(*selected)
                            .select_from(sa.table("household_settings"))
                            .where(sa.column("id") == "household")
                        )
                    )
                    .mappings()
                    .one()
                )
                values = dict(row)
        await engine.dispose()
        return columns, values

    asyncio.run(seed_existing_settings_row())
    columns_before, values_before = asyncio.run(read_schema_and_values())
    assert set(TIMING_DEFAULTS).isdisjoint(columns_before)
    assert values_before is None

    command.upgrade(config, "head")

    columns_after, values_after = asyncio.run(read_schema_and_values())
    assert set(TIMING_DEFAULTS).issubset(columns_after)
    assert values_after == TIMING_DEFAULTS
