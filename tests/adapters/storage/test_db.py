"""Tests for adapters/storage/db.py — WAL, foreign_keys, session context."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import text

from lingxuan.adapters.storage.db import Database, create_engine_and_sessionmaker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db_url(tmp_path: Path) -> str:
    """Return an aiosqlite URL pointing at a temporary database file."""
    db_file = tmp_path / "test.db"
    return f"sqlite+aiosqlite:///{db_file}"


@pytest.fixture
async def db(tmp_db_url: str) -> Database:
    """Provide a ``Database`` instance; dispose after test."""
    database = Database(tmp_db_url)
    yield database
    await database.dispose()


# ---------------------------------------------------------------------------
# PRAGMA tests
# ---------------------------------------------------------------------------


async def test_pragma_journal_mode_wal(db: Database) -> None:
    """PRAGMA journal_mode must be WAL."""
    async with db.session() as s:
        result = await s.execute(text("PRAGMA journal_mode"))
        mode = result.scalar()
        assert mode == "wal", f"Expected journal_mode=wal, got {mode!r}"


async def test_pragma_foreign_keys_on(db: Database) -> None:
    """PRAGMA foreign_keys must be ON for cascade deletes to work."""
    async with db.session() as s:
        result = await s.execute(text("PRAGMA foreign_keys"))
        val = result.scalar()
        assert val == 1, f"Expected foreign_keys=1, got {val!r}"


async def test_pragma_synchronous_normal(db: Database) -> None:
    """PRAGMA synchronous should be NORMAL (1) for performance."""
    async with db.session() as s:
        result = await s.execute(text("PRAGMA synchronous"))
        val = result.scalar()
        assert val == 1, f"Expected synchronous=1 (NORMAL), got {val!r}"


async def test_pragma_busy_timeout(db: Database) -> None:
    """PRAGMA busy_timeout should be 5000ms."""
    async with db.session() as s:
        result = await s.execute(text("PRAGMA busy_timeout"))
        val = result.scalar()
        assert val == 5000, f"Expected busy_timeout=5000, got {val!r}"


# ---------------------------------------------------------------------------
# Session context manager tests
# ---------------------------------------------------------------------------


async def test_session_commit_on_success(db: Database) -> None:
    """Session auto-commits when the context exits without error."""
    async with db.session() as s:
        await s.execute(
            text("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        )
        await s.execute(text("INSERT INTO t (id, name) VALUES (1, 'alice')"))
    # Data should be visible in a new session
    async with db.session() as s:
        result = await s.execute(text("SELECT name FROM t WHERE id = 1"))
        assert result.scalar() == "alice"


async def test_session_rollback_on_error(db: Database) -> None:
    """Session auto-rolls back when an exception is raised inside the context."""
    async with db.session() as s:
        await s.execute(
            text("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        )
        await s.execute(text("INSERT INTO t (id, name) VALUES (1, 'alice')"))

    with pytest.raises(RuntimeError):
        async with db.session() as s:
            await s.execute(text("INSERT INTO t (id, name) VALUES (2, 'bob')"))
            raise RuntimeError("boom")

    # 'bob' should not have been committed
    async with db.session() as s:
        result = await s.execute(text("SELECT COUNT(*) FROM t"))
        assert result.scalar() == 1


# ---------------------------------------------------------------------------
# create_engine_and_sessionmaker tests
# ---------------------------------------------------------------------------


async def test_create_engine_and_sessionmaker(tmp_db_url: str) -> None:
    """Low-level factory returns a working engine + sessionmaker."""
    engine, sm = create_engine_and_sessionmaker(tmp_db_url)
    try:
        async with sm() as s:
            result = await s.execute(text("SELECT 1"))
            assert result.scalar() == 1
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Auto-create directory tests
# ---------------------------------------------------------------------------


async def test_db_directory_auto_created(tmp_path: Path) -> None:
    """Database auto-creates the parent directory if it doesn't exist."""
    nested = tmp_path / "deep" / "nested" / "dir"
    db_url = f"sqlite+aiosqlite:///{nested / 'test.db'}"
    assert not nested.exists()

    db = Database(db_url)
    try:
        async with db.session() as s:
            await s.execute(text("SELECT 1"))
        assert nested.exists()
    finally:
        await db.dispose()
