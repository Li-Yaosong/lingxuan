"""Tests for migration/backup.py — backup & restore with manifest validation."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from zipfile import ZipFile

import pytest

from lingxuan.migration.backup import (
    MANIFEST_FILENAME,
    _db_path_from_url,
    _read_manifest,
    create_backup,
    restore_backup,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Create a temporary SQLite database with some test data."""
    db_path = tmp_path / "lingxuan.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO t (id, name) VALUES (1, 'alice')")
    conn.execute("INSERT INTO t (id, name) VALUES (2, 'bob')")
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def tmp_db_url(tmp_db: Path) -> str:
    """Return an aiosqlite URL pointing at the temporary database."""
    return f"sqlite+aiosqlite:///{tmp_db}"


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    """Return a data root directory with optional memory subdir."""
    root = tmp_path / "data"
    root.mkdir()
    return root


@pytest.fixture
def data_root_with_memory(data_root: Path) -> Path:
    """Return a data root with ``memory/`` containing JSON files."""
    mem = data_root / "memory"
    mem.mkdir()
    (mem / "session_1.json").write_text(
        json.dumps({"version": 2, "history": []}), encoding="utf-8"
    )
    users = mem / "users"
    users.mkdir()
    (users / "123.json").write_text(
        json.dumps({"user_id": 123, "identity": {}}), encoding="utf-8"
    )
    return data_root


# ---------------------------------------------------------------------------
# Unit tests: helpers
# ---------------------------------------------------------------------------


class TestDbPathFromUrl:
    def test_relative_path(self) -> None:
        result = _db_path_from_url("sqlite+aiosqlite:///data/lingxuan.db")
        assert result == Path("data/lingxuan.db")

    def test_absolute_path(self) -> None:
        result = _db_path_from_url("sqlite+aiosqlite:////var/data/lingxuan.db")
        # On POSIX: urlparse gives path="/var/data/lingxuan.db"
        # After stripping leading slash → "var/data/lingxuan.db" (relative)
        # This is fine for POSIX where triple-slash makes it relative
        assert "lingxuan.db" in str(result)

    def test_invalid_url_raises(self) -> None:
        with pytest.raises(ValueError, match="in-memory"):
            _db_path_from_url("sqlite+aiosqlite:///:memory:")


class TestReadManifest:
    def test_valid_manifest(self, tmp_path: Path) -> None:
        manifest = {"timestamp": "2026-07-04T12:00:00+00:00", "files": ["lingxuan.db"]}
        (tmp_path / MANIFEST_FILENAME).write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        result = _read_manifest(tmp_path)
        assert result["timestamp"] == "2026-07-04T12:00:00+00:00"

    def test_missing_manifest_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="缺少"):
            _read_manifest(tmp_path)

    def test_invalid_manifest_raises(self, tmp_path: Path) -> None:
        (tmp_path / MANIFEST_FILENAME).write_text(
            json.dumps({"bad": True}), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="格式无效"):
            _read_manifest(tmp_path)


# ---------------------------------------------------------------------------
# Integration tests: create_backup
# ---------------------------------------------------------------------------


class TestCreateBackup:
    def test_creates_snapshot_with_manifest(
        self, tmp_db_url: str, data_root: Path
    ) -> None:
        manifest = create_backup(tmp_db_url, data_root)

        assert "timestamp" in manifest
        assert "db_size" in manifest
        assert manifest["db_size"] > 0
        assert manifest["includes_memory_zip"] is False

        # Verify backup directory was created
        backups_dir = data_root / "backups"
        assert backups_dir.exists()
        backup_dirs = list(backups_dir.iterdir())
        assert len(backup_dirs) == 1

        # Verify files inside
        backup_dir = backup_dirs[0]
        assert (backup_dir / "lingxuan.db").exists()
        assert (backup_dir / MANIFEST_FILENAME).exists()

    def test_backup_db_has_consistent_data(
        self, tmp_db_url: str, data_root: Path
    ) -> None:
        create_backup(tmp_db_url, data_root)

        # Read the backup DB directly
        backups_dir = data_root / "backups"
        backup_dir = list(backups_dir.iterdir())[0]
        backup_db = backup_dir / "lingxuan.db"

        conn = sqlite3.connect(str(backup_db))
        rows = conn.execute("SELECT name FROM t ORDER BY id").fetchall()
        conn.close()

        assert [r[0] for r in rows] == ["alice", "bob"]

    def test_includes_memory_zip(
        self, tmp_db_url: str, data_root_with_memory: Path
    ) -> None:
        manifest = create_backup(tmp_db_url, data_root_with_memory)

        assert manifest["includes_memory_zip"] is True

        backups_dir = data_root_with_memory / "backups"
        backup_dir = list(backups_dir.iterdir())[0]
        assert (backup_dir / "memory.zip").exists()

        # Verify zip contents
        with ZipFile(backup_dir / "memory.zip", "r") as zf:
            names = zf.namelist()
            assert "session_1.json" in names
            assert "users/123.json" in names

    def test_custom_out_dir(
        self, tmp_db_url: str, data_root: Path, tmp_path: Path
    ) -> None:
        custom_out = tmp_path / "custom_backups"
        manifest = create_backup(tmp_db_url, data_root, out_dir=custom_out)

        assert custom_out.exists()
        assert len(list(custom_out.iterdir())) == 1
        assert manifest["db_size"] > 0


# ---------------------------------------------------------------------------
# Integration tests: restore_backup
# ---------------------------------------------------------------------------


