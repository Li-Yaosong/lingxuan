"""SQLite backup & restore with source-JSON archiving.

Usage::

    from lingxuan.migration.backup import create_backup, restore_backup

    # Create a snapshot
    info = create_backup(db_url="sqlite+aiosqlite:///data/lingxuan.db",
                         data_root=Path("./data"),
                         out_dir=Path("data/backups"))

    # Restore from a snapshot (auto-snapshots current state first)
    restore_backup(backup_dir=Path("data/backups/20260704-120000"),
                   db_url="sqlite+aiosqlite:///data/lingxuan.db",
                   data_root=Path("./data"))

Both functions are **synchronous** — they use the SQLite backup API or raw
file copies, which are inherently blocking.  The CLI calls them directly;
async callers should wrap with ``loop.run_in_executor``.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from zipfile import ZIP_DEFLATED, ZipFile

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "manifest.json"


class DBLockError(Exception):
    """Raised when the database cannot be exclusively locked for restore."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _db_path_from_url(db_url: str) -> Path:
    """Extract the filesystem path from a SQLAlchemy SQLite URL.

    Handles both POSIX (``sqlite:///data/lingxuan.db``) and Windows
    (``sqlite:///C:/data/lingxuan.db``) URL forms.
    """
    parsed = urlparse(db_url)
    path = parsed.path
    if not path:
        raise ValueError(f"Cannot extract DB path from URL: {db_url}")

    # ``urlparse`` on ``sqlite:///data/lingxuan.db`` → path="/data/lingxuan.db"
    # On Windows: ``sqlite:///C:/data/lingxuan.db`` → path="/C:/data/lingxuan.db"
    # Strip leading slash unless it's a POSIX absolute path (no drive letter).
    if path.startswith("/"):
        # Windows: "/C:/data/lingxuan.db" → "C:/data/lingxuan.db"
        if len(path) >= 3 and path[2] == ":":
            path = path[1:]
        # POSIX relative: "/data/lingxuan.db" → "data/lingxuan.db"
        else:
            path = path[1:]

    # In-memory databases cannot be backed up via file path
    if path == ":memory:":
        raise ValueError(f"Cannot extract DB path from in-memory URL: {db_url}")

    return Path(path)


def _sync_db_url(db_url: str) -> str:
    """Convert async aiosqlite URL to sync sqlite URL."""
    return db_url.replace("sqlite+aiosqlite:///", "sqlite:///")


def _acquire_exclusive_lock(db_path: Path) -> sqlite3.Connection:
    """Try to acquire an exclusive lock on the database file.

    Opens the DB in WAL mode and attempts ``BEGIN EXCLUSIVE``.  If another
    process holds a lock, this raises :class:`DBLockError` within 2 seconds.

    Returns the connection holding the lock — the caller **must** close it
    when done (typically after the restore file copy completes).
    """
    if not db_path.exists():
        # No DB file means no competing process
        return None  # type: ignore[return-value]

    conn = sqlite3.connect(str(db_path), timeout=2)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        conn.execute("BEGIN EXCLUSIVE")
    except sqlite3.OperationalError as exc:
        conn.close()
        raise DBLockError(
            f"无法获取数据库独占锁 ({db_path})，请确保灵轩进程已停止: {exc}"
        ) from exc
    return conn


