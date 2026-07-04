"""lingxuan CLI — argparse-based command-line interface.

Subcommands:
  run              Start the bot (default when no subcommand given)
  db upgrade       Run Alembic upgrade head
  db revision      Autogenerate an Alembic revision
  migrate-memory   (placeholder) Migrate JSON memory to SQLite
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

    # ── placeholder subcommands ──────────────────────────────────────────
    sub.add_parser("migrate-memory", help="Migrate JSON memory to SQLite (not yet implemented)")
    sub.add_parser("backup", help="Backup data (not yet implemented)")
    sub.add_parser("restore", help="Restore data (not yet implemented)")
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
        _cmd_not_implemented("migrate-memory")
    elif command == "backup":
        _cmd_not_implemented("backup")
    elif command == "restore":
        _cmd_not_implemented("restore")
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