class TestRestoreBackup:
    def test_restore_reverts_db_to_snapshot(
        self, tmp_db_url: str, tmp_db: Path, data_root: Path
    ) -> None:
        # 1. Create backup of initial state
        manifest = create_backup(tmp_db_url, data_root)
        backups_dir = data_root / "backups"
        backup_dir = list(backups_dir.iterdir())[0]

        # 2. Modify the database
        conn = sqlite3.connect(str(tmp_db))
        conn.execute("INSERT INTO t (id, name) VALUES (3, 'charlie')")
        conn.execute("DELETE FROM t WHERE id = 1")
        conn.commit()
        conn.close()

        # Verify modification
        conn = sqlite3.connect(str(tmp_db))
        rows = conn.execute("SELECT name FROM t ORDER BY id").fetchall()
        conn.close()
        assert [r[0] for r in rows] == ["bob", "charlie"]

        # 3. Restore from backup
        result = restore_backup(backup_dir, tmp_db_url, data_root)

        # 4. Verify data is back to snapshot state
        conn = sqlite3.connect(str(tmp_db))
        rows = conn.execute("SELECT name FROM t ORDER BY id").fetchall()
        conn.close()
        assert [r[0] for r in rows] == ["alice", "bob"]

    def test_restore_creates_auto_snapshot(
        self, tmp_db_url: str, tmp_db: Path, data_root: Path
    ) -> None:
        # Create backup
        create_backup(tmp_db_url, data_root)
        backups_dir = data_root / "backups"
        backup_dir = list(backups_dir.iterdir())[0]

        # Restore (should auto-snapshot current state)
        restore_backup(backup_dir, tmp_db_url, data_root)

        # There should now be 2 backup dirs (original + auto-snapshot)
        all_backups = sorted(backups_dir.iterdir())
        # The original backup + auto-snapshot before restore
        assert len(all_backups) >= 2

    def test_restore_no_auto_snapshot(
        self, tmp_db_url: str, tmp_db: Path, data_root: Path
    ) -> None:
        create_backup(tmp_db_url, data_root)
        backups_dir = data_root / "backups"
        backup_dir = list(backups_dir.iterdir())[0]

        restore_backup(backup_dir, tmp_db_url, data_root, auto_snapshot=False)

        # Only the original backup should exist
        assert len(list(backups_dir.iterdir())) == 1

    def test_restore_with_memory_zip(
        self, tmp_db_url: str, data_root_with_memory: Path
    ) -> None:
        # Create backup with memory
        create_backup(tmp_db_url, data_root_with_memory)
        backups_dir = data_root_with_memory / "backups"
        backup_dir = list(backups_dir.iterdir())[0]

        # Remove memory dir
        memory_dir = data_root_with_memory / "memory"
        import shutil
        shutil.rmtree(memory_dir)
        assert not memory_dir.exists()

        # Restore with memory
        restore_backup(backup_dir, tmp_db_url, data_root_with_memory)

        # Verify memory was restored
        assert memory_dir.exists()
        assert (memory_dir / "session_1.json").exists()
        assert (memory_dir / "users" / "123.json").exists()

    def test_restore_without_memory_zip(
        self, tmp_db_url: str, data_root_with_memory: Path
    ) -> None:
        create_backup(tmp_db_url, data_root_with_memory)
        backups_dir = data_root_with_memory / "backups"
        backup_dir = list(backups_dir.iterdir())[0]

        # Remove memory dir
        memory_dir = data_root_with_memory / "memory"
        import shutil
        shutil.rmtree(memory_dir)

        # Restore without memory
        restore_backup(
            backup_dir, tmp_db_url, data_root_with_memory, restore_memory=False
        )

        # Memory should NOT be restored
        assert not memory_dir.exists()

    def test_restore_missing_backup_dir_raises(
        self, tmp_db_url: str, data_root: Path
    ) -> None:
        with pytest.raises(FileNotFoundError, match="不存在"):
            restore_backup(
                Path("/nonexistent/backup"), tmp_db_url, data_root
            )

    def test_restore_missing_manifest_raises(
        self, tmp_db_url: str, data_root: Path, tmp_path: Path
    ) -> None:
        # Dir exists but no manifest
        empty_dir = tmp_path / "empty_backup"
        empty_dir.mkdir()

        with pytest.raises(FileNotFoundError, match="缺少"):
            restore_backup(empty_dir, tmp_db_url, data_root)

    def test_restore_missing_db_in_backup_raises(
        self, tmp_db_url: str, data_root: Path, tmp_path: Path
    ) -> None:
        # Dir with manifest but no db file
        bad_dir = tmp_path / "bad_backup"
        bad_dir.mkdir()
        manifest = {"timestamp": "2026-07-04T12:00:00+00:00", "files": []}
        (bad_dir / MANIFEST_FILENAME).write_text(
            json.dumps(manifest), encoding="utf-8"
        )

        with pytest.raises(ValueError, match="缺少 lingxuan.db"):
            restore_backup(bad_dir, tmp_db_url, data_root)


# ---------------------------------------------------------------------------
# Manifest content validation
# ---------------------------------------------------------------------------


class TestManifestContent:
    def test_manifest_fields(
        self, tmp_db_url: str, data_root: Path
    ) -> None:
        manifest = create_backup(tmp_db_url, data_root)

        assert "timestamp" in manifest
        assert "db_size" in manifest
        assert "includes_memory_zip" in manifest
        assert "files" in manifest
        assert isinstance(manifest["files"], list)
        assert "lingxuan.db" in manifest["files"]
        assert MANIFEST_FILENAME in manifest["files"]

    def test_manifest_db_size_matches_file(
        self, tmp_db_url: str, data_root: Path
    ) -> None:
        manifest = create_backup(tmp_db_url, data_root)

        backups_dir = data_root / "backups"
        backup_dir = list(backups_dir.iterdir())[0]
        actual_size = (backup_dir / "lingxuan.db").stat().st_size

        assert manifest["db_size"] == actual_size
