"""Tests for admin plugin management + audit API (P5-06).

Covers:
- GET /plugins: list reflects registry + persisted state
- PUT /plugins/{name}: enable/disable, config update, reload strategy
- PUT dispatch behavior changes after enable/disable
- GET /audit: filtered query, keyset pagination, admin-only access
- Audit records created for all write operations
- Permission checks (readonly vs admin)
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from lingxuan.admin.app import create_admin_app
from lingxuan.admin.auth import create_access_token, hash_password
from lingxuan.container import Container
from lingxuan.plugins.host import DefaultPluginHost
from lingxuan.protocols.plugins import HookType, Plugin, PluginContext, PluginHost
from tests.fakes.config import FakeConfigProvider
from tests.fakes.logsink import FakeLogSink
from tests.fakes.repositories import (
    InMemoryAdminUserRepository,
    InMemoryAuditRepository,
    InMemoryConfigRepository,
    InMemoryPluginConfigRepository,
    InMemorySessionRepository,
    InMemorySocialGraphRepository,
    InMemoryUserProfileRepository,
)


# ---------------------------------------------------------------------------
# Fake plugins for testing
# ---------------------------------------------------------------------------


class FakePlugin(Plugin):
    """Minimal plugin that subscribes to on_inbound_message."""

    name: str
    version: str

    def __init__(self, name: str = "fake_plugin", version: str = "1.0.0") -> None:
        self.name = name
        self.version = version
        self.setup_called = False
        self.teardown_called = False
        self.last_config: dict = {}
        self.last_ctx: PluginContext | None = None

    def setup(self, host: PluginHost, config: dict, services: object = None) -> None:
        self.setup_called = True
        self.last_config = config
        host.subscribe(HookType.on_inbound_message, self._on_inbound)

    async def teardown(self) -> None:
        self.teardown_called = True

    async def _on_inbound(self, ctx: PluginContext) -> PluginContext:
        self.last_ctx = ctx
        return ctx


class FakePluginWithConfigChange(Plugin):
    """Plugin that subscribes to both on_inbound_message and on_config_change."""

    name: str
    version: str

    def __init__(self, name: str = "hot_plugin", version: str = "2.0.0") -> None:
        self.name = name
        self.version = version
        self.last_config: dict = {}
        self.config_change_received: dict | None = None

    def setup(self, host: PluginHost, config: dict, services: object = None) -> None:
        self.last_config = config
        host.subscribe(HookType.on_inbound_message, self._on_inbound)
        host.subscribe(HookType.on_config_change, self._on_config_change)

    async def teardown(self) -> None:
        pass

    async def _on_inbound(self, ctx: PluginContext) -> PluginContext:
        return ctx

    async def _on_config_change(self, ctx: PluginContext) -> PluginContext:
        self.config_change_received = ctx.extra
        return ctx


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SECRET_KEY = "test-secret-key-for-jwt-signing-not-for-production"


def _make_container(*, host: DefaultPluginHost | None = None) -> Container:
    c = Container()
    c.override(
        "config",
        FakeConfigProvider(overrides={
            "SECRET_KEY": SECRET_KEY,
            "JWT_ACCESS_TTL": 900,
            "JWT_REFRESH_TTL": 604800,
            "DATA_ROOT": "/tmp/lingxuan-test-data",
        }),
    )
    c.override("log", FakeLogSink())
    c.override("session_repo", InMemorySessionRepository())
    c.override("user_profile_repo", InMemoryUserProfileRepository())
    c.override("social_graph_repo", InMemorySocialGraphRepository())
    c.override("config_repo", InMemoryConfigRepository())
    c.override("audit_repo", InMemoryAuditRepository())
    c.override("plugin_config_repo", InMemoryPluginConfigRepository())
    c.override("admin_user_repo", InMemoryAdminUserRepository())
    if host is not None:
        c.override("plugin_host", host)
    return c


@pytest.fixture
def fake_host() -> DefaultPluginHost:
    return DefaultPluginHost()


@pytest.fixture
def container(fake_host: DefaultPluginHost) -> Container:
    return _make_container(host=fake_host)


@pytest.fixture
def client(container: Container) -> TestClient:
    app = create_admin_app(container)
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def admin_repo(container: Container) -> InMemoryAdminUserRepository:
    return container.admin_user_repo  # type: ignore[return-value]


@pytest.fixture
def audit_repo(container: Container) -> InMemoryAuditRepository:
    return container.audit_repo  # type: ignore[return-value]


@pytest.fixture
def plugin_config_repo(container: Container) -> InMemoryPluginConfigRepository:
    return container.plugin_config_repo  # type: ignore[return-value]


@pytest.fixture
def host(container: Container) -> DefaultPluginHost:
    return container.plugin_host  # type: ignore[return-value]


def _create_admin(
    repo: InMemoryAdminUserRepository,
    username: str = "admin",
    password: str = "Admin123!",
    role: str = "admin",
) -> None:
    async def _do() -> None:
        await repo.create(
            username=username,
            password_hash=hash_password(password),
            role=role,
            must_change_password=False,
        )

    asyncio.run(_do())


def _login(client: TestClient, username: str = "admin", password: str = "Admin123!") -> dict:
    resp = client.post("/admin/api/auth/login", json={
        "username": username,
        "password": password,
    })
    assert resp.status_code == 200
    return resp.json()


def _auth_headers(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


# ---------------------------------------------------------------------------
# GET /plugins
# ---------------------------------------------------------------------------


class TestListPlugins:
    def test_empty_registry(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.get("/admin/api/plugins", headers=_auth_headers(tokens))
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []

    def test_reflects_registered_plugin(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        host: DefaultPluginHost,
    ) -> None:
        plugin = FakePlugin()
        host.register(plugin, config={"enabled": True})

        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.get("/admin/api/plugins", headers=_auth_headers(tokens))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["name"] == "fake_plugin"
        assert item["version"] == "1.0.0"
        assert item["enabled"] is True
        assert "on_inbound_message" in item["hooks"]
        assert item["config_reload_strategy"] == "reload"

    def test_reflects_disabled_plugin(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        host: DefaultPluginHost,
    ) -> None:
        plugin = FakePlugin()
        host.register(plugin, config={"enabled": True})
        host.disable("fake_plugin")

        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.get("/admin/api/plugins", headers=_auth_headers(tokens))
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"][0]["enabled"] is False

    def test_hot_reload_strategy_for_config_change_hook(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        host: DefaultPluginHost,
    ) -> None:
        plugin = FakePluginWithConfigChange()
        host.register(plugin, config={"enabled": True})

        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.get("/admin/api/plugins", headers=_auth_headers(tokens))
        assert resp.status_code == 200
        data = resp.json()
        item = data["items"][0]
        assert item["config_reload_strategy"] == "hot"
        assert "on_config_change" in item["hooks"]

    def test_readonly_user_can_list(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
    ) -> None:
        _create_admin(admin_repo, username="viewer", role="readonly")
        tokens = _login(client, username="viewer")
        resp = client.get("/admin/api/plugins", headers=_auth_headers(tokens))
        assert resp.status_code == 200

    def test_unauthenticated_rejected(
        self, client: TestClient,
    ) -> None:
        resp = client.get("/admin/api/plugins")
        assert resp.status_code == 401

    def test_persisted_config_merged(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        host: DefaultPluginHost, plugin_config_repo: InMemoryPluginConfigRepository,
    ) -> None:
        plugin = FakePlugin()
        host.register(plugin, config={"enabled": True})
        # Persist a custom config
        asyncio.run(plugin_config_repo.upsert("fake_plugin", enabled=True, config={"threshold": 0.5}))

        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.get("/admin/api/plugins", headers=_auth_headers(tokens))
        assert resp.status_code == 200
        data = resp.json()
        item = data["items"][0]
        assert item["config"] == {"threshold": 0.5}


# ---------------------------------------------------------------------------
# PUT /plugins/{name}
# ---------------------------------------------------------------------------


class TestUpdatePlugin:
    def test_enable_plugin(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        host: DefaultPluginHost,
    ) -> None:
        plugin = FakePlugin()
        host.register(plugin, config={"enabled": True})
        host.disable("fake_plugin")

        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.put(
            "/admin/api/plugins/fake_plugin",
            json={"enabled": True},
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["name"] == "fake_plugin"

        # Verify host state changed
        info = host.registry()
        assert info[0].enabled is True

    def test_disable_plugin(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        host: DefaultPluginHost,
    ) -> None:
        plugin = FakePlugin()
        host.register(plugin, config={"enabled": True})

        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.put(
            "/admin/api/plugins/fake_plugin",
            json={"enabled": False},
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False

        # Verify host state changed
        info = host.registry()
        assert info[0].enabled is False

    def test_disable_affects_dispatch(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        host: DefaultPluginHost,
    ) -> None:
        """After disabling, the plugin's handler should not be invoked."""
        plugin = FakePlugin()
        host.register(plugin, config={"enabled": True})

        # Dispatch while enabled — handler should be called
        ctx = PluginContext(hook=HookType.on_inbound_message)
        asyncio.run(host.dispatch(ctx))
        assert plugin.last_ctx is not None

        # Disable via API
        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.put(
            "/admin/api/plugins/fake_plugin",
            json={"enabled": False},
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 200

        # Reset and dispatch again — handler should NOT be called
        plugin.last_ctx = None
        ctx2 = PluginContext(hook=HookType.on_inbound_message)
        asyncio.run(host.dispatch(ctx2))
        assert plugin.last_ctx is None

    def test_update_config(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        host: DefaultPluginHost, plugin_config_repo: InMemoryPluginConfigRepository,
    ) -> None:
        plugin = FakePlugin()
        host.register(plugin, config={"enabled": True})

        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.put(
            "/admin/api/plugins/fake_plugin",
            json={"config": {"threshold": 0.8}},
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["config"] == {"threshold": 0.8}
        assert data["config_reload_strategy"] == "reload"

        # Verify persisted
        record = asyncio.run(plugin_config_repo.get("fake_plugin"))
        assert record is not None
        assert record[1] == {"threshold": 0.8}

    def test_config_hot_reload_dispatches_hook(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        host: DefaultPluginHost,
    ) -> None:
        """When plugin subscribes to on_config_change, config update dispatches the hook."""
        plugin = FakePluginWithConfigChange()
        host.register(plugin, config={"enabled": True})

        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.put(
            "/admin/api/plugins/hot_plugin",
            json={"config": {"new_key": "new_value"}},
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["config_reload_strategy"] == "hot"

        # The on_config_change hook should have been dispatched
        assert plugin.config_change_received is not None
        assert plugin.config_change_received.get("key") == "hot_plugin"

    def test_update_both_enabled_and_config(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        host: DefaultPluginHost,
    ) -> None:
        plugin = FakePlugin()
        host.register(plugin, config={"enabled": True})

        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.put(
            "/admin/api/plugins/fake_plugin",
            json={"enabled": False, "config": {"threshold": 0.3}},
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        assert data["config"] == {"threshold": 0.3}

    def test_plugin_not_found(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
    ) -> None:
        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.put(
            "/admin/api/plugins/nonexistent",
            json={"enabled": True},
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 404

    def test_readonly_user_gets_403(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        host: DefaultPluginHost,
    ) -> None:
        plugin = FakePlugin()
        host.register(plugin, config={"enabled": True})

        _create_admin(admin_repo, username="viewer", role="readonly")
        tokens = _login(client, username="viewer")
        resp = client.put(
            "/admin/api/plugins/fake_plugin",
            json={"enabled": False},
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 403

    def test_unauthenticated_rejected(
        self, client: TestClient,
    ) -> None:
        resp = client.put(
            "/admin/api/plugins/fake_plugin",
            json={"enabled": False},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Plugin audit
# ---------------------------------------------------------------------------


class TestPluginAudit:
    def test_enable_creates_audit(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        host: DefaultPluginHost, audit_repo: InMemoryAuditRepository,
    ) -> None:
        plugin = FakePlugin()
        host.register(plugin, config={"enabled": True})
        host.disable("fake_plugin")

        _create_admin(admin_repo)
        tokens = _login(client)
        client.put(
            "/admin/api/plugins/fake_plugin",
            json={"enabled": True},
            headers=_auth_headers(tokens),
        )

        audit_entries = [e for e in audit_repo._entries if e.action == "plugin.update"]
        assert len(audit_entries) >= 1
        entry = audit_entries[0]
        assert entry.actor == "admin"
        assert entry.target == "fake_plugin"
        assert entry.detail.get("enabled") is True

    def test_config_update_creates_audit(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        host: DefaultPluginHost, audit_repo: InMemoryAuditRepository,
    ) -> None:
        plugin = FakePlugin()
        host.register(plugin, config={"enabled": True})

        _create_admin(admin_repo)
        tokens = _login(client)
        client.put(
            "/admin/api/plugins/fake_plugin",
            json={"config": {"key": "val"}},
            headers=_auth_headers(tokens),
        )

        audit_entries = [e for e in audit_repo._entries if e.action == "plugin.update"]
        assert len(audit_entries) >= 1
        entry = audit_entries[0]
        assert entry.detail.get("config_updated") is True


# ---------------------------------------------------------------------------
# GET /audit
# ---------------------------------------------------------------------------


class TestQueryAudit:
    def test_empty_audit(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
    ) -> None:
        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.get("/admin/api/audit", headers=_auth_headers(tokens))
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["has_more"] is False

    def test_returns_audit_entries(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        # Pre-populate audit
        asyncio.run(audit_repo.record(
            actor="admin", action="config.update", target="batch",
            detail={"keys": ["BOT_NAME"]}, success=True,
        ))

        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.get("/admin/api/audit", headers=_auth_headers(tokens))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) >= 1
        item = data["items"][0]
        assert item["actor"] == "admin"
        assert item["action"] == "config.update"
        assert item["target"] == "batch"
        assert item["success"] is True

    def test_filter_by_actor(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        asyncio.run(audit_repo.record(actor="admin", action="test.action"))
        asyncio.run(audit_repo.record(actor="system", action="test.action"))

        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.get(
            "/admin/api/audit?actor=admin",
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert all(item["actor"] == "admin" for item in data["items"])

    def test_filter_by_action(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        asyncio.run(audit_repo.record(actor="admin", action="config.update"))
        asyncio.run(audit_repo.record(actor="admin", action="plugin.update"))

        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.get(
            "/admin/api/audit?action=plugin.update",
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert all(item["action"] == "plugin.update" for item in data["items"])

    def test_keyset_pagination(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        # Insert 5 entries
        for i in range(5):
            asyncio.run(audit_repo.record(
                actor="admin", action=f"test.action.{i}",
            ))

        _create_admin(admin_repo)
        tokens = _login(client)

        # First page: limit=2
        resp = client.get(
            "/admin/api/audit?limit=2",
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 200
        page1 = resp.json()
        assert len(page1["items"]) == 2
        assert page1["has_more"] is True

        # Second page: before_id from last item of page 1
        last_id = page1["items"][-1]["id"]
        resp2 = client.get(
            f"/admin/api/audit?limit=2&before_id={last_id}",
            headers=_auth_headers(tokens),
        )
        assert resp2.status_code == 200
        page2 = resp2.json()
        assert len(page2["items"]) == 2
        assert page2["has_more"] is True
        # All page2 ids should be less than last_id
        assert all(item["id"] < last_id for item in page2["items"])

        # Third page
        last_id2 = page2["items"][-1]["id"]
        resp3 = client.get(
            f"/admin/api/audit?limit=2&before_id={last_id2}",
            headers=_auth_headers(tokens),
        )
        assert resp3.status_code == 200
        page3 = resp3.json()
        assert len(page3["items"]) == 1
        assert page3["has_more"] is False

    def test_combined_filters(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        asyncio.run(audit_repo.record(actor="admin", action="config.update"))
        asyncio.run(audit_repo.record(actor="admin", action="plugin.update"))
        asyncio.run(audit_repo.record(actor="system", action="config.update"))

        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.get(
            "/admin/api/audit?actor=admin&action=config.update",
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["actor"] == "admin"
        assert data["items"][0]["action"] == "config.update"

    def test_admin_only_access(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
    ) -> None:
        _create_admin(admin_repo, username="viewer", role="readonly")
        tokens = _login(client, username="viewer")
        resp = client.get("/admin/api/audit", headers=_auth_headers(tokens))
        assert resp.status_code == 403

    def test_unauthenticated_rejected(
        self, client: TestClient,
    ) -> None:
        resp = client.get("/admin/api/audit")
        assert resp.status_code == 401

    def test_entry_structure(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        asyncio.run(audit_repo.record(
            actor="admin",
            action="test.action",
            target="test_target",
            detail={"key": "value"},
            ip="127.0.0.1",
            success=True,
        ))

        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.get("/admin/api/audit", headers=_auth_headers(tokens))
        assert resp.status_code == 200
        data = resp.json()
        item = data["items"][0]
        assert "id" in item
        assert item["actor"] == "admin"
        assert item["action"] == "test.action"
        assert item["target"] == "test_target"
        assert item["detail"] == {"key": "value"}
        assert item["ip"] == "127.0.0.1"
        assert item["success"] is True
        assert item["created_at"] != ""
