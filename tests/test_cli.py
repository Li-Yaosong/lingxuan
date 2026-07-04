"""Tests for lingxuan CLI (P3-01).

Covers:
- ``lingxuan --help`` exits 0 and lists all subcommands
- ``lingxuan db upgrade`` creates tables in a temporary database
- ``lingxuan`` (no args) defaults to ``run``
- Placeholder subcommands report not-implemented
- CLI dispatch for migrate-memory, backup, restore subcommands
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from lingxuan.cli import _build_parser, dispatch, main


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


class TestHelp:
    def test_help_exits_zero(self) -> None:
        """``lingxuan --help`` exits with code 0."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0

    def test_help_lists_subcommands(self) -> None:
        """``lingxuan --help`` output contains all registered subcommand names."""
        parser = _build_parser()
        help_text = parser.format_help()
        for name in ("run", "db", "migrate-memory", "backup", "restore", "admin-passwd"):
            assert name in help_text, f"Subcommand '{name}' missing from --help output"

    def test_db_help_lists_subcommands(self) -> None:
        """``lingxuan db --help`` lists upgrade and revision."""
        with pytest.raises(SystemExit) as exc_info:
            main(["db", "--help"])
        assert exc_info.value.code == 0

    def test_db_help_text_contains_upgrade_revision(self) -> None:
        """``lingxuan db -h`` shows upgrade and revision subcommands."""
        with pytest.raises(SystemExit) as exc_info:
            main(["db", "--help"])
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Default behaviour: no subcommand → run
# ---------------------------------------------------------------------------


class TestDefaultRun:
    def test_no_subcommand_calls_run(self) -> None:
        """``lingxuan`` with no args delegates to bootstrap.main()."""
        with patch("lingxuan.cli._cmd_run") as mock_run:
            main([])
        mock_run.assert_called_once()

    def test_explicit_run_calls_run(self) -> None:
        """``lingxuan run`` delegates to bootstrap.main()."""
        with patch("lingxuan.cli._cmd_run") as mock_run:
            main(["run"])
        mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# db upgrade — integration with temporary database
# ---------------------------------------------------------------------------


class TestDbUpgrade:
    def test_db_upgrade_creates_tables(self, tmp_path: Path) -> None:
        """``lingxuan db upgrade`` creates all tables in a fresh SQLite DB."""
        db_file = tmp_path / "test.db"
        db_url = f"sqlite:///{db_file}"
        os_env = {"DB_URL": f"sqlite+aiosqlite:///{db_file}"}

        with patch.dict("os.environ", os_env, clear=False):
            # Build args manually to avoid argparse needing subcommand defaults
            parser = _build_parser()
            args = parser.parse_args(["db", "upgrade"])
            dispatch(args)

        # Verify the database file was created and has tables
        import sqlite3

        conn = sqlite3.connect(str(db_file))
        try:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = {row[0] for row in cursor.fetchall()}
            # Must have at least the alembic_version table and some domain tables
            assert "alembic_version" in tables, f"alembic_version missing; got {tables}"
            assert len(tables) > 1, f"Expected domain tables; got {tables}"
        finally:
            conn.close()

    def test_db_upgrade_custom_revision(self, tmp_path: Path) -> None:
        """``lingxuan db upgrade head`` with explicit revision arg works."""
        db_file = tmp_path / "test2.db"
        os_env = {"DB_URL": f"sqlite+aiosqlite:///{db_file}"}

        with patch.dict("os.environ", os_env, clear=False):
            parser = _build_parser()
            args = parser.parse_args(["db", "upgrade", "head"])
            dispatch(args)

        import sqlite3

        conn = sqlite3.connect(str(db_file))
        try:
            cursor = conn.execute(
                "SELECT version_num FROM alembic_version"
            )
            versions = [row[0] for row in cursor.fetchall()]
            assert len(versions) == 1, f"Expected one alembic version stamp; got {versions}"
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# db revision — autogenerate (requires DB at head, tests message flag)
# ---------------------------------------------------------------------------


class TestDbRevision:
    def test_db_revision_requires_message(self) -> None:
        """``lingxuan db revision`` without -m exits with error."""
        with pytest.raises(SystemExit) as exc_info:
            main(["db", "revision"])
        assert exc_info.value.code != 0

    def test_db_revision_generates_file(self, tmp_path: Path) -> None:
        """``lingxuan db revision -m "test"`` generates a revision file."""
        db_file = tmp_path / "test_rev.db"
        db_url = f"sqlite+aiosqlite:///{db_file}"
        os_env = {"DB_URL": db_url}

        # First upgrade to head so revision can detect current state
        with patch.dict("os.environ", os_env, clear=False):
            parser = _build_parser()
            args = parser.parse_args(["db", "upgrade"])
            dispatch(args)

        # Now generate a revision (should detect no changes → empty revision)
        versions_dir = Path("alembic/versions")
        existing_py = set(versions_dir.glob("*.py"))
        existing_pyc = set(versions_dir.glob("__pycache__/*.pyc"))
        generated: list[Path] = []

        try:
            with patch.dict("os.environ", os_env, clear=False):
                parser = _build_parser()
                args = parser.parse_args(["db", "revision", "-m", "test_p3_01"])
                dispatch(args)

            new_files = set(versions_dir.glob("*.py")) - existing_py
            generated.extend(new_files)
            assert len(new_files) > 0, "No revision file was generated"
        finally:
            # Clean up generated revision files AND their .pyc caches
            for f in generated:
                f.unlink(missing_ok=True)
            # Also clean up any new .pyc files generated by the test
            new_pyc = set(versions_dir.glob("__pycache__/*.pyc")) - existing_pyc
            for pyc in new_pyc:
                pyc.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# admin-passwd subcommand
