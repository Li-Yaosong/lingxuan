"""Tests for Alembic migrations — upgrade head, downgrade base, schema consistency.

Uses a temporary SQLite database with the synchronous driver (matching
alembic/env.py) to run migrations programmatically.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from lingxuan.adapters.storage.orm import Base


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


def _make_alembic_cfg(db_path: Path) -> Config:
    """Build an Alembic Config pointing at a temporary database."""
    cfg = Config()
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


# ---------------------------------------------------------------------------
# upgrade head
# ---------------------------------------------------------------------------


def test_upgrade_head_creates_all_tables(tmp_path: Path) -> None:
    """``alembic upgrade head`` must create all 11 tables."""
    db_path = tmp_path / "test.db"
    cfg = _make_alembic_cfg(db_path)

    command.upgrade(cfg, "head")

    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
    finally:
        engine.dispose()

    # alembic_version is always present after upgrade; exclude it from comparison
    app_tables = table_names - {"alembic_version"}
    assert app_tables == _EXPECTED_TABLES


# ---------------------------------------------------------------------------
# downgrade base
# ---------------------------------------------------------------------------


def test_downgrade_base_drops_all_tables(tmp_path: Path) -> None:
    """``alembic downgrade base`` must remove all application tables."""
    db_path = tmp_path / "test.db"
    cfg = _make_alembic_cfg(db_path)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
    finally:
        engine.dispose()

    # Only alembic_version should remain (tracking table)
    assert _EXPECTED_TABLES.isdisjoint(table_names)


# ---------------------------------------------------------------------------
# autogenerate consistency
# ---------------------------------------------------------------------------


def test_autogenerate_no_changes_after_upgrade(tmp_path: Path) -> None:
    """After ``upgrade head``, autogenerate must detect no model-vs-DB drift."""
    db_path = tmp_path / "test.db"
    cfg = _make_alembic_cfg(db_path)

    command.upgrade(cfg, "head")

    # Run autogenerate into a temporary revision; if no changes, this raises
    # ``alembic.util.CommandError`` with "Target database is not up to date"
    # or produces an empty migration script.
    from alembic.operations import ops
    from alembic.autogenerate import compare_metadata

    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        # Use Alembic's internal diff to compare DB vs metadata
        # We need to hook into the migration context
        from alembic.runtime.migration import MigrationContext

        with engine.connect() as conn:
            context = MigrationContext.configure(conn, opts={"render_as_batch": True})
            diff = compare_metadata(context, Base.metadata)

        # diff is a list of operation tuples; empty means no changes
        assert diff == [], (
            f"Autogenerate detected drift after upgrade head: {diff}"
        )
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# schema details: constraints and indexes
# ---------------------------------------------------------------------------


def test_unique_constraints_after_migration(tmp_path: Path) -> None:
    """Verify named unique constraints exist after migration."""
    db_path = tmp_path / "test.db"
    cfg = _make_alembic_cfg(db_path)
    command.upgrade(cfg, "head")

    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        inspector = inspect(engine)

        # session_messages: UNIQUE(session_id, seq)
        uqs = inspector.get_unique_constraints("session_messages")
        names = {uq["name"] for uq in uqs}
        assert "uq_session_messages_session_id_seq" in names

        # social_edges: UNIQUE(from_user_id, to_user_id, relation, label)
        uqs = inspector.get_unique_constraints("social_edges")
        names = {uq["name"] for uq in uqs}
        assert "uq_social_edges_from_to_relation_label" in names

        # admin_users: UNIQUE(username)
        uqs = inspector.get_unique_constraints("admin_users")
        username_cols = [
            uq["column_names"] for uq in uqs if "username" in uq.get("column_names", [])
        ]
        assert username_cols, "Expected a UNIQUE constraint on 'username'"
    finally:
        engine.dispose()


def test_foreign_keys_after_migration(tmp_path: Path) -> None:
    """Verify foreign keys with ON DELETE CASCADE after migration."""
    db_path = tmp_path / "test.db"
    cfg = _make_alembic_cfg(db_path)
    command.upgrade(cfg, "head")

    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        inspector = inspect(engine)

        # session_messages -> sessions
        fks = inspector.get_foreign_keys("session_messages")
        fk = next(fk for fk in fks if fk["referred_table"] == "sessions")
        assert fk["constrained_columns"] == ["session_id"]
        assert fk["options"].get("ondelete") == "CASCADE"

        # session_entities -> sessions
        fks = inspector.get_foreign_keys("session_entities")
        fk = next(fk for fk in fks if fk["referred_table"] == "sessions")
        assert fk["constrained_columns"] == ["session_id"]
        assert fk["options"].get("ondelete") == "CASCADE"

        # user_facts -> user_profiles
        fks = inspector.get_foreign_keys("user_facts")
        fk = next(fk for fk in fks if fk["referred_table"] == "user_profiles")
        assert fk["constrained_columns"] == ["user_id"]
        assert fk["options"].get("ondelete") == "CASCADE"
    finally:
        engine.dispose()


def test_indexes_after_migration(tmp_path: Path) -> None:
    """Verify named indexes exist after migration."""
    db_path = tmp_path / "test.db"
    cfg = _make_alembic_cfg(db_path)
    command.upgrade(cfg, "head")

    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        inspector = inspect(engine)

        # session_messages
        indexes = inspector.get_indexes("session_messages")
        ix_names = {ix["name"] for ix in indexes}
        assert "ix_session_messages_session_id_id" in ix_names

        # user_facts
        indexes = inspector.get_indexes("user_facts")
        ix_names = {ix["name"] for ix in indexes}
        assert "ix_user_facts_user_id_active_learned_at" in ix_names

        # social_edges
        indexes = inspector.get_indexes("social_edges")
        ix_names = {ix["name"] for ix in indexes}
        assert "ix_social_edges_from_user_id" in ix_names
        assert "ix_social_edges_to_user_id" in ix_names

        # audit_logs
        indexes = inspector.get_indexes("audit_logs")
        ix_names = {ix["name"] for ix in indexes}
        assert "ix_audit_logs_created_at" in ix_names
        assert "ix_audit_logs_actor" in ix_names
    finally:
        engine.dispose()


def test_composite_pk_after_migration(tmp_path: Path) -> None:
    """Verify composite primary key on session_entities."""
    db_path = tmp_path / "test.db"
    cfg = _make_alembic_cfg(db_path)
    command.upgrade(cfg, "head")

    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        inspector = inspect(engine)
        pk = inspector.get_pk_constraint("session_entities")
        assert set(pk["constrained_columns"]) == {"session_id", "name"}
    finally:
        engine.dispose()
