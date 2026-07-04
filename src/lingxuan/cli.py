"""lingxuan CLI — argparse-based command-line interface.

Subcommands:
  run              Start the bot (default when no subcommand given)
  db upgrade       Run Alembic upgrade head
  db revision      Autogenerate an Alembic revision
  migrate-memory   Migrate JSON memory to SQLite
  backup           (placeholder) Backup data
  restore          (placeholder) Restore data
  admin-passwd     (placeholder) Reset admin password

Global options:
  --data-root      Override DATA_ROOT config
  --db-url         Override DB_URL config
"""

from __future__ import annotations

import argparse
import os
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lingxuan",
        description="灵轩 — AI QQ 助手",
    )
    parser.add_argument(
        "--data-root",
        default=None,
        help="Override DATA_ROOT (data file directory)",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="Override DB_URL (SQLAlchemy database URL)",
    )

    sub = parser.add_subparsers(dest="command")

    # ── run ──────────────────────────────────────────────────────────────
    sub.add_parser("run", help="Start the bot (default)")

    # ── db ───────────────────────────────────────────────────────────────
    db_parser = sub.add_parser("db", help="Database management")
    db_sub = db_parser.add_subparsers(dest="db_command")

    db_upgrade = db_sub.add_parser("upgrade", help="Run Alembic upgrade head")
    db_upgrade.add_argument(
        "revision",
        nargs="?",
        default="head",
        help="Target revision (default: head)",
    )

    db_revision = db_sub.add_parser("revision", help="Autogenerate an Alembic revision")
    db_revision.add_argument(
        "-m", "--message",
        required=True,
        help="Revision message",
    )

    # ── migrate-memory ──────────────────────────────────────────────────
    mm_parser = sub.add_parser("migrate-memory", help="Migrate JSON memory to SQLite")
    mm_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Scan and validate only; do not write to DB",
    )
    mm_parser.add_argument(
        "--source",
        default=None,
        help="Source directory (default: {DATA_ROOT}/memory)",
    )
    mm_parser.add_argument(
        "--report",
        default=None,
        help="Write JSON migration report to this file",
    )
    mm_parser.add_argument(
        "--archive",
        action="store_true",
        default=False,
        help="Rename source dir to {source}.imported/ after successful migration",
    )

    # ── backup ────────────────────────────────────────────────────────────
    backup_parser = sub.add_parser("backup", help="Backup data")
    backup_parser.add_argument(
        "--out",
        default=None,
        help="Output directory for backups (default: {DATA_ROOT}/backups)",
    )

    # ── restore ──────────────────────────────────────────────────────────
    restore_parser = sub.add_parser("restore", help="Restore data from backup")
    restore_parser.add_argument(
        "--from",
        dest="from_dir",
        required=True,
        help="Backup directory to restore from",
    )
    restore_parser.add_argument(
        "--yes",
        action="store_true",
        default=False,
        help="Skip confirmation prompt",
    )
    restore_parser.add_argument(
        "--no-memory",
        action="store_true",
        default=False,
        help="Do not restore memory.zip",
    )

    # ── placeholder subcommands ──────────────────────────────────────────
    sub.add_parser("admin-passwd", help="Reset admin password (not yet implemented)")

    return parser


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def _apply_global_overrides(args: argparse.Namespace) -> None:
    """Inject --data-root / --db-url into environment so ConfigProvider picks them up."""
    if args.data_root is not None:
        os.environ["DATA_ROOT"] = args.data_root
    if args.db_url is not None:
        os.environ["DB_URL"] = args.db_url


def _cmd_run(_args: argparse.Namespace) -> None:
    """Delegate to bootstrap.main()."""
    from lingxuan.bootstrap import main as bootstrap_main

    bootstrap_main()


def _resolve_sync_db_url(args: argparse.Namespace) -> str:
    """Resolve the synchronous SQLite URL from CLI args / env / default.

    Also ensures the DB_URL env var is set so that ``alembic/env.py``'s
    ``_resolve_url()`` picks it up when running migrations programmatically.
    """
    db_url = args.db_url or os.environ.get("DB_URL", "sqlite+aiosqlite:///data/lingxuan.db")
    # Ensure env.py sees the URL via environment variable
    os.environ["DB_URL"] = db_url
    return db_url.replace("sqlite+aiosqlite:///", "sqlite:///")


