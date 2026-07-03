"""Tests for adapters/storage/orm.py — create_all + inspector assertions.

Uses a temporary SQLite database via the ``Database`` helper from P2-01.
After ``create_all``, the SQLAlchemy inspector is used to verify table names,
unique constraints, foreign keys, and composite primary keys.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect

from lingxuan.adapters.storage.db import Database
from lingxuan.adapters.storage.orm import Base


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_EXPECTED_TABLES = {
    "sessions",
    "session_messages",
    "session_entities",
    "user_profiles",
    "user_facts",
    "social_edges",
    "name_index",
    "settings",
    "admin_users",
    "audit_logs",
    "plugin_configs",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_create_all_produces_all_tables(tmp_path: Path) -> None:
    """``Base.metadata.create_all`` must produce all 11 tables."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    db = Database(db_url)
    try:
        await db.create_all()

        async with db.engine.begin() as conn:
            table_names = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names(),
            )
        assert set(table_names) == _EXPECTED_TABLES
    finally:
        await db.dispose()


async def test_metadata_contains_all_tables() -> None:
    """``Base.metadata`` must register all 11 tables without touching the DB."""
    assert set(Base.metadata.tables.keys()) == _EXPECTED_TABLES


# ---------------------------------------------------------------------------
# Unique constraints
# ---------------------------------------------------------------------------


async def test_session_messages_unique_session_id_seq(tmp_path: Path) -> None:
    """``session_messages`` must have UNIQUE(session_id, seq)."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    db = Database(db_url)
    try:
        await db.create_all()
        async with db.engine.begin() as conn:
            uqs = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_unique_constraints("session_messages"),
            )
    finally:
        await db.dispose()

    names = {uq["name"] for uq in uqs}
    assert "uq_session_messages_session_id_seq" in names
    # Also verify the column composition
    matched = [uq for uq in uqs if uq["name"] == "uq_session_messages_session_id_seq"]
    assert set(matched[0]["column_names"]) == {"session_id", "seq"}


async def test_social_edges_unique_from_to_relation_label(tmp_path: Path) -> None:
    """``social_edges`` must have UNIQUE(from_user_id, to_user_id, relation, label)."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    db = Database(db_url)
    try:
        await db.create_all()
        async with db.engine.begin() as conn:
            uqs = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_unique_constraints("social_edges"),
            )
    finally:
        await db.dispose()

    names = {uq["name"] for uq in uqs}
    assert "uq_social_edges_from_to_relation_label" in names
    matched = [uq for uq in uqs if uq["name"] == "uq_social_edges_from_to_relation_label"]
    assert set(matched[0]["column_names"]) == {"from_user_id", "to_user_id", "relation", "label"}


async def test_admin_users_username_unique(tmp_path: Path) -> None:
    """``admin_users.username`` must be UNIQUE."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    db = Database(db_url)
    try:
        await db.create_all()
        async with db.engine.begin() as conn:
            uqs = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_unique_constraints("admin_users"),
            )
    finally:
        await db.dispose()

    # SQLite inspector may return None as constraint name for single-column UNIQUE
    username_cols = [
        uq["column_names"] for uq in uqs if "username" in uq.get("column_names", [])
    ]
    assert username_cols, "Expected a UNIQUE constraint on 'username'"


# ---------------------------------------------------------------------------
# Foreign keys
# ---------------------------------------------------------------------------


async def test_session_messages_fk_to_sessions(tmp_path: Path) -> None:
    """``session_messages.session_id`` must FK→sessions with ON DELETE CASCADE."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    db = Database(db_url)
    try:
        await db.create_all()
        async with db.engine.begin() as conn:
            fks = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_foreign_keys("session_messages"),
            )
    finally:
        await db.dispose()

    fk = next(fk for fk in fks if fk["referred_table"] == "sessions")
    assert fk["constrained_columns"] == ["session_id"]
    assert fk["referred_columns"] == ["session_id"]
    # SQLite cascade is enforced via PRAGMA foreign_keys=ON
    assert fk["options"].get("ondelete") == "CASCADE"