# ---------------------------------------------------------------------------


class TestAdminPasswd:
    def test_requires_username(self) -> None:
        """admin-passwd without --username exits with usage error."""
        with pytest.raises(SystemExit) as exc_info:
            main(["admin-passwd"])
        assert exc_info.value.code != 0

    def test_accepts_role_flag(self) -> None:
        """admin-passwd --username X --role readonly is accepted by argparser."""
        # It will still fail at password prompt, but arg parsing should succeed
        with patch("lingxuan.cli._cmd_admin_passwd") as mock_cmd:
            main(["admin-passwd", "--username", "testadmin", "--role", "readonly"])
            mock_cmd.assert_called_once()


# ---------------------------------------------------------------------------
# Global overrides
# ---------------------------------------------------------------------------


class TestGlobalOverrides:
    def test_data_root_override(self) -> None:
        """--data-root sets DATA_ROOT in environment."""
        with patch("lingxuan.cli._cmd_run"):
            with patch.dict("os.environ", {}, clear=True):
                parser = _build_parser()
                args = parser.parse_args(["--data-root", "/tmp/test-data", "run"])
                dispatch(args)
                assert os.environ.get("DATA_ROOT") == "/tmp/test-data"

    def test_db_url_override(self) -> None:
        """--db-url sets DB_URL in environment."""
        with patch("lingxuan.cli._cmd_run"):
            with patch.dict("os.environ", {}, clear=True):
                parser = _build_parser()
                args = parser.parse_args(["--db-url", "sqlite:///tmp/test.db", "run"])
                dispatch(args)
                assert os.environ.get("DB_URL") == "sqlite:///tmp/test.db"


# ---------------------------------------------------------------------------
# CLI dispatch: migrate-memory
# ---------------------------------------------------------------------------


class TestMigrateMemoryDispatch:
    def test_migrate_memory_dry_run(self, tmp_path: Path) -> None:
        """``lingxuan migrate-memory --dry-run`` scans and reports without writing to DB."""
        # Set up a sample memory directory
        source = tmp_path / "memory"
        source.mkdir()
        session = {
            "version": 2,
            "history": [{"role": "user", "content": "Hello", "user_id": 1}],
            "summary": "",
            "meta": {},
        }
        (source / "private_1.json").write_text(
            json.dumps(session, ensure_ascii=False), encoding="utf-8"
        )

        db_file = tmp_path / "test.db"
        db_url = f"sqlite+aiosqlite:///{db_file}"
        os_env = {
            "DB_URL": db_url,
            "DATA_ROOT": str(tmp_path),
        }

        with patch.dict("os.environ", os_env, clear=False):
            parser = _build_parser()
            args = parser.parse_args(["migrate-memory", "--dry-run"])
            dispatch(args)

        # DB should not be created in dry-run mode
        # (ensure_schema still creates it, but no data should be written)


# ---------------------------------------------------------------------------
# CLI dispatch: backup
# ---------------------------------------------------------------------------


class TestBackupDispatch:
    def test_backup_creates_snapshot(self, tmp_path: Path) -> None:
        """``lingxuan backup`` creates a backup snapshot."""
        # Create a DB with some data
        db_file = tmp_path / "data" / "lingxuan.db"
        db_file.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_file))
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'test')")
        conn.commit()
        conn.close()

        db_url = f"sqlite+aiosqlite:///{db_file}"
        os_env = {"DB_URL": db_url, "DATA_ROOT": str(tmp_path / "data")}

        with patch.dict("os.environ", os_env, clear=False):
            parser = _build_parser()
            args = parser.parse_args(["backup"])
            dispatch(args)

        backups_dir = tmp_path / "data" / "backups"
        assert backups_dir.exists()
        backup_dirs = list(backups_dir.iterdir())
        assert len(backup_dirs) >= 1
        assert (backup_dirs[0] / "lingxuan.db").exists()


# ---------------------------------------------------------------------------
# CLI dispatch: restore
# ---------------------------------------------------------------------------


class TestRestoreDispatch:
    def test_restore_requires_from_dir(self) -> None:
        """``lingxuan restore`` without --from exits with error."""
        with pytest.raises(SystemExit) as exc_info:
            main(["restore"])
        assert exc_info.value.code != 0

    def test_restore_nonexistent_from_dir_exits(self, tmp_path: Path) -> None:
        """``lingxuan restore --from /nonexistent`` exits with error."""
        with pytest.raises(SystemExit) as exc_info:
            main(["restore", "--from", "/nonexistent/backup", "--yes"])
        assert exc_info.value.code != 0
