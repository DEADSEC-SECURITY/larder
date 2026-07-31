import os

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from testcontainers.postgres import PostgresContainer

from larder.config import get_settings
from larder.db import create_engine, create_session_factory
from larder.models import Base


@pytest.fixture(scope="session")
def database_url():
    with PostgresContainer("postgres:16-alpine") as postgres:
        url = postgres.get_connection_url()
        url = url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        url = url.replace("postgresql://", "postgresql+asyncpg://")
        os.environ["LARDER_DATABASE_URL"] = url
        get_settings.cache_clear()
        command.upgrade(Config("alembic.ini"), "head")
        yield url
        get_settings.cache_clear()


@pytest_asyncio.fixture
async def session_factory(database_url):
    engine = create_engine(database_url)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_database(session_factory):
    table_names = ", ".join(f'"{name}"' for name in Base.metadata.tables)
    async with session_factory() as session, session.begin():
        await session.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))
        await session.execute(
            text(
                """
                INSERT INTO food_categories
                    (id, display_name, expiration_basis, active, created_at, updated_at)
                VALUES ('local:uncategorized', 'Uncategorized', 'DATE_ADDED', true, now(), now())
                """
            )
        )
        await session.execute(
            text(
                """
                INSERT INTO control_barcodes
                    (payload, command_type, mode, enabled, description)
                VALUES
                    ('CMD:MODE:ADD', 'SET_MODE', 'ADD', true, 'Add inventory'),
                    ('CMD:MODE:LIST', 'SET_MODE', 'LIST', true, 'Add To List'),
                    ('CMD:MODE:REMOVE', 'SET_MODE', 'REMOVE', true, 'Remove inventory'),
                    ('CMD:MODE:TRASH', 'SET_MODE', 'TRASH', true, 'Trash inventory'),
                    ('CMD:MODE:CLEAR', 'CLEAR_MODE', NULL, true, 'Clear operation mode'),
                    ('CMD:LOC:CLEAR', 'CLEAR_LOCATION', NULL, true,
                     'Clear storage location context')
                """
            )
        )
