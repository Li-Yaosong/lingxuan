"""Auto-migrate flow for bootstrap: schema upgrade + first-run data import + rollback.

When ``AUTO_MIGRATE=true`` (default), the bootstrap startup hook calls
:func:`run_auto_migrate` which:

1. Runs ``alembic upgrade head`` (schema migration).
2. Checks whether a first-run data import is needed (DB business tables
   are empty **and** legacy ``data/memory`` JSON exists).
3. If needed: backup → migrate → archive source JSON → write report.
4. On failure: restore backup, log error, and raise (aborting startup).

When ``AUTO_MIGRATE=false``, only the schema upgrade runs; data import
must be done manually via ``lingxuan migrate-memory``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from lingxuan.adapters.storage.db import Database
from lingxuan.adapters.storage.orm import Session, UserProfile
from lingxuan.migration.backup import create_backup, restore_backup
from lingxuan.migration.from_json import migrate_from_json
from lingxuan.protocols.config import ConfigProvider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Report data structure
# ---------------------------------------------------------------------------


@dataclass
class AutoMigrateResult:
    """Outcome of the auto-migrate flow, for logging and reporting."""

    schema_upgraded: bool = False
    import_needed: bool = False
    import_performed: bool = False
    backup_dir: str = ""
    report_path: str = ""
    archived_to: str = ""
    rolled_back: bool = False
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_upgraded": self.schema_upgraded,
            "import_needed": self.import_needed,
            "import_performed": self.import_performed,
            "backup_dir": self.backup_dir,
            "report_path": self.report_path,
            "archived_to": self.archived_to,
            "rolled_back": self.rolled_back,
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_legacy_json(memory_dir: Path) -> bool:
    """Return True if ``memory_dir`` contains migratable session JSON files."""
    if not memory_dir.is_dir():
        return False
    # Check for session files (private_*.json / group_*.json) at top level
    for f in memory_dir.iterdir():
        if f.is_file() and f.suffix == ".json" and (
            f.stem.startswith("private_") or f.stem.startswith("group_")
        ):
            return True
    # Also check for user profile JSONs or social_graph.json
    users_dir = memory_dir / "users"
    if users_dir.is_dir():
        for f in users_dir.iterdir():
            if f.is_file() and f.suffix == ".json":
                try:
                    int(f.stem)
                    return True
                except ValueError:
                    pass
    social = memory_dir / "social_graph.json"
    if social.is_file():
        return True
    return False


async def _is_db_empty(db: Database) -> bool:
    """Return True if the core business tables (sessions, user_profiles) are empty.

    This is the safety guard: only when the DB has no business data do we
    consider auto-importing from legacy JSON.
    """
    async with db.session() as s:
        session_count = await s.scalar(
            select(func.count()).select_from(Session)
        )
        if session_count and session_count > 0:
            return False
        profile_count = await s.scalar(
            select(func.count()).select_from(UserProfile)
        )
        return not profile_count or profile_count == 0


def _archive_memory_dir(memory_dir: Path) -> Path:
    """Rename ``data/memory`` → ``data/memory.imported``.

    Returns the new path.  If the target already exists, appends a numeric
    suffix (``.imported.1``, ``.imported.2``, …).
    """
    target = memory_dir.with_name(memory_dir.name + ".imported")
    if target.exists():
        n = 1
        while memory_dir.with_name(f"{memory_dir.name}.imported.{n}").exists():
            n += 1
        target = memory_dir.with_name(f"{memory_dir.name}.imported.{n}")
    memory_dir.rename(target)
    return target


def _write_report(
    backup_dir: Path,
    migration_report_dict: dict[str, Any],
    auto_result: AutoMigrateResult,
) -> Path:
    """Write a combined migration report into the backup directory."""
    report = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "auto_migrate": auto_result.to_dict(),
        "migration_detail": migration_report_dict,
    }
    report_path = backup_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report_path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class AutoMigrateError(Exception):
    """Raised when auto-migration fails and rollback has been attempted."""


async def run_auto_migrate(
    db: Database,
    config: ConfigProvider,
    *,
    data_root: Path | None = None,
) -> AutoMigrateResult:
    """Execute the auto-migrate flow during bootstrap startup.

    Steps (when ``AUTO_MIGRATE=true``):
      1. ``alembic upgrade head`` — schema migration.
      2. Check if first-run import is needed.
      3. If needed: backup → migrate → archive + report.
      4. On failure: restore backup and raise :class:`AutoMigrateError`.

    When ``AUTO_MIGRATE=false``: only step 1 runs.

    Args:
        db: The async Database instance.
        config: ConfigProvider for reading AUTO_MIGRATE and DATA_ROOT.
        data_root: Override for data root directory. Defaults to
            ``config.get_str("DATA_ROOT")``.

    Returns:
        An :class:`AutoMigrateResult` summarising what happened.

    Raises:
        AutoMigrateError: If migration fails after rollback attempt.
    """
    result = AutoMigrateResult()

    # ── Step 1: Schema upgrade (always) ──────────────────────────────────
    logger.info("自动迁移: 执行 alembic upgrade head ...")
    try:
        db.ensure_schema()
        result.schema_upgraded = True
        logger.info("自动迁移: schema 升级完成")
    except Exception as exc:
        result.errors.append(f"Schema upgrade failed: {exc}")
        logger.error("自动迁移: schema 升级失败: %s", exc, exc_info=True)
        raise AutoMigrateError(result.errors[-1]) from exc

    # ── Check AUTO_MIGRATE flag ──────────────────────────────────────────
    auto_migrate = config.get_bool("AUTO_MIGRATE")
    if not auto_migrate:
        logger.info("自动迁移: AUTO_MIGRATE=false, 跳过数据导入")
        return result

    # ── Step 2: Determine if first-run import is needed ──────────────────
    if data_root is None:
        data_root = Path(config.get_str("DATA_ROOT"))

    memory_dir = data_root / "memory"

    db_empty = await _is_db_empty(db)
    has_json = _has_legacy_json(memory_dir)

    if not db_empty or not has_json:
        reason = "DB 非空" if not db_empty else "无旧 JSON 数据"
        logger.info("自动迁移: 不需要首次导入 (%s)", reason)
        return result

    result.import_needed = True
    logger.info(
        "自动迁移: 检测到空库 + 旧 JSON, 开始首次数据导入 (source=%s)",
        memory_dir,
    )

    # ── Step 3a: Backup ──────────────────────────────────────────────────
    db_url = config.get_str("DB_URL")
    backup_dir: Path | None = None

    try:
        logger.info("自动迁移: 创建备份快照 ...")
        create_backup(db_url, data_root)
        # Find the backup directory that was just created
        backups_root = data_root / "backups"
        if backups_root.exists():
            dirs = sorted(backups_root.iterdir())
            backup_dir = dirs[-1] if dirs else None
        if backup_dir is not None:
            result.backup_dir = str(backup_dir)
            logger.info("自动迁移: 备份完成 → %s", backup_dir)
        else:
            result.errors.append("Backup completed but directory not found")
            raise AutoMigrateError(result.errors[-1])
    except Exception as exc:
        result.errors.append(f"Backup failed: {exc}")
        logger.error("自动迁移: 备份失败: %s", exc, exc_info=True)
        raise AutoMigrateError(result.errors[-1]) from exc

    # ── Step 3b: Migrate ─────────────────────────────────────────────────
    try:
        logger.info("自动迁移: 执行 migrate-from-json ...")
        migration_report = await migrate_from_json(
            source=memory_dir,
            db=db,
            dry_run=False,
        )
        result.import_performed = True
        logger.info(
            "自动迁移: 数据导入完成 (sessions=%d, messages=%d, profiles=%d, facts=%d, "
            "edges=%d, elapsed=%.2fs)",
            migration_report.sessions.inserted,
            migration_report.messages.inserted,
            migration_report.user_profiles.inserted,
            migration_report.user_facts.inserted,
            migration_report.social_edges.inserted,
            migration_report.elapsed_seconds,
        )
        if migration_report.errors:
            logger.warning(
                "自动迁移: 导入完成但有 %d 个错误: %s",
                len(migration_report.errors),
                migration_report.errors[:5],
            )
    except Exception as exc:
        # ── Step 3d: Rollback on failure ─────────────────────────────────
        result.errors.append(f"Migration failed: {exc}")
        logger.error("自动迁移: 数据导入失败: %s", exc, exc_info=True)

        # Attempt rollback
        if backup_dir is not None:
            try:
                logger.info("自动迁移: 正在回滚到备份快照 ...")
                restore_backup(
                    backup_dir,
                    db_url,
                    data_root,
                    auto_snapshot=False,
                    restore_memory=False,
                )
                result.rolled_back = True
                logger.info("自动迁移: 回滚完成")
            except Exception as restore_exc:
                result.rolled_back = False
                result.errors.append(
                    f"Rollback also failed: {restore_exc}"
                )
                logger.critical(
                    "自动迁移: 回滚也失败了! 数据可能处于不一致状态: %s",
                    restore_exc,
                    exc_info=True,
                )

        raise AutoMigrateError(
            f"Auto-migration failed; rollback={'succeeded' if result.rolled_back else 'FAILED'}: "
            + "; ".join(result.errors)
        ) from exc

    # ── Step 3c: Archive source JSON + write report ──────────────────────
    try:
        archived_to = _archive_memory_dir(memory_dir)
        result.archived_to = str(archived_to)
        logger.info("自动迁移: 源 JSON 已归档到 %s", archived_to)
    except Exception as exc:
        # Archiving is non-critical; log but don't abort
        logger.warning("自动迁移: 归档源 JSON 失败 (非致命): %s", exc)

    try:
        if backup_dir is not None:
            report_path = _write_report(
                backup_dir,
                migration_report.to_dict(),
                result,
            )
            result.report_path = str(report_path)
            logger.info("自动迁移: 报告已写入 %s", report_path)
    except Exception as exc:
        logger.warning("自动迁移: 写报告失败 (非致命): %s", exc)

    logger.info("自动迁移: 全部完成")
    return result
