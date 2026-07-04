"""Contract tests for ConfigRepository, AuditRepository, PluginConfigRepository,
and AdminUserRepository — parameterized over InMemory and SQLite.

Both InMemory and SQL implementations must satisfy the same behavioural
contract defined by their Protocol interfaces.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pytest

from lingxuan.adapters.storage.db import Database
from lingxuan.adapters.storage.repositories import (
    SqlAdminUserRepository,
    SqlAuditRepository,
    SqlConfigRepository,
    SqlPluginConfigRepository,
)
from lingxuan.protocols.repositories import AdminUserRow, AuditEntry
from tests.fakes.repositories import (
    InMemoryAdminUserRepository,
    InMemoryAuditRepository,
    InMemoryConfigRepository,
    InMemoryPluginConfigRepository,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def inmemory_config_repo() -> InMemoryConfigRepository:
    return InMemoryConfigRepository()


@pytest.fixture
async def sql_config_repo() -> SqlConfigRepository:
    db = Database("sqlite+aiosqlite://")
    await db.create_all()
    yield SqlConfigRepository(db)
    await db.dispose()


@pytest.fixture
def inmemory_audit_repo() -> InMemoryAuditRepository:
    return InMemoryAuditRepository()


@pytest.fixture
async def sql_audit_repo() -> SqlAuditRepository:
    db = Database("sqlite+aiosqlite://")
    await db.create_all()
    yield SqlAuditRepository(db)
    await db.dispose()


@pytest.fixture
def inmemory_plugin_config_repo() -> InMemoryPluginConfigRepository:
    return InMemoryPluginConfigRepository()


@pytest.fixture
async def sql_plugin_config_repo() -> SqlPluginConfigRepository:
    db = Database("sqlite+aiosqlite://")
    await db.create_all()
    yield SqlPluginConfigRepository(db)
    await db.dispose()


@pytest.fixture
def inmemory_admin_user_repo() -> InMemoryAdminUserRepository:
    return InMemoryAdminUserRepository()


@pytest.fixture
async def sql_admin_user_repo() -> SqlAdminUserRepository:
    db = Database("sqlite+aiosqlite://")
    await db.create_all()
    yield SqlAdminUserRepository(db)
    await db.dispose()


# ===========================================================================
# ConfigRepository contract
# ===========================================================================


class ConfigRepoContract:
    """Mixin with all ConfigRepository contract tests.

    Inherit and provide ``config_repo`` fixture to run against any impl.
    """

    # -- get_all empty ---

    async def test_get_all_empty(self, config_repo: InMemoryConfigRepository | SqlConfigRepository) -> None:
        assert await config_repo.get_all() == {}

    # -- set + get_all round-trip ---

    async def test_set_and_get_all(self, config_repo: InMemoryConfigRepository | SqlConfigRepository) -> None:
        await config_repo.set("BOT_NAME", "灵轩")
        result = await config_repo.get_all()
        assert result == {"BOT_NAME": "灵轩"}

    # -- value types: int, bool, list, str ---

    async def test_value_roundtrip_str(self, config_repo: InMemoryConfigRepository | SqlConfigRepository) -> None:
        await config_repo.set("key_str", "hello")
        assert (await config_repo.get_all())["key_str"] == "hello"

    async def test_value_roundtrip_int(self, config_repo: InMemoryConfigRepository | SqlConfigRepository) -> None:
        await config_repo.set("key_int", 42)
        assert (await config_repo.get_all())["key_int"] == 42

    async def test_value_roundtrip_bool(self, config_repo: InMemoryConfigRepository | SqlConfigRepository) -> None:
        await config_repo.set("key_bool", True)
        assert (await config_repo.get_all())["key_bool"] is True

    async def test_value_roundtrip_list(self, config_repo: InMemoryConfigRepository | SqlConfigRepository) -> None:
        await config_repo.set("key_list", [1, "two", 3])
        assert (await config_repo.get_all())["key_list"] == [1, "two", 3]

    # -- set overwrites ---

    async def test_set_overwrites(self, config_repo: InMemoryConfigRepository | SqlConfigRepository) -> None:
        await config_repo.set("key", "old")
        await config_repo.set("key", "new")
        assert (await config_repo.get_all())["key"] == "new"

    # -- bulk_set ---

    async def test_bulk_set(self, config_repo: InMemoryConfigRepository | SqlConfigRepository) -> None:
        await config_repo.bulk_set({"a": 1, "b": True, "c": "hi"})
        result = await config_repo.get_all()
        assert result == {"a": 1, "b": True, "c": "hi"}

    async def test_bulk_set_merges(self, config_repo: InMemoryConfigRepository | SqlConfigRepository) -> None:
        await config_repo.set("existing", "old")
        await config_repo.bulk_set({"new_key": 99})
        result = await config_repo.get_all()
        assert result["existing"] == "old"
        assert result["new_key"] == 99

    async def test_bulk_set_empty_is_noop(self, config_repo: InMemoryConfigRepository | SqlConfigRepository) -> None:
        await config_repo.set("key", "val")
        await config_repo.bulk_set({})
        assert await config_repo.get_all() == {"key": "val"}


# --- InMemory ConfigRepository tests ---


class TestInMemoryConfigRepo(ConfigRepoContract):
    @pytest.fixture
    def config_repo(self, inmemory_config_repo: InMemoryConfigRepository) -> InMemoryConfigRepository:
        return inmemory_config_repo


# --- SQL ConfigRepository tests ---


class TestSqlConfigRepo(ConfigRepoContract):
    @pytest.fixture
    def config_repo(self, sql_config_repo: SqlConfigRepository) -> SqlConfigRepository:
        return sql_config_repo


# ===========================================================================
# AuditRepository contract
# ===========================================================================


class AuditRepoContract:
    """Mixin with all AuditRepository contract tests."""

    # -- query empty ---

    async def test_query_empty(self, audit_repo: InMemoryAuditRepository | SqlAuditRepository) -> None:
        assert await audit_repo.query() == []

    # -- record + query ---

    async def test_record_and_query(self, audit_repo: InMemoryAuditRepository | SqlAuditRepository) -> None:
        await audit_repo.record(actor="admin", action="login")
        entries = await audit_repo.query()
        assert len(entries) == 1
        assert entries[0].actor == "admin"
        assert entries[0].action == "login"

    # -- record with all fields ---

    async def test_record_with_all_fields(self, audit_repo: InMemoryAuditRepository | SqlAuditRepository) -> None:
        await audit_repo.record(
            actor="admin",
            action="config_update",
            target="BOT_NAME",
            detail={"old": "灵轩", "new": "小灵"},
            ip="127.0.0.1",
            success=True,
        )
        entries = await audit_repo.query()
        assert len(entries) == 1
        e = entries[0]
        assert e.actor == "admin"
        assert e.action == "config_update"
        assert e.target == "BOT_NAME"
        assert e.detail == {"old": "灵轩", "new": "小灵"}
        assert e.ip == "127.0.0.1"
        assert e.success is True

    # -- record with success=False ---

    async def test_record_failure(self, audit_repo: InMemoryAuditRepository | SqlAuditRepository) -> None:
        await audit_repo.record(actor="hacker", action="login", success=False)
        entries = await audit_repo.query()
        assert entries[0].success is False

    # -- query by actor ---

    async def test_query_filter_by_actor(self, audit_repo: InMemoryAuditRepository | SqlAuditRepository) -> None:
        await audit_repo.record(actor="admin", action="login")
        await audit_repo.record(actor="user", action="login")
        entries = await audit_repo.query(actor="admin")
        assert len(entries) == 1
        assert entries[0].actor == "admin"

    # -- query by action ---

    async def test_query_filter_by_action(self, audit_repo: InMemoryAuditRepository | SqlAuditRepository) -> None:
        await audit_repo.record(actor="admin", action="login")
        await audit_repo.record(actor="admin", action="logout")
        entries = await audit_repo.query(action="login")
        assert len(entries) == 1
        assert entries[0].action == "login"

    # -- query limit ---

    async def test_query_limit(self, audit_repo: InMemoryAuditRepository | SqlAuditRepository) -> None:
        for i in range(5):
            await audit_repo.record(actor="admin", action=f"action_{i}")
        entries = await audit_repo.query(limit=3)
        assert len(entries) == 3

    # -- keyset pagination (before_id) ---

    async def test_query_before_id(self, audit_repo: InMemoryAuditRepository | SqlAuditRepository) -> None:
        for i in range(5):
            await audit_repo.record(actor="admin", action=f"action_{i}")
        # Get the most recent entry
        all_entries = await audit_repo.query(limit=1)
        latest_id = all_entries[0].id
        # Query entries before that id
        earlier = await audit_repo(before_id=latest_id) if False else await audit_repo.query(before_id=latest_id)
        assert all(e.id < latest_id for e in earlier)

    # -- multiple records are returned ---

    async def test_query_multiple_records(self, audit_repo: InMemoryAuditRepository | SqlAuditRepository) -> None:
        await audit_repo.record(actor="admin", action="first")
        await audit_repo.record(actor="admin", action="second")
        await audit_repo.record(actor="admin", action="third")
        entries = await audit_repo.query()
        assert len(entries) == 3
        actions = {e.action for e in entries}
        assert actions == {"first", "second", "third"}


# --- InMemory AuditRepository tests ---


class TestInMemoryAuditRepo(AuditRepoContract):
    @pytest.fixture
    def audit_repo(self, inmemory_audit_repo: InMemoryAuditRepository) -> InMemoryAuditRepository:
        return inmemory_audit_repo


# --- SQL AuditRepository tests ---


class TestSqlAuditRepo(AuditRepoContract):
    @pytest.fixture
    def audit_repo(self, sql_audit_repo: SqlAuditRepository) -> SqlAuditRepository:
        return sql_audit_repo

    # -- SQL-specific: descending order ---

    async def test_sql_query_descending_order(self, sql_audit_repo: SqlAuditRepository) -> None:
        await sql_audit_repo.record(actor="admin", action="first")
        await sql_audit_repo.record(actor="admin", action="second")
        await sql_audit_repo.record(actor="admin", action="third")
        entries = await sql_audit_repo.query()
        assert entries[0].id > entries[1].id > entries[2].id


# ===========================================================================
# PluginConfigRepository contract
# ===========================================================================


class PluginConfigRepoContract:
    """Mixin with all PluginConfigRepository contract tests."""

    # -- get returns None for missing ---

    async def test_get_missing_returns_none(self, plugin_config_repo: InMemoryPluginConfigRepository | SqlPluginConfigRepository) -> None:
        assert await plugin_config_repo.get("nonexistent") is None

    # -- upsert + get ---

    async def test_upsert_and_get(self, plugin_config_repo: InMemoryPluginConfigRepository | SqlPluginConfigRepository) -> None:
        await plugin_config_repo.upsert("my_plugin", enabled=True, config={"threshold": 0.5})
        result = await plugin_config_repo.get("my_plugin")
        assert result is not None
        assert result == (True, {"threshold": 0.5})

    # -- upsert overwrites ---

    async def test_upsert_overwrites(self, plugin_config_repo: InMemoryPluginConfigRepository | SqlPluginConfigRepository) -> None:
        await plugin_config_repo.upsert("p1", enabled=True, config={"a": 1})
        await plugin_config_repo.upsert("p1", enabled=False, config={"b": 2})
        result = await plugin_config_repo.get("p1")
        assert result == (False, {"b": 2})

    # -- all ---

    async def test_all_empty(self, plugin_config_repo: InMemoryPluginConfigRepository | SqlPluginConfigRepository) -> None:
        assert await plugin_config_repo.all() == {}

    async def test_all_returns_all(self, plugin_config_repo: InMemoryPluginConfigRepository | SqlPluginConfigRepository) -> None:
        await plugin_config_repo.upsert("p1", enabled=True, config={"x": 1})
        await plugin_config_repo.upsert("p2", enabled=False, config={"y": 2})
        result = await plugin_config_repo.all()
        assert result == {
            "p1": (True, {"x": 1}),
            "p2": (False, {"y": 2}),
        }

    # -- disabled plugin ---

    async def test_disabled_plugin(self, plugin_config_repo: InMemoryPluginConfigRepository | SqlPluginConfigRepository) -> None:
        await plugin_config_repo.upsert("disabled_plugin", enabled=False, config={})
        result = await plugin_config_repo.get("disabled_plugin")
        assert result is not None
        assert result[0] is False


# --- InMemory PluginConfigRepository tests ---


class TestInMemoryPluginConfigRepo(PluginConfigRepoContract):
    @pytest.fixture
    def plugin_config_repo(self, inmemory_plugin_config_repo: InMemoryPluginConfigRepository) -> InMemoryPluginConfigRepository:
        return inmemory_plugin_config_repo


# --- SQL PluginConfigRepository tests ---


class TestSqlPluginConfigRepo(PluginConfigRepoContract):
    @pytest.fixture
    def plugin_config_repo(self, sql_plugin_config_repo: SqlPluginConfigRepository) -> SqlPluginConfigRepository:
        return sql_plugin_config_repo


# ===========================================================================
# AdminUserRepository contract
# ===========================================================================


class AdminUserRepoContract:
    """Mixin with all AdminUserRepository contract tests."""

    # -- get_by_username returns None for missing ---

    async def test_get_by_username_missing(self, admin_user_repo: InMemoryAdminUserRepository | SqlAdminUserRepository) -> None:
        assert await admin_user_repo.get_by_username("nobody") is None

    # -- create + get_by_username ---

    async def test_create_and_get(self, admin_user_repo: InMemoryAdminUserRepository | SqlAdminUserRepository) -> None:
        await admin_user_repo.create(
            username="admin",
            password_hash="hashed123",
            role="admin",
        )
        row = await admin_user_repo.get_by_username("admin")
        assert row is not None
        assert row.username == "admin"
        assert row.password_hash == "hashed123"
        assert row.role == "admin"
        assert row.must_change_password is True

    # -- create with must_change_password=False ---

    async def test_create_without_must_change(self, admin_user_repo: InMemoryAdminUserRepository | SqlAdminUserRepository) -> None:
        await admin_user_repo.create(
            username="admin2",
            password_hash="hash2",
            role="admin",
            must_change_password=False,
        )
        row = await admin_user_repo.get_by_username("admin2")
        assert row is not None
        assert row.must_change_password is False

    # -- set_password ---

    async def test_set_password(self, admin_user_repo: InMemoryAdminUserRepository | SqlAdminUserRepository) -> None:
        await admin_user_repo.create(
            username="admin",
            password_hash="old_hash",
            role="admin",
        )
        await admin_user_repo.set_password("admin", "new_hash", must_change_password=False)
        row = await admin_user_repo.get_by_username("admin")
        assert row is not None
        assert row.password_hash == "new_hash"
        assert row.must_change_password is False

    # -- set_password clears must_change_password ---

    async def test_set_password_clears_must_change(self, admin_user_repo: InMemoryAdminUserRepository | SqlAdminUserRepository) -> None:
        await admin_user_repo.create(
            username="admin",
            password_hash="old",
            role="admin",
            must_change_password=True,
        )
        await admin_user_repo.set_password("admin", "new", must_change_password=False)
        row = await admin_user_repo.get_by_username("admin")
        assert row is not None
        assert row.must_change_password is False

    # -- touch_login ---

    async def test_touch_login(self, admin_user_repo: InMemoryAdminUserRepository | SqlAdminUserRepository) -> None:
        await admin_user_repo.create(
            username="admin",
            password_hash="hash",
            role="admin",
        )
        row_before = await admin_user_repo.get_by_username("admin")
        assert row_before is not None
        assert row_before.last_login_at is None

        await admin_user_repo.touch_login("admin")
        row_after = await admin_user_repo.get_by_username("admin")
        assert row_after is not None
        assert row_after.last_login_at is not None

    # -- count ---

    async def test_count_empty(self, admin_user_repo: InMemoryAdminUserRepository | SqlAdminUserRepository) -> None:
        assert await admin_user_repo.count() == 0

    async def test_count_after_creates(self, admin_user_repo: InMemoryAdminUserRepository | SqlAdminUserRepository) -> None:
        await admin_user_repo.create(username="a", password_hash="h", role="admin")
        await admin_user_repo.create(username="b", password_hash="h", role="viewer")
        assert await admin_user_repo.count() == 2

    # -- created_at is set ---

    async def test_created_at_is_set(self, admin_user_repo: InMemoryAdminUserRepository | SqlAdminUserRepository) -> None:
        await admin_user_repo.create(
            username="admin",
            password_hash="hash",
            role="admin",
        )
        row = await admin_user_repo.get_by_username("admin")
        assert row is not None
        assert row.created_at is not None


# --- InMemory AdminUserRepository tests ---


class TestInMemoryAdminUserRepo(AdminUserRepoContract):
    @pytest.fixture
    def admin_user_repo(self, inmemory_admin_user_repo: InMemoryAdminUserRepository) -> InMemoryAdminUserRepository:
        return inmemory_admin_user_repo


# --- SQL AdminUserRepository tests ---


class TestSqlAdminUserRepo(AdminUserRepoContract):
    @pytest.fixture
    def admin_user_repo(self, sql_admin_user_repo: SqlAdminUserRepository) -> SqlAdminUserRepository:
        return sql_admin_user_repo
