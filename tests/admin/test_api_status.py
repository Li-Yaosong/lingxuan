"""Tests for admin status API: GET /status, POST /status/llm-check.

Covers: status structure, memory stats accuracy, bot_online flag,
feature flags, LLM check success/failure paths, readonly access.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from lingxuan.admin.app import create_admin_app
from lingxuan.admin.auth import create_access_token, hash_password
from lingxuan.container import Container
from lingxuan.core.observation_state import ObservationStore
from lingxuan.core.stats import StatsService
from lingxuan.protocols.messaging import SessionId
from lingxuan.protocols.repositories import StoredMessage, UserFact, UserProfile
from tests.fakes.config import FakeConfigProvider
from tests.fakes.llm import FakeLLMProvider
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
from tests.fakes.transport import FakeTransport


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SECRET_KEY = "test-secret-key-for-jwt-signing-not-for-production"


def _make_container(
    *,
    transport_connected: bool = True,
    api_key: str = "sk-test-key",
) -> Container:
    c = Container()
    c.override(
        "config",
        FakeConfigProvider(overrides={
            "SECRET_KEY": SECRET_KEY,
            "JWT_ACCESS_TTL": 900,
            "JWT_REFRESH_TTL": 604800,
            "DATA_ROOT": "/tmp/lingxuan-test-data",
            "OPENAI_API_KEY": api_key,
        }),
    )
    c.override("log", FakeLogSink())
    c.override("transport", FakeTransport(connected=transport_connected))
    c.override("llm", FakeLLMProvider())
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
def session_repo(container: Container) -> InMemorySessionRepository:
    return container.session_repo  # type: ignore[return-value]


@pytest.fixture
def user_profile_repo(container: Container) -> InMemoryUserProfileRepository:
    return container.user_profile_repo  # type: ignore[return-value]


@pytest.fixture
def social_graph_repo(container: Container) -> InMemorySocialGraphRepository:
    return container.social_graph_repo  # type: ignore[return-value]


@pytest.fixture
def llm(container: Container) -> FakeLLMProvider:
    return container.llm  # type: ignore[return-value]


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
# GET /status — structure and content
# ---------------------------------------------------------------------------


class TestGetStatus:
    def test_returns_complete_structure(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.get("/admin/api/status", headers=_auth_headers(tokens))
        assert resp.status_code == 200
        data = resp.json()

        # Top-level keys
        assert "bot_online" in data
        assert "features" in data
        assert "model" in data
        assert "memory_stats" in data
        assert "observe_states" in data

        # memory_stats keys
        ms = data["memory_stats"]
        assert "sessions" in ms
        assert "messages" in ms
        assert "users" in ms
        assert "active_facts" in ms
        assert "edges" in ms

    def test_bot_online_reflects_transport(
        self, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        # Connected transport
        c = _make_container(transport_connected=True)
        _create_admin(c.admin_user_repo)  # type: ignore[arg-type]
        app = create_admin_app(c)
        tc = TestClient(app, raise_server_exceptions=True)
        tokens = _login(tc)
        resp = tc.get("/admin/api/status", headers=_auth_headers(tokens))
        assert resp.json()["bot_online"] is True

        # Disconnected transport
        c2 = _make_container(transport_connected=False)
        _create_admin(c2.admin_user_repo)  # type: ignore[arg-type]
        app2 = create_admin_app(c2)
        tc2 = TestClient(app2, raise_server_exceptions=True)
        tokens2 = _login(tc2)
        resp2 = tc2.get("/admin/api/status", headers=_auth_headers(tokens2))
        assert resp2.json()["bot_online"] is False

    def test_features_show_all_enable_flags(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.get("/admin/api/status", headers=_auth_headers(tokens))
        features = resp.json()["features"]

        # All ENABLE_* flags should be present and True by default
        assert features["ENABLE_PRIVATE_CHAT"] is True
        assert features["ENABLE_GROUP_CHAT"] is True
        assert features["ENABLE_GROUP_OBSERVE"] is True
        assert features["ENABLE_MEMORY_SUMMARY"] is True
        assert features["ENABLE_USER_MEMORY"] is True
        assert features["ENABLE_USER_COGNITION_REFINE"] is True
        assert features["ENABLE_STREAM_CHUNK"] is True

    def test_model_name_returned(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.get("/admin/api/status", headers=_auth_headers(tokens))
        assert resp.json()["model"] == "deepseek-chat"

    def test_memory_stats_match_repos(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        session_repo: InMemorySessionRepository,
        user_profile_repo: InMemoryUserProfileRepository,
    ) -> None:
        # Populate some data
        async def _seed() -> None:
            sid = SessionId(kind="private", peer_id=100)
            await session_repo.ensure(sid)
            await session_repo.append_message(
                sid, StoredMessage(role="user", content="hi", user_id=100)
            )
            await session_repo.append_message(
                sid, StoredMessage(role="assistant", content="hello")
            )
            await user_profile_repo.add_fact(
                100, UserFact(id="f1", content="likes cats", category="hobby")
            )

        asyncio.run(_seed())

        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.get("/admin/api/status", headers=_auth_headers(tokens))
        ms = resp.json()["memory_stats"]

        assert ms["sessions"] == 1
        assert ms["messages"] == 2
        assert ms["users"] == 1
        assert ms["active_facts"] == 1
        assert ms["edges"] == 0

    def test_empty_memory_stats(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.get("/admin/api/status", headers=_auth_headers(tokens))
        ms = resp.json()["memory_stats"]

        assert ms["sessions"] == 0
        assert ms["messages"] == 0
        assert ms["users"] == 0
        assert ms["active_facts"] == 0
        assert ms["edges"] == 0

    def test_requires_authentication(self, client: TestClient) -> None:
        resp = client.get("/admin/api/status")
        assert resp.status_code == 401

    def test_readonly_user_can_access(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo, username="viewer", role="readonly")
        tokens = _login(client, username="viewer")
        resp = client.get("/admin/api/status", headers=_auth_headers(tokens))
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /status/llm-check — LLM reachability
# ---------------------------------------------------------------------------


class TestLLMCheck:
    def test_success_when_llm_responds(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        llm: FakeLLMProvider,
    ) -> None:
        llm.set_chat_response("pong")

        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.post("/admin/api/status/llm-check", headers=_auth_headers(tokens))
        assert resp.status_code == 200
        data = resp.json()

        assert data["ok"] is True
        assert data["latency_ms"] >= 0
        assert data["error"] is None

    def test_failure_when_no_api_key(
        self, admin_repo: InMemoryAdminUserRepository,
    ) -> None:
        c = _make_container(api_key="")
        _create_admin(c.admin_user_repo)  # type: ignore[arg-type]
        app = create_admin_app(c)
        tc = TestClient(app, raise_server_exceptions=True)
        tokens = _login(tc)
        resp = tc.post("/admin/api/status/llm-check", headers=_auth_headers(tokens))

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["error"] == "API key not configured"

    def test_failure_when_llm_raises(
        self, admin_repo: InMemoryAdminUserRepository,
    ) -> None:
        """Simulate LLM failure by using a FakeLLM that raises on chat."""

        class FailingLLM(FakeLLMProvider):
            async def chat(self, messages, **kw):
                raise RuntimeError("connection refused")

        c = Container()
        c.override(
            "config",
            FakeConfigProvider(overrides={
                "SECRET_KEY": SECRET_KEY,
                "JWT_ACCESS_TTL": 900,
                "JWT_REFRESH_TTL": 604800,
                "DATA_ROOT": "/tmp/lingxuan-test-data",
                "OPENAI_API_KEY": "sk-test-key",
            }),
        )
        c.override("log", FakeLogSink())
        c.override("transport", FakeTransport(connected=True))
        c.override("llm", FailingLLM())
        c.override("session_repo", InMemorySessionRepository())
        c.override("user_profile_repo", InMemoryUserProfileRepository())
        c.override("social_graph_repo", InMemorySocialGraphRepository())
        c.override("config_repo", InMemoryConfigRepository())
        c.override("audit_repo", InMemoryAuditRepository())
        c.override("plugin_config_repo", InMemoryPluginConfigRepository())
        c.override("admin_user_repo", InMemoryAdminUserRepository())

        _create_admin(c.admin_user_repo)  # type: ignore[arg-type]
        app = create_admin_app(c)
        tc = TestClient(app, raise_server_exceptions=True)
        tokens = _login(tc)
        resp = tc.post("/admin/api/status/llm-check", headers=_auth_headers(tokens))

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "connection refused" in data["error"]

    def test_requires_authentication(self, client: TestClient) -> None:
        resp = client.post("/admin/api/status/llm-check")
        assert resp.status_code == 401

    def test_readonly_user_can_access(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo, username="viewer", role="readonly")
        tokens = _login(client, username="viewer")
        resp = client.post("/admin/api/status/llm-check", headers=_auth_headers(tokens))
        assert resp.status_code == 200
