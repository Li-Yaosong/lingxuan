"""SQLAlchemy 2.0 async engine/session factory with SQLite PRAGMA configuration.

Provides ``create_engine_and_sessionmaker`` for low-level control and
``Database`` as a convenience wrapper used by repositories and bootstrap.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger(__name__)


def _ensure_db_dir(db_url: str) -> None:
    """Create the parent directory of the SQLite database file if it doesn't exist.

    Only applies to ``sqlite+aiosqlite:///`` URLs with a non-empty path.
    """
    parsed = urlparse(db_url)
    # sqlite URLs: scheme=sqlite, netloc empty, path=/path/to/file.db
    db_path = parsed.path
    if not db_path or db_path == ":memory:":
        return
    # On Windows, urlparse gives "/C:/data/file.db" for absolute paths.
    # Strip leading slash if it reveals a Windows drive letter (C:/...).
    if db_path.startswith("/") and len(db_path) >= 3 and db_path[2] == ":":
        db_path = db_path[1:]
    # SQLite relative-path URLs (e.g. ///./data/file.db) produce a path
    # starting with "/" but the second segment is "." or ".." — strip the
    # leading slash to convert to a real relative path.
    elif db_path.startswith("/.") or (db_path.startswith("/") and not os.path.isabs(db_path)):
        db_path = db_path[1:]
    parent = Path(db_path).parent
    if parent and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)


def _set_sqlite_pragmas(dbapi_connection: object, connection_record: object) -> None:
    """Event handler: set PRAGMA on every new SQLite connection."""
    cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def create_engine_and_sessionmaker(
    db_url: str,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create an async engine and sessionmaker with SQLite PRAGMA configured.

    Returns:
        ``(engine, sessionmaker)`` tuple.
    """
    _ensure_db_dir(db_url)

    engine = create_async_engine(db_url, echo=False)
    event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessionmaker


class Database:
    """Holds an async engine + sessionmaker; provides a ``session()`` context manager.

    Typical usage::

        db = Database("sqlite+aiosqlite:///data/lingxuan.db")
        async with db.session() as s:
            result = await s.execute(text("SELECT 1"))
    """

    def __init__(self, db_url: str) -> None:
        self._db_url = db_url
        self._engine, self._sessionmaker = create_engine_and_sessionmaker(db_url)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        return self._sessionmaker

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield an ``AsyncSession``; auto-commit/rollback on exit."""
        async with self._sessionmaker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    async def create_all(self) -> None:
        """Create all tables from the SQLAlchemy metadata (test/first-run only).

        Production schema is managed by Alembic; this is a convenience for
        tests and initial bootstrapping.
        """
        # Import Base lazily to avoid circular imports at module level.
        # When orm.py (P2-02) defines Base, it will be importable here.
        from lingxuan.adapters.storage.orm import Base  # type: ignore[import-untyped]

        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    def ensure_schema(self) -> None:
        """Ensure the DB schema is at the latest Alembic revision.

        Runs ``alembic upgrade head`` programmatically using the synchronous
        SQLite driver (matching alembic/env.py).  If the DB is empty,
        this creates all tables *and* writes the ``alembic_version`` stamp so
        that future incremental migrations can detect the current state.

        Raises on migration failure; the caller should log and decide whether
        to abort startup.
        """
        from alembic import command
        from alembic.config import Config

        cfg = Config()
        cfg.set_main_option("script_location", "alembic")

        # Resolve DB URL: convert async → sync for Alembic
        sync_url = self._db_url.replace("sqlite+aiosqlite:///", "sqlite:///")
        cfg.set_main_option("sqlalchemy.url", sync_url)

        _ensure_db_dir(sync_url)

        command.upgrade(cfg, "head")
        logger.info("Schema ensured via alembic upgrade head (url=%s)", sync_url)

    async def dispose(self) -> None:
        """Dispose the async engine and release all connections."""
        await self._engine.dispose()