def _atomic_replace_file(src: Path, dest: Path) -> None:
    """Replace *dest* with *src* atomically via temp-file + os.replace().

    On POSIX, ``os.replace()`` is atomic, so a crash mid-write cannot leave
    a partially-written database file.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file next to the destination first
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(dest.parent), prefix=".lingxuan_restore_",
    )
    try:
        os.close(tmp_fd)
        shutil.copy2(src, tmp_path)
        os.replace(tmp_path, dest)
    except BaseException:
        # Clean up the temp file on any failure
        Path(tmp_path).unlink(missing_ok=True)
        raise


def _timestamp_dir() -> str:
    """Return a directory-safe timestamp string ``YYYYMMDD-HHMMSS-ffffff``.

    Includes microseconds to prevent collisions when multiple backups are
    created within the same second (e.g. auto-snapshot before restore).
    """
    return datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S-%f")


# ---------------------------------------------------------------------------
# SQLite online backup API
# ---------------------------------------------------------------------------


def _backup_db_via_api(src_url: str, dest_db_path: Path) -> None:
    """Copy the source database into *dest_db_path* using the SQLite backup API.

    This ensures a consistent snapshot even if the source DB is in WAL mode
    and potentially being written to (though the caller should ideally ensure
    no active writers for safety).
    """
    dest_db_path.parent.mkdir(parents=True, exist_ok=True)

    src_path = _db_path_from_url(src_url)
    src_conn = sqlite3.connect(str(src_path))
    dest_conn = sqlite3.connect(str(dest_db_path))

    try:
        src_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        src_conn.close()


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def _write_manifest(
    backup_dir: Path,
    *,
    db_size: int,
    includes_memory_zip: bool,
) -> dict:
    """Write ``manifest.json`` and return its content dict."""
    manifest: dict = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "db_size": db_size,
        "includes_memory_zip": includes_memory_zip,
        "files": [],
    }
    # List actual files in backup dir (before writing manifest itself)
    for f in sorted(backup_dir.iterdir()):
        if f.is_file():
            manifest["files"].append(f.name)
    manifest["files"].append(MANIFEST_FILENAME)

    (backup_dir / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return manifest


def _read_manifest(backup_dir: Path) -> dict:
    """Read and validate ``manifest.json`` from *backup_dir*.

    Raises ``FileNotFoundError`` if missing, ``ValueError`` if malformed.
    """
    manifest_path = backup_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"备份目录缺少 {MANIFEST_FILENAME}: {backup_dir}"
        )
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Basic validation
    if "timestamp" not in data or "files" not in data:
        raise ValueError(f"manifest.json 格式无效: {backup_dir}")
    return data


# ---------------------------------------------------------------------------
# Memory zip
# ---------------------------------------------------------------------------


def _zip_memory_dir(memory_dir: Path, zip_path: Path) -> bool:
    """Zip *memory_dir* into *zip_path* if it exists and is non-empty.

    Returns True if the zip was created, False if *memory_dir* doesn't exist.
    """
    if not memory_dir.exists():
        return False

    files = [f for f in memory_dir.rglob("*") if f.is_file()]
    if not files:
        return False

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(zip_path, "w", ZIP_DEFLATED) as zf:
        for f in files:
            arcname = f.relative_to(memory_dir)
            zf.write(f, arcname)
    return True


def _unzip_memory(zip_path: Path, dest_dir: Path) -> None:
    """Extract *zip_path* into *dest_dir*."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    with ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_backup(
    db_url: str,
    data_root: Path,
    out_dir: Path | None = None,
) -> dict:
    """Create a backup snapshot of the SQLite database and optional source JSON.

    Args:
        db_url: SQLAlchemy async DB URL (``sqlite+aiosqlite:///...``).
        data_root: Root data directory (e.g. ``./data``); ``data_root/memory``
            will be zipped if present.
        out_dir: Base output directory for backups.  Defaults to
            ``data_root / "backups"``.

    Returns:
        The manifest dict for the created backup.
    """
    if out_dir is None:
        out_dir = data_root / "backups"

    stamp = _timestamp_dir()
    backup_dir = out_dir / stamp
    backup_dir.mkdir(parents=True, exist_ok=True)

    # 1. Backup the SQLite database via online backup API
    db_dest = backup_dir / "lingxuan.db"
    logger.info("正在备份数据库 → %s", db_dest)
    _backup_db_via_api(db_url, db_dest)
    db_size = db_dest.stat().st_size

    # 2. Zip memory source JSON if present
    memory_dir = data_root / "memory"
    includes_memory_zip = False
    if memory_dir.exists():
        zip_path = backup_dir / "memory.zip"
        logger.info("正在打包 memory JSON → %s", zip_path)
        includes_memory_zip = _zip_memory_dir(memory_dir, zip_path)

    # 3. Write manifest
    manifest = _write_manifest(
        backup_dir,
        db_size=db_size,
        includes_memory_zip=includes_memory_zip,
    )

    logger.info("备份完成: %s (db_size=%d, memory_zip=%s)",
                backup_dir, db_size, includes_memory_zip)
    return manifest


