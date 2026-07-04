"""Tests for migration/auto.py — auto-migrate bootstrap flow.

Covers:
- Empty DB + legacy JSON + AUTO_MIGRATE=true → triggers import, archives, reports.
- Non-empty DB → no import.
- Migration failure → rollback + AutoMigrateError raised.
- AUTO_MIGRATE=false → schema only, no import.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lingxuan.migration.auto import (
    AutoMigrateError,
    AutoMigrateResult,
    _archive_memory_dir,
    _has_legacy_json,
    run_auto_migrate,
)

if TYPE_CHECKING:
    from lingxuan.adapters.storage.db import Database


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _populate_memory_dir(memory_dir: Path) -> Path:
    """Populate a memory directory with sample session + user JSON."""
    memory_dir.mkdir(parents=True, exist_ok=True)

    # Session file
    (memory_dir / "private_123.json").write_text(
        json.dumps({
            "version": 2,
            "history": [
                {"role": "user", "content": "你好", "user_id": 123},
                {"role": "assistant", "content": "你好呀~"},
            ],
            "summary": "",
            "meta": {"last_active_at": "2026-07-01T12:00:00Z", "nickname": "小明"},
        }),
        encoding="utf-8",
    )

    # User profile
    users = memory_dir / "users"
    users.mkdir(exist_ok=True)
    (users / "123.json").write_text(
        json.dumps({
            "user_id": 123,
            "identity": {"preferred_name": "小明"},
            "relationship": {"stage": "familiar", "interaction_count": 15},
            "facts": [],
            "cognition": {},
        }),
        encoding="utf-8",
    )

    # Social graph
    (memory_dir / "social_graph.json").write_text(
        json.dumps({
            "version": 1,
            "edges": [],
            "name_index": {"小明": 123},
        }),
        encoding="utf-8",
    )

    return memory_dir


@pytest.fixture
def data_root_with_memory(tmp_path: Path) -> Path:
    """Return a data root with ``memory/`` containing JSON files."""
    root = tmp_path / "data"
    _populate_memory_dir(root / "memory")
    return root


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    """Return a data root directory without memory JSON."""
    root = tmp_path / "data"
    root.mkdir()
    return root


@pytest.fixture
async def db(tmp_path: Path) -> "Database":
    """Create a temp-file SQLite Database with schema ensured."""
    from lingxuan.adapters.storage.db import Database

    db_file = tmp_path / "test.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    database = Database(db_url)
    database.ensure_schema()
    yield database
    await database.dispose()


def _make_config(
    *,
    auto_migrate: bool = True,
    db_url: str = "",
    data_root: str = "./data",
) -> MagicMock:
    """Create a mock ConfigProvider with the given settings."""
    cfg = MagicMock()
    cfg.get_bool = MagicMock(return_value=auto_migrate)
    cfg.get_str = MagicMock(side_effect=lambda k: {
        "DB_URL": db_url,
        "DATA_ROOT": data_root,
    }.get(k, ""))
    return cfg


# ---------------------------------------------------------------------------
# Unit tests: helpers
# ---------------------------------------------------------------------------


class TestHasLegacyJson:
    def test_no_dir(self, tmp_path: Path) -> None:
        assert _has_legacy_json(tmp_path / "nonexistent") is False

    def test_empty_dir(self, tmp_path: Path) -> None:
        d = tmp_path / "memory"
        d.mkdir()
        assert _has_legacy_json(d) is False

    def test_session_file(self, tmp_path: Path) -> None:
        d = tmp_path / "memory"
        d.mkdir()
        (d / "private_123.json").write_text("{}", encoding="utf-8")
        assert _has_legacy_json(d) is True

    def test_group_session_file(self, tmp_path: Path) -> None:
        d = tmp_path / "memory"
        d.mkdir()
        (d / "group_456.json").write_text("{}", encoding="utf-8")
        assert _has_legacy_json(d) is True

    def test_user_profile_file(self, tmp_path: Path) -> None:
        d = tmp_path / "memory"
        d.mkdir()
        users = d / "users"
        users.mkdir()
        (users / "789.json").write_text("{}", encoding="utf-8")
        assert _has_legacy_json(d) is True

    def test_social_graph(self, tmp_path: Path) -> None:
        d = tmp_path / "memory"
        d.mkdir()
        (d / "social_graph.json").write_text("{}", encoding="utf-8")
        assert _has_legacy_json(d) is True

    def test_non_session_json_ignored(self, tmp_path: Path) -> None:
        d = tmp_path / "memory"
        d.mkdir()
        (d / "other.json").write_text("{}", encoding="utf-8")
        assert _has_legacy_json(d) is False


class TestArchiveMemoryDir:
    def test_renames_to_imported(self, tmp_path: Path) -> None:
        mem = tmp_path / "memory"
        mem.mkdir()
        (mem / "test.json").write_text("{}", encoding="utf-8")

        result = _archive_memory_dir(mem)
        assert result == tmp_path / "memory.imported"
        assert result.is_dir()
        assert (result / "test.json").exists()
        assert not mem.exists()

    def test_handles_existing_target(self, tmp_path: Path) -> None:
        mem = tmp_path / "memory"
        mem.mkdir()
        (mem / "test.json").write_text("{}", encoding="utf-8")

        # Pre-create the .imported target
        existing = tmp_path / "memory.imported"
        existing.mkdir()

        result = _archive_memory_dir(mem)
        assert result == tmp_path / "memory.imported.1"
        assert result.is_dir()
        assert (result / "test.json").exists()


# ---------------------------------------------------------------------------
# Integration tests: run_auto_migrate
# ---------------------------------------------------------------------------


class TestAutoMigrateEmptyDbWithJson:
    """Empty DB + legacy JSON + AUTO_MIGRATE=true → triggers import."""

    @pytest.mark.asyncio
    async def test_imports_and_archives(
        self, data_root_with_memory: Path, db: "Database"
    ) -> None:
        from lingxuan.adapters.storage.orm import Session, UserProfile
        from sqlalchemy import func, select

        db_url = db._db_url
        config = _make_config(
            auto_migrate=True,
            db_url=db_url,
            data_root=str(data_root_with_memory),
        )

        result = await run_auto_migrate(db, config, data_root=data_root_with_memory)

        assert result.schema_upgraded is True
        assert result.import_needed is True
        assert result.import_performed is True
        assert result.rolled_back is False
        assert result.backup_dir  # backup was created
        assert result.archived_to  # JSON was archived
        assert result.report_path  # report was written

        # Verify data was actually imported
        async with db.session() as s:
            session_count = await s.scalar(
                select(func.count()).select_from(Session)
            )
            assert session_count == 1  # private_123

            profile_count = await s.scalar(
                select(func.count()).select_from(UserProfile)
            )
            assert profile_count == 1  # user 123

        # Verify source JSON was archived
        assert not (data_root_with_memory / "memory").exists()
        assert Path(result.archived_to).is_dir()

        # Verify report file exists
        assert Path(result.report_path).exists()
        report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
        assert "auto_migrate" in report
        assert "migration_detail" in report


class TestAutoMigrateNonEmptyDb:
    """Non-empty DB → no import."""

    @pytest.mark.asyncio
    async def test_skips_import_when_db_has_data(
        self, data_root_with_memory: Path, db: "Database"
    ) -> None:
        from lingxuan.adapters.storage.orm import Session

        # Insert a session to make DB non-empty
        async with db.session() as s:
            s.add(Session(
                session_id="private_999",
                kind="private",
                summary="test",
                nickname="test",
                created_at="2026-07-01T00:00:00Z",
            ))

        db_url = db._db_url
        config = _make_config(
            auto_migrate=True,
            db_url=db_url,
            data_root=str(data_root_with_memory),
        )

        result = await run_auto_migrate(db, config, data_root=data_root_with_memory)

        assert result.schema_upgraded is True
        assert result.import_needed is False
        assert result.import_performed is False

        # Source JSON should still be there (not archived)
        assert (data_root_with_memory / "memory").is_dir()


class TestAutoMigrateDisabled:
    """AUTO_MIGRATE=false → schema only, no import."""

    @pytest.mark.asyncio
    async def test_schema_only_when_disabled(
        self, data_root_with_memory: Path, db: "Database"
    ) -> None:
        db_url = db._db_url
        config = _make_config(
            auto_migrate=False,
            db_url=db_url,
            data_root=str(data_root_with_memory),
        )

        result = await run_auto_migrate(db, config, data_root=data_root_with_memory)

        assert result.schema_upgraded is True
        assert result.import_needed is False
        assert result.import_performed is False
        assert result.backup_dir == ""


class TestAutoMigrateFailureRollback:
    """Migration failure → rollback + AutoMigrateError."""

    @pytest.mark.asyncio
    async def test_rollback_on_migration_failure(
        self, data_root_with_memory: Path, db: "Database"
    ) -> None:
        from sqlalchemy import func, select
        from lingxuan.adapters.storage.orm import Session

        db_url = db._db_url
        config = _make_config(
            auto_migrate=True,
            db_url=db_url,
            data_root=str(data_root_with_memory),
        )

        # Patch migrate_from_json to raise an exception
        with patch(
            "lingxuan.migration.auto.migrate_from_json",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Simulated migration failure"),
        ):
            with pytest.raises(AutoMigrateError, match="Migration failed"):
                await run_auto_migrate(db, config, data_root=data_root_with_memory)

        # Verify backup was created (for rollback)
        backups_dir = data_root_with_memory / "backups"
        assert backups_dir.exists()

        # Verify DB was rolled back (should be empty since we started empty)
        async with db.session() as s:
            count = await s.scalar(select(func.count()).select_from(Session))
            assert count == 0

    @pytest.mark.asyncio
    async def test_schema_failure_raises(self, db: "Database", tmp_path: Path) -> None:
        db_url = db._db_url
        config = _make_config(auto_migrate=True, db_url=db_url)

        # Patch ensure_schema to raise
        with patch.object(db, "ensure_schema", side_effect=RuntimeError("Schema boom")):
            with pytest.raises(AutoMigrateError, match="Schema upgrade failed"):
                await run_auto_migrate(db, config, data_root=tmp_path)


class TestAutoMigrateNoJsonSource:
    """Empty DB + no legacy JSON → no import."""

    @pytest.mark.asyncio
    async def test_no_import_without_json(
        self, data_root: Path, db: "Database"
    ) -> None:
        db_url = db._db_url
        config = _make_config(
            auto_migrate=True,
            db_url=db_url,
            data_root=str(data_root),
        )

        result = await run_auto_migrate(db, config, data_root=data_root)

        assert result.schema_upgraded is True
        assert result.import_needed is False
        assert result.import_performed is False


class TestAutoMigrateResult:
    """Test AutoMigrateResult serialization."""

    def test_to_dict(self) -> None:
        result = AutoMigrateResult(
            schema_upgraded=True,
            import_needed=True,
            import_performed=True,
            backup_dir="/data/backups/20260704",
            report_path="/data/backups/20260704/report.json",
            archived_to="/data/memory.imported",
        )
        d = result.to_dict()
        assert d["schema_upgraded"] is True
        assert d["import_needed"] is True
        assert d["import_performed"] is True
        assert d["backup_dir"] == "/data/backups/20260704"
        assert d["report_path"] == "/data/backups/20260704/report.json"
        assert d["archived_to"] == "/data/memory.imported"
        assert d["rolled_back"] is False
        assert d["errors"] == []