def _cmd_db_upgrade(args: argparse.Namespace) -> None:
    """Run Alembic upgrade to the target revision."""
    from alembic import command
    from alembic.config import Config

    from lingxuan.adapters.storage.db import _ensure_db_dir

    cfg = Config()
    cfg.set_main_option("script_location", "alembic")

    sync_url = _resolve_sync_db_url(args)
    cfg.set_main_option("sqlalchemy.url", sync_url)

    _ensure_db_dir(sync_url)

    revision = args.revision
    command.upgrade(cfg, revision)
    print(f"Database upgraded to: {revision}")


def _cmd_db_revision(args: argparse.Namespace) -> None:
    """Autogenerate an Alembic revision."""
    from alembic import command
    from alembic.config import Config

    from lingxuan.adapters.storage.db import _ensure_db_dir

    cfg = Config()
    cfg.set_main_option("script_location", "alembic")

    sync_url = _resolve_sync_db_url(args)
    cfg.set_main_option("sqlalchemy.url", sync_url)

    _ensure_db_dir(sync_url)

    command.revision(cfg, message=args.message, autogenerate=True)
    print(f"Revision generated: {args.message}")


def _cmd_not_implemented(name: str) -> None:
    """Print a not-yet-implemented message and exit with code 1."""
    print(f"子命令 '{name}' 尚未实现，将在后续任务中完成。", file=sys.stderr)
    sys.exit(1)


def _resolve_config_paths(args: argparse.Namespace) -> tuple[str, str]:
    """Return (db_url, data_root) from CLI overrides / env / defaults."""
    from lingxuan.settings_defaults import SETTINGS_BY_KEY

    db_url = args.db_url or os.environ.get(
        "DB_URL", str(SETTINGS_BY_KEY["DB_URL"].default)
    )
    data_root = args.data_root or os.environ.get(
        "DATA_ROOT", str(SETTINGS_BY_KEY["DATA_ROOT"].default)
    )
    return db_url, data_root


def _cmd_backup(args: argparse.Namespace) -> None:
    """Create a backup snapshot of the database and optional source JSON."""
    from pathlib import Path

    from lingxuan.migration.backup import create_backup

    db_url, data_root = _resolve_config_paths(args)
    out_dir = Path(args.out) if args.out else None
    manifest = create_backup(db_url, Path(data_root), out_dir)

    actual_out = out_dir or (Path(data_root) / "backups")
    dirs = sorted(actual_out.iterdir()) if actual_out.exists() else []
    backup_path = dirs[-1] if dirs else actual_out / "unknown"
    print(f"✓ 备份完成: {backup_path}")
    print(f"  数据库大小: {manifest['db_size']} bytes")
    print(f"  包含 memory.zip: {'是' if manifest.get('includes_memory_zip') else '否'}")


def _cmd_restore(args: argparse.Namespace) -> None:
    """Restore the database from a backup snapshot."""
    from pathlib import Path

    from lingxuan.migration.backup import restore_backup

    db_url, data_root = _resolve_config_paths(args)
    backup_dir = Path(args.from_dir)

    if not backup_dir.is_dir():
        print(f"✗ 备份目录不存在: {backup_dir}", file=sys.stderr)
        sys.exit(1)

    # Confirmation prompt (unless --yes)
    if not args.yes:
        print(f"⚠ 即将从备份恢复数据库: {backup_dir}")
        print("  此操作将覆盖当前数据库！恢复前会自动创建当前状态的快照。")
        answer = input("确认恢复？(y/N): ").strip().lower()
        if answer != "y":
            print("已取消。")
            return

    try:
        manifest = restore_backup(
            backup_dir,
            db_url,
            Path(data_root),
            auto_snapshot=True,
            restore_memory=not args.no_memory,
        )
        print(f"✓ 恢复完成 (来源: {backup_dir})")
        print(f"  备份时间: {manifest['timestamp']}")
        if manifest.get("includes_memory_zip") and not args.no_memory:
            print("  memory.zip 已恢复")
    except (FileNotFoundError, ValueError) as e:
        print(f"✗ 恢复失败: {e}", file=sys.stderr)
        sys.exit(1)