def restore_backup(
    backup_dir: Path,
    db_url: str,
    data_root: Path,
    *,
    auto_snapshot: bool = True,
    restore_memory: bool = True,
    skip_lock_check: bool = False,
) -> dict:
    """Restore the database (and optionally source JSON) from a backup snapshot.

    **Destructive operation** — overwrites the current database file.

    Safety measures:
      1. Acquires an exclusive lock on the current DB to ensure no other
         process is actively writing (raises :class:`DBLockError` on failure).
         Set *skip_lock_check* to True when called from the same process
         (e.g. auto-migrate rollback) where the lock check would self-deadlock.
      2. Auto-snapshots the current state before overwriting.  If the
         auto-snapshot fails, the restore is **aborted** (not silently
         continued) to preserve a recovery path.
      3. Uses atomic file replacement (temp-file + ``os.replace``) so a crash
         mid-write cannot leave a corrupted database.

    Args:
        backup_dir: Path to the backup snapshot directory (containing
            ``lingxuan.db`` and ``manifest.json``).
        db_url: SQLAlchemy async DB URL of the current database to overwrite.
        data_root: Root data directory for memory JSON restoration.
        auto_snapshot: If True, create a snapshot of the current state before
            overwriting (safety net for accidental restore).
        restore_memory: If True and the backup contains ``memory.zip``,
            extract it into ``data_root/memory``.
        skip_lock_check: If True, skip the exclusive-lock pre-check.  Use
            only when the caller is the same process that holds the DB open
            (e.g. auto-migrate rollback).

    Returns:
        The manifest dict of the restored backup.

    Raises:
        FileNotFoundError: If backup dir or its manifest is missing.
        ValueError: If the manifest is invalid or backup DB file is missing.
        DBLockError: If another process holds a lock on the database.
    """
    # 1. Validate backup
    if not backup_dir.is_dir():
        raise FileNotFoundError(f"备份目录不存在: {backup_dir}")

    manifest = _read_manifest(backup_dir)

    backup_db = backup_dir / "lingxuan.db"
    if not backup_db.exists():
        raise ValueError(f"备份目录缺少 lingxuan.db: {backup_dir}")

    current_db_path = _db_path_from_url(db_url)

    # 2. Exclusive lock — ensure no other process is writing the DB
    lock_conn: sqlite3.Connection | None = None
    if not skip_lock_check and current_db_path.exists():
        logger.info("恢复前获取数据库独占锁 ...")
        lock_conn = _acquire_exclusive_lock(current_db_path)

    try:
        # 3. Auto-snapshot current state before overwriting
        if auto_snapshot and current_db_path.exists():
            logger.info("恢复前自动快照当前数据库...")
            create_backup(db_url, data_root)
            logger.info("自动快照完成")

        # 4. Overwrite current DB — atomic via temp-file + os.replace
        logger.info("正在恢复数据库: %s → %s", backup_db, current_db_path)

        # Remove WAL/SHM files if they exist (stale WAL can cause issues)
        for suffix in ("-wal", "-shm"):
            sidecar = current_db_path.with_suffix(current_db_path.suffix + suffix)
            if sidecar.exists():
                sidecar.unlink()
                logger.debug("删除辅助文件: %s", sidecar)

        _atomic_replace_file(backup_db, current_db_path)
        logger.info("数据库已恢复")

        # 5. Optionally restore memory.zip
        if restore_memory:
            memory_zip = backup_dir / "memory.zip"
            if memory_zip.exists():
                memory_dir = data_root / "memory"
                logger.info("正在恢复 memory JSON → %s", memory_dir)
                _unzip_memory(memory_zip, memory_dir)
                logger.info("memory JSON 已恢复")

        logger.info("恢复完成 (来源: %s)", backup_dir)
    except BaseException:
        # Re-raise any error (auto-snapshot failure, file copy failure, etc.)
        raise
    finally:
        # Release the exclusive lock regardless of outcome
        if lock_conn is not None:
            try:
                lock_conn.close()
            except Exception:
                pass

    return manifest
