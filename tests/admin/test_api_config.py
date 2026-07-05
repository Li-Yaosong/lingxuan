"""Tests for admin config API: GET /config, GET /config/schema, PUT /config.

Covers: secret masking, hot-reload vs needs-restart, RBAC (403 for readonly),
type validation (422), audit logging without sensitive plaintext.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from lingxuan.admin.app import create_admin_app
from lingxuan.admin.auth import create_access_token, hash_password
from lingxuan.container import Container
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
# Fixtures
# ---------------------------------------------------------------------------

SECRET_KEY = "test-secret-key-for-jwt-signing-not-for-production"


def _make_container() -> Container:
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
    return c


@pytest.fixture
def container() -> Container:
    return _make_container()


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
def config(container: Container) -> FakeConfigProvider:
    return container.config  # type: ignore[return-value]


def _create_admin(
    repo: InMemoryAdminUserRepository,
    username: str = "admin",
    password: str = "Admin123!",
    role: str = "admin",
) -> None:
    """Create an admin user directly in the repo."""
    import asyncio

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
# GET /config — secret masking
# ---------------------------------------------------------------------------


class TestGetConfig:
    def test_secret_values_are_masked(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.get("/admin/api/config", headers=_auth_headers(tokens))
        assert resp.status_code == 200
        data = resp.json()
        # OPENAI_API_KEY is secret; default is "" which should show "(未配置)"
        assert data["OPENAI_API_KEY"] == "(未配置)"
        # SECRET_KEY is secret and overridden in test fixtures with a real value,
        # so it should be masked (first2 + **** + last2), not shown in plaintext
        assert data["SECRET_KEY"] != SECRET_KEY
        assert "****" in data["SECRET_KEY"]
        # Non-secret values should be returned as-is
        assert data["BOT_NAME"] == "灵轩"

    def test_secret_with_real_value_masked(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository, config: FakeConfigProvider
    ) -> None:
        import asyncio

        # Set a real API key
        asyncio.run(config.set("OPENAI_API_KEY", "sk-abcdef1234567890", actor="test"))

        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.get("/admin/api/config", headers=_auth_headers(tokens))
        assert resp.status_code == 200
        data = resp.json()
        # Should be masked: first2 + **** + last2 = "sk****90"
        assert data["OPENAI_API_KEY"] == "sk****90"

    def test_requires_authentication(self, client: TestClient) -> None:
        resp = client.get("/admin/api/config")
        assert resp.status_code == 401

    def test_readonly_user_can_read(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo, username="viewer", role="readonly")
        tokens = _login(client, username="viewer")
        resp = client.get("/admin/api/config", headers=_auth_headers(tokens))
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /config/schema
# ---------------------------------------------------------------------------


class TestGetConfigSchema:
    def test_returns_all_settings(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.get("/admin/api/config/schema", headers=_auth_headers(tokens))
        assert resp.status_code == 200
        data = resp.json()
        # Should have all 31+ settings
        assert len(data) >= 31
        # Check structure of first item
        item = data[0]
        assert "key" in item
        assert "type" in item
        assert "default" in item
        assert "group" in item
        assert "is_secret" in item
        assert "hot_reloadable" in item
        assert "description" in item

    def test_hot_reloadable_flags(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.get("/admin/api/config/schema", headers=_auth_headers(tokens))
        data = resp.json()
        by_key = {item["key"]: item for item in data}

        # BOT_NAME is hot_reloadable=True
        assert by_key["BOT_NAME"]["hot_reloadable"] is True
        # ADMIN_PORT is hot_reloadable=False
        assert by_key["ADMIN_PORT"]["hot_reloadable"] is False
        # SECRET_KEY is secret and not hot_reloadable
        assert by_key["SECRET_KEY"]["is_secret"] is True
        assert by_key["SECRET_KEY"]["hot_reloadable"] is False

    def test_readonly_user_can_access_schema(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo, username="viewer", role="readonly")
        tokens = _login(client, username="viewer")
        resp = client.get("/admin/api/config/schema", headers=_auth_headers(tokens))
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# PUT /config — batch update
# ---------------------------------------------------------------------------


class TestPutConfig:
    def test_hot_reloadable_update_succeeds(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository, config: FakeConfigProvider
    ) -> None:
        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.put(
            "/admin/api/config",
            json={"BOT_NAME": "测试名"},
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["key"] == "BOT_NAME"
        assert data["results"][0]["success"] is True
        assert data["results"][0]["needs_restart"] is False

        # Value should be updated immediately
        assert config.get_str("BOT_NAME") == "测试名"

    def test_non_hot_reloadable_needs_restart(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository, config: FakeConfigProvider
    ) -> None:
        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.put(
            "/admin/api/config",
            json={"ADMIN_PORT": 9090},
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"][0]["key"] == "ADMIN_PORT"
        assert data["results"][0]["success"] is True
        assert data["results"][0]["needs_restart"] is True

    def test_readonly_user_gets_403(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo, username="viewer", role="readonly")
        tokens = _login(client, username="viewer")
        resp = client.put(
            "/admin/api/config",
            json={"BOT_NAME": "hacked"},
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 403

    def test_hot_reloadable_update_echoes_value(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository, config: FakeConfigProvider
    ) -> None:
        """Non-secret values are echoed in the PUT response."""
        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.put(
            "/admin/api/config",
            json={"BOT_NAME": "测试名"},
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 200
        data = resp.json()
        result = data["results"][0]
        assert result["success"] is True
        assert result["value"] == "测试名"

    def test_secret_update_echoes_masked_value(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository, config: FakeConfigProvider
    ) -> None:
        """Secret values are echoed as masked in the PUT response."""
        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.put(
            "/admin/api/config",
            json={"OPENAI_API_KEY": "sk-abcdef1234567890"},
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 200
        data = resp.json()
        result = data["results"][0]
        assert result["success"] is True
        # The value should be masked (contains "****")
        masked = result["value"]
        assert "****" in masked
        # The plaintext should NOT appear
        assert "sk-abcdef1234567890" not in str(masked)

    def test_type_validation_failure(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        """Type validation errors return 422 (not 200 with per-item failure)."""
        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.put(
            "/admin/api/config",
            json={"MEMORY_WINDOW": "not_a_number"},
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 422
        data = resp.json()
        assert "type_errors" in data["detail"]

    def test_unknown_key_still_returns_422(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        """Unknown keys are also pre-validation errors returning 422."""
        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.put(
            "/admin/api/config",
            json={"NONEXISTENT_KEY": "value"},
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 422

    def test_batch_update_partial_failure(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository, config: FakeConfigProvider
    ) -> None:
        """When a batch contains both valid and unknown keys, 422 is returned
        (type/unknown-key errors reject the whole request)."""
        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.put(
            "/admin/api/config",
            json={"BOT_NAME": "新名字", "NONEXISTENT_KEY": "bad"},
            headers=_auth_headers(tokens),
        )
        # Unknown key triggers 422 pre-validation failure
        assert resp.status_code == 422
        # Nothing should be persisted
        assert config.get_str("BOT_NAME") == "灵轩"

    def test_batch_all_valid_succeeds(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository, config: FakeConfigProvider
    ) -> None:
        """When all items in a batch are valid, all are persisted."""
        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.put(
            "/admin/api/config",
            json={"BOT_NAME": "新名字", "MEMORY_WINDOW": 30},
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 2
        bot_result = next(r for r in data["results"] if r["key"] == "BOT_NAME")
        mem_result = next(r for r in data["results"] if r["key"] == "MEMORY_WINDOW")
        assert bot_result["success"] is True
        assert mem_result["success"] is True
        assert config.get_str("BOT_NAME") == "新名字"
        assert config.get_int("MEMORY_WINDOW") == 30

    def test_bool_coercion(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository, config: FakeConfigProvider
    ) -> None:
        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.put(
            "/admin/api/config",
            json={"ENABLE_PRIVATE_CHAT": False},
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 200
        assert resp.json()["results"][0]["success"] is True
        assert config.get_bool("ENABLE_PRIVATE_CHAT") is False

    def test_int_update(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository, config: FakeConfigProvider
    ) -> None:
        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.put(
            "/admin/api/config",
            json={"MEMORY_WINDOW": 50},
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 200
        assert resp.json()["results"][0]["success"] is True
        assert config.get_int("MEMORY_WINDOW") == 50

    def test_requires_authentication(self, client: TestClient) -> None:
        resp = client.put(
            "/admin/api/config",
            json={"BOT_NAME": "test"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


class TestConfigAudit:
    def test_audit_record_created_on_update(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository, audit_repo: InMemoryAuditRepository
    ) -> None:
        _create_admin(admin_repo)
        tokens = _login(client)
        client.put(
            "/admin/api/config",
            json={"BOT_NAME": "审计测试"},
            headers=_auth_headers(tokens),
        )

        # Check that an audit record was created
        entries = audit_repo._entries
        assert len(entries) >= 1

        # Find the batch audit record (action="config.update")
        batch_audit = [e for e in entries if e.action == "config.update"]
        assert len(batch_audit) >= 1
        audit = batch_audit[0]
        assert audit.actor == "admin"
        assert audit.target == "batch"
        assert "BOT_NAME" in audit.detail.get("keys", [])

    def test_audit_does_not_contain_secret_plaintext(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository, audit_repo: InMemoryAuditRepository
    ) -> None:
        _create_admin(admin_repo)
        tokens = _login(client)
        client.put(
            "/admin/api/config",
            json={"OPENAI_API_KEY": "sk-super-secret-key-12345"},
            headers=_auth_headers(tokens),
        )

        # Check that no audit detail contains the plaintext secret
        for entry in audit_repo._entries:
            detail_str = str(entry.detail)
            assert "sk-super-secret-key-12345" not in detail_str

        # The batch audit should list the key but mark it as secret
        batch_audit = [e for e in audit_repo._entries if e.action == "config.update"]
        assert len(batch_audit) >= 1
        audit = batch_audit[0]
        assert "OPENAI_API_KEY" in audit.detail.get("keys", [])
        assert "OPENAI_API_KEY" in audit.detail.get("secret_keys", [])

    def test_multiple_updates_audit(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository, audit_repo: InMemoryAuditRepository
    ) -> None:
        _create_admin(admin_repo)
        tokens = _login(client)
        client.put(
            "/admin/api/config",
            json={"BOT_NAME": "名字1", "MEMORY_WINDOW": 30},
            headers=_auth_headers(tokens),
        )

        # Find the batch audit record
        batch_audit = [e for e in audit_repo._entries if e.action == "config.update"]
        assert len(batch_audit) >= 1
        audit = batch_audit[0]
        assert set(audit.detail.get("keys", [])) == {"BOT_NAME", "MEMORY_WINDOW"}
