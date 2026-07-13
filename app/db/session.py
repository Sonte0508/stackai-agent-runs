from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.models import Base

settings = get_settings()

# SQLite is a fine choice for a take-home, but multiple runs execute as
# concurrent background tasks against the same file, so a plain default
# config hits "database is locked" quickly. WAL mode lets readers and a
# writer proceed concurrently, and a longer busy_timeout makes SQLite retry
# instead of failing immediately.
#
# Important: we deliberately do NOT use StaticPool here. StaticPool pins
# every session to one shared physical connection, which is right for
# ':memory:' databases but wrong for a file DB under concurrency - two
# coroutines sharing one connection can interleave their transactions and
# make one session's read miss another's uncommitted-but-in-flight write.
# Normal pooling gives each session its own connection to the same file,
# which is what WAL mode is designed for.
_is_sqlite = settings.database_url.startswith("sqlite")
_connect_args = {"timeout": 15} if _is_sqlite else {}

engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    connect_args=_connect_args,
)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

if _is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.close()


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


class SessionScope:
    """Context manager for services / background tasks that aren't in a request."""

    async def __aenter__(self) -> AsyncSession:
        self._session = async_session_factory()
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            await self._session.rollback()
        else:
            await self._session.commit()
        await self._session.close()
