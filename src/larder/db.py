import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def connection_options(database_url: str) -> tuple[URL, dict]:
    url = make_url(database_url)
    if url.drivername != "postgresql+asyncpg":
        return url, {}

    sslmode = url.query.get("sslmode")
    sslrootcert = url.query.get("sslrootcert")
    url = url.difference_update_query(["sslmode", "sslrootcert"])
    if not sslmode or sslmode == "disable":
        return url, {}

    if sslmode in {"verify-ca", "verify-full"}:
        if not sslrootcert:
            raise ValueError(f"sslrootcert is required for sslmode={sslmode}")
        context = ssl.create_default_context(cafile=sslrootcert)
        context.check_hostname = sslmode == "verify-full"
    elif sslmode == "require":
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    else:
        raise ValueError(f"unsupported PostgreSQL sslmode: {sslmode}")
    return url, {"ssl": context}


def create_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    url, connect_args = connection_options(database_url)
    return create_async_engine(
        url,
        echo=echo,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with factory() as session, session.begin():
        yield session