async def test_session_entities_fk_to_sessions(tmp_path: Path) -> None:
    """``session_entities.session_id`` must FK→sessions with ON DELETE CASCADE."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    db = Database(db_url)
    try:
        await db.create_all()
        async with db.engine.begin() as conn:
            fks = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_foreign_keys("session_entities"),
            )
    finally:
        await db.dispose()

    fk = next(fk for fk in fks if fk["referred_table"] == "sessions")
    assert fk["constrained_columns"] == ["session_id"]
    assert fk["options"].get("ondelete") == "CASCADE"


async def test_user_facts_fk_to_user_profiles(tmp_path: Path) -> None:
    """``user_facts.user_id`` must FK→user_profiles with ON DELETE CASCADE."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    db = Database(db_url)
    try:
        await db.create_all()
        async with db.engine.begin() as conn:
            fks = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_foreign_keys("user_facts"),
            )
    finally:
        await db.dispose()

    fk = next(fk for fk in fks if fk["referred_table"] == "user_profiles")
    assert fk["constrained_columns"] == ["user_id"]
    assert fk["options"].get("ondelete") == "CASCADE"


# ---------------------------------------------------------------------------
# Composite primary keys
# ---------------------------------------------------------------------------


async def test_session_entities_composite_pk(tmp_path: Path) -> None:
    """``session_entities`` must have PK(session_id, name)."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    db = Database(db_url)
    try:
        await db.create_all()
        async with db.engine.begin() as conn:
            pk = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_pk_constraint("session_entities"),
            )
    finally:
        await db.dispose()

    assert set(pk["constrained_columns"]) == {"session_id", "name"}


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------


async def test_session_messages_index_session_id_id(tmp_path: Path) -> None:
    """``session_messages`` must have INDEX(session_id, id)."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    db = Database(db_url)
    try:
        await db.create_all()
        async with db.engine.begin() as conn:
            indexes = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_indexes("session_messages"),
            )
    finally:
        await db.dispose()

    ix = next(ix for ix in indexes if ix["name"] == "ix_session_messages_session_id_id")
    assert ix["column_names"] == ["session_id", "id"]


async def test_user_facts_index_user_id_active_learned_at(tmp_path: Path) -> None:
    """``user_facts`` must have INDEX(user_id, active, learned_at)."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    db = Database(db_url)
    try:
        await db.create_all()
        async with db.engine.begin() as conn:
            indexes = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_indexes("user_facts"),
            )
    finally:
        await db.dispose()

    ix = next(
        ix
        for ix in indexes
        if ix["name"] == "ix_user_facts_user_id_active_learned_at"
    )
    assert ix["column_names"] == ["user_id", "active", "learned_at"]


async def test_social_edges_indexes(tmp_path: Path) -> None:
    """``social_edges`` must have INDEX(from_user_id) and INDEX(to_user_id)."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    db = Database(db_url)
    try:
        await db.create_all()
        async with db.engine.begin() as conn:
            indexes = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_indexes("social_edges"),
            )
    finally:
        await db.dispose()

    ix_names = {ix["name"] for ix in indexes}
    assert "ix_social_edges_from_user_id" in ix_names
    assert "ix_social_edges_to_user_id" in ix_names


async def test_audit_logs_indexes(tmp_path: Path) -> None:
    """``audit_logs`` must have INDEX(created_at) and INDEX(actor)."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    db = Database(db_url)
    try:
        await db.create_all()
        async with db.engine.begin() as conn:
            indexes = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_indexes("audit_logs"),
            )
    finally:
        await db.dispose()

    ix_names = {ix["name"] for ix in indexes}
    assert "ix_audit_logs_created_at" in ix_names
    assert "ix_audit_logs_actor" in ix_names
