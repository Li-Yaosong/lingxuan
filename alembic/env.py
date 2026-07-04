"""Alembic environment configuration for lingxuan.

Key design decisions:
- Migrations run with the **synchronous** sqlite driver (``sqlite:///``).
  The runtime uses async (``sqlite+aiosqlite:///``); both point at the same
  file, but sync avoids the complexity of ``run_async_migrations``.
- ``render_as_batch=True`` is enabled so that SQLite ALTER limitations are
  handled transparently (batch mode recreates tables for column changes).
- DB URL resolution: ``ALEMBIC_DB_URL`` env var > ``DB_URL`` env var >
  ``alembic.ini`` ``sqlalchemy.url`` fallback.
"""

from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path
from urllib.parse import urlparse

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---------------------------------------------------------------------------
# Alembic Config object
# ---------------------------------------------------------------------------

config = context.config

# ---------------------------------------------------------------------------
# Logging (only when using alembic CLI)
# ---------------------------------------------------------------------------

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Target metadata — imported from ORM models
# ---------------------------------------------------------------------------

from lingxuan.adapters.storage.orm import Base  # noqa: E402

target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# DB URL resolution
# ---------------------------------------------------------------------------


def _resolve_url() -> str:
    """Return a synchronous SQLite URL for migration execution.

    Priority: ``ALEMBIC_DB_URL`` > ``DB_URL`` > alembic.ini default.

    Async URLs (``sqlite+aiosqlite:///``) are converted to sync
    (``sqlite:///``) so that Alembic can use the standard sync driver.
    """
    url = os.environ.get("ALEMBIC_DB_URL") or os.environ.get("DB_URL") or ""
    if not url:
        # Fall back to the value in alembic.ini (already sync)
        return config.get_main_option("sqlalchemy.url", "")

    # Convert async URL to sync for migration execution
    url = url.replace("sqlite+aiosqlite:///", "sqlite:///")
    return url


def _ensure_db_dir(url: str) -> None:
    """Create the parent directory of the SQLite database file if missing."""
    parsed = urlparse(url)
    db_path = parsed.path
    if not db_path or db_path == ":memory:":
        return
    # On Windows, urlparse gives "/C:/data/file.db" for absolute paths.
    # Strip leading slash if it reveals a Windows drive letter (C:/...).
    if db_path.startswith("/") and len(db_path) >= 3 and db_path[2] == ":":
        db_path = db_path[1:]
    # On POSIX relative paths, strip the leading slash too.
    elif db_path.startswith("/") and not os.path.isabs(db_path):
        db_path = db_path[1:]
    parent = Path(db_path).parent
    if parent and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)


def _set_url_in_config() -> None:
    """Inject the resolved URL into the Alembic config so engine creation picks it up."""
    url = _resolve_url()
    _ensure_db_dir(url)
    config.set_main_option("sqlalchemy.url", url)


# ---------------------------------------------------------------------------
# Offline migration
# ---------------------------------------------------------------------------


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL scripts, no DB connection)."""
    _set_url_in_config()
    url = config.get_main_option("sqlalchemy.url")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online migration
# ---------------------------------------------------------------------------


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connected to the database)."""
    _set_url_in_config()

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