def _cmd_migrate_memory(args: argparse.Namespace) -> None:
    """Run JSON→DB migration."""
    import asyncio
    import json as json_mod
    from pathlib import Path

    from lingxuan.adapters.storage.db import Database
    from lingxuan.migration.from_json import migrate_from_json

    # Resolve source directory
    source = args.source
    if source is None:
        data_root = os.environ.get("DATA_ROOT", "./data")
        source = os.path.join(data_root, "memory")
    source_path = Path(source)

    # Resolve DB URL — reuse the same logic as db upgrade
    db_url = args.db_url or os.environ.get("DB_URL", "sqlite+aiosqlite:///data/lingxuan.db")
    os.environ["DB_URL"] = db_url

    # Ensure schema first
    db = Database(db_url)
    db.ensure_schema()

    async def _run() -> None:
        try:
            report = await migrate_from_json(
                source=source_path,
                db=db,
                dry_run=args.dry_run,
            )

            # Print summary
            r = report
            mode = "DRY-RUN" if r.dry_run else "MIGRATED"
            print(f"\n{'='*50}")
            print(f"  JSON→DB Migration Report  [{mode}]")
            print(f"{'='*50}")
            print(f"  Source:       {r.source}")
            print(f"  Elapsed:      {r.elapsed_seconds:.2f}s")
            print(f"  Sessions:     {r.sessions.scanned} scanned, {r.sessions.inserted} inserted, {r.sessions.skipped} skipped")
            print(f"  Messages:     {r.messages.scanned} scanned, {r.messages.inserted} inserted, {r.messages.skipped} skipped")
            print(f"  Entities:     {r.entities.scanned} scanned, {r.entities.inserted} inserted, {r.entities.skipped} skipped")
            print(f"  Profiles:     {r.user_profiles.scanned} scanned, {r.user_profiles.inserted} inserted, {r.user_profiles.skipped} skipped")
            print(f"  Facts:        {r.user_facts.scanned} scanned, {r.user_facts.inserted} inserted, {r.user_facts.skipped} skipped")
            print(f"  Social edges: {r.social_edges.scanned} scanned, {r.social_edges.inserted} inserted, {r.social_edges.skipped} skipped")
            print(f"  Name index:   {r.name_index.scanned} scanned, {r.name_index.inserted} inserted, {r.name_index.skipped} skipped")

            if r.errors:
                print(f"\n  Errors ({len(r.errors)}):")
                for err in r.errors:
                    print(f"    - {err}")

            if r.sessions.skipped_reasons or r.user_profiles.skipped_reasons or r.user_facts.skipped_reasons:
                all_reasons = (
                    r.sessions.skipped_reasons
                    + r.user_profiles.skipped_reasons
                    + r.user_facts.skipped_reasons
                    + r.social_edges.skipped_reasons
                    + r.name_index.skipped_reasons
                    + r.entities.skipped_reasons
                )
                if all_reasons:
                    print(f"\n  Skipped items ({len(all_reasons)}):")
                    for reason in all_reasons[:20]:
                        print(f"    - {reason}")
                    if len(all_reasons) > 20:
                        print(f"    ... and {len(all_reasons) - 20} more")

            print(f"{'='*50}\n")

            # Write report file if requested
            if args.report:
                report_path = Path(args.report)
                report_path.write_text(
                    json_mod.dumps(r.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(f"Report written to: {args.report}")

            # Archive source directory if requested and migration was successful
            if args.archive and not args.dry_run and not r.errors and source_path.is_dir():
                archived = source_path.with_name(source_path.name + ".imported")
                source_path.rename(archived)
                print(f"Source directory archived to: {archived}")
            elif not args.dry_run and not r.errors and source_path.is_dir():
                print(f"提示: 源目录保留在 {source_path}，可用 --archive 参数在迁移成功后自动归档")

            if r.errors:
                sys.exit(1)
        finally:
            await db.dispose()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Public API (for testing without sys.argv mutation)
# ---------------------------------------------------------------------------


def dispatch(args: argparse.Namespace) -> None:
    """Route parsed args to the appropriate handler.

    Separated from ``main()`` so that tests can call it with pre-built args
    without touching ``sys.argv``.
    """
    _apply_global_overrides(args)

    command = args.command

    # No subcommand → default to 'run'
    if command is None:
        _cmd_run(args)
        return

    if command == "run":
        _cmd_run(args)
    elif command == "db":
        db_cmd = args.db_command
        if db_cmd == "upgrade":
            _cmd_db_upgrade(args)
        elif db_cmd == "revision":
            _cmd_db_revision(args)
        else:
            print("用法: lingxuan db {upgrade|revision}", file=sys.stderr)
            sys.exit(1)
    elif command == "migrate-memory":
        _cmd_migrate_memory(args)
    elif command == "backup":
        _cmd_backup(args)
    elif command == "restore":
        _cmd_restore(args)
    elif command == "admin-passwd":
        _cmd_not_implemented("admin-passwd")
    else:
        print(f"未知子命令: {command}", file=sys.stderr)
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``lingxuan`` console script."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    dispatch(args)


if __name__ == "__main__":
    main()
