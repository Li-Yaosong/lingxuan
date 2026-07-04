"""Tests for admin WebSocket and log REST: WS /admin/ws/logs, WS /admin/ws/status, GET /admin/api/logs.

Covers: auth (no token → close, bad token → close, valid token → ok),
log streaming, dynamic filter, status periodic push, config_changed
broadcast with secret masking, back-pressure (slow consumer), and
REST log history query with filtering.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from lingxuan.admin.app import create_admin_app
from lingxuan.admin.auth import create_access_token, hash_password
from lingxuan.adapters.logging.sink import RingBufferLogSink
from lingxuan.container import Container
from lingxuan.protocols.logging import LogRecord
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
    use_real_sink: bool = False,
    transport_connected: bool = True,
) -> Container:
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
    if use_real_sink:
        c.override("log", RingBufferLogSink(bridge_loguru=False))
    else:
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


def _make_token(config: FakeConfigProvider, *, role: str = "admin") -> str:
    """Create a standalone access token without needing a user in DB."""
    return create_access_token(config, username="ws-tester", role=role)


# ---------------------------------------------------------------------------
# REST: GET /admin/api/logs
# ---------------------------------------------------------------------------


class TestGetLogsREST:
    def test_returns_records_structure(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo)
        tokens = _login(client)
        resp = client.get("/admin/api/logs", headers=_auth_headers(tokens))
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "records" in data
        assert isinstance(data["records"], list)
        # Login itself emits a log, so total >= 1
        assert data["total"] >= 1
        rec = data["records"][0]
        assert "ts" in rec
        assert "level" in rec
        assert "logger" in rec
        assert "msg" in rec
        assert "extra" in rec

    def test_returns_log_records(
        self, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        c = _make_container(use_real_sink=True)
        _create_admin(c.admin_user_repo)  # type: ignore[arg-type]
        app = create_admin_app(c)
        tc = TestClient(app, raise_server_exceptions=True)
        tokens = _login(tc)

        # Emit some logs
        sink = c.log
        sink.emit(LogRecord(
            ts=datetime(2025, 1, 1, 12, 0, 0), level="INFO",
            logger="test", msg="hello world", extra={"k": "v"},
        ))
        sink.emit(LogRecord(
            ts=datetime(2025, 1, 1, 12, 0, 1), level="ERROR",
            logger="app", msg="something broke", extra={},
        ))

        resp = tc.get("/admin/api/logs", headers=_auth_headers(tokens))
        assert resp.status_code == 200
        data = resp.json()
        # Login also emits a log, so total >= 2
        assert data["total"] >= 2
        records = data["records"]
        # Find our emitted records by msg
        msgs = [r["msg"] for r in records]
        assert "hello world" in msgs
        assert "something broke" in msgs
        # Check structure of our record
        hello = next(r for r in records if r["msg"] == "hello world")
        assert hello["level"] == "INFO"
        assert hello["extra"] == {"k": "v"}

    def test_level_filter(
        self, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        c = _make_container(use_real_sink=True)
        _create_admin(c.admin_user_repo)  # type: ignore[arg-type]
        app = create_admin_app(c)
        tc = TestClient(app, raise_server_exceptions=True)
        tokens = _login(tc)

        sink = c.log
        sink.emit(LogRecord(
            ts=datetime(2025, 1, 1, 12, 0, 0), level="INFO",
            logger="test", msg="info msg",
        ))
        sink.emit(LogRecord(
            ts=datetime(2025, 1, 1, 12, 0, 1), level="ERROR",
            logger="test", msg="error msg",
        ))

        resp = tc.get(
            "/admin/api/logs?level=ERROR",
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 200
        data = resp.json()
        # Should only contain ERROR level records
        assert all(r["level"] == "ERROR" for r in data["records"])
        msgs = [r["msg"] for r in data["records"]]
        assert "error msg" in msgs

    def test_keyword_filter(
        self, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        c = _make_container(use_real_sink=True)
        _create_admin(c.admin_user_repo)  # type: ignore[arg-type]
        app = create_admin_app(c)
        tc = TestClient(app, raise_server_exceptions=True)
        tokens = _login(tc)

        sink = c.log
        sink.emit(LogRecord(
            ts=datetime(2025, 1, 1, 12, 0, 0), level="INFO",
            logger="test", msg="hello world",
        ))
        sink.emit(LogRecord(
            ts=datetime(2025, 1, 1, 12, 0, 1), level="INFO",
            logger="test", msg="goodbye world",
        ))

        resp = tc.get(
            "/admin/api/logs?keyword=hello",
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 200
        data = resp.json()
        msgs = [r["msg"] for r in data["records"]]
        assert "hello world" in msgs
        assert "goodbye world" not in msgs

    def test_limit_param(
        self, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        c = _make_container(use_real_sink=True)
        _create_admin(c.admin_user_repo)  # type: ignore[arg-type]
        app = create_admin_app(c)
        tc = TestClient(app, raise_server_exceptions=True)
        tokens = _login(tc)

        sink = c.log
        for i in range(10):
            sink.emit(LogRecord(
                ts=datetime(2025, 1, 1, 12, 0, i), level="INFO",
                logger="test", msg=f"msg {i}",
            ))

        resp = tc.get(
            "/admin/api/logs?limit=3",
            headers=_auth_headers(tokens),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3

    def test_requires_authentication(self, client: TestClient) -> None:
        resp = client.get("/admin/api/logs")
        assert resp.status_code == 401

    def test_readonly_user_can_access(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo, username="viewer", role="readonly")
        tokens = _login(client, username="viewer")
        resp = client.get("/admin/api/logs", headers=_auth_headers(tokens))
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# WS /admin/ws/logs — auth
# ---------------------------------------------------------------------------


class TestWSLogsAuth:
    def test_no_token_closes_connection(self, client: TestClient) -> None:
        with client.websocket_connect("/admin/ws/logs") as ws:
            # Server should close the connection due to auth failure
            with pytest.raises(Exception):
                ws.receive_json()

    def test_invalid_token_closes_connection(self, client: TestClient) -> None:
        with client.websocket_connect("/admin/ws/logs?token=invalid") as ws:
            with pytest.raises(Exception):
                ws.receive_json()

    def test_refresh_token_rejected(self, client: TestClient) -> None:
        """Refresh tokens should not be accepted for WS auth."""
        from lingxuan.admin.auth import create_refresh_token

        config = client.app.state.container.config if hasattr(client.app.state, 'container') else None
        c = _make_container()
        app = create_admin_app(c)
        tc = TestClient(app, raise_server_exceptions=True)

        refresh_token, _ = create_refresh_token(c.config, username="test", role="admin")
        with tc.websocket_connect(f"/admin/ws/logs?token={refresh_token}") as ws:
            with pytest.raises(Exception):
                ws.receive_json()

    def test_valid_token_connects(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo)
        tokens = _login(client)
        token = tokens["access_token"]
        # Should not raise — connection stays open
        with client.websocket_connect(f"/admin/ws/logs?token={token}") as ws:
            # Just verify we can stay connected briefly
            time.sleep(0.1)


# ---------------------------------------------------------------------------
# WS /admin/ws/logs — log streaming
# ---------------------------------------------------------------------------


class TestWSLogsStreaming:
    def test_receives_real_time_logs(
        self, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        c = _make_container(use_real_sink=True)
        _create_admin(c.admin_user_repo)  # type: ignore[arg-type]
        app = create_admin_app(c)
        tc = TestClient(app, raise_server_exceptions=True)
        tokens = _login(tc)
        token = tokens["access_token"]

        with tc.websocket_connect(f"/admin/ws/logs?token={token}") as ws:
            # Emit a log after connecting
            c.log.emit(LogRecord(
                ts=datetime(2025, 1, 1, 12, 0, 0), level="INFO",
                logger="test", msg="ws test log", extra={"key": "val"},
            ))
            msg = ws.receive_json(mode="text")
            assert msg["type"] == "log"
            assert msg["level"] == "INFO"
            assert msg["msg"] == "ws test log"
            assert msg["logger"] == "test"
            assert msg["extra"] == {"key": "val"}
            assert "ts" in msg

    def test_multiple_logs_in_order(
        self, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        c = _make_container(use_real_sink=True)
        _create_admin(c.admin_user_repo)  # type: ignore[arg-type]
        app = create_admin_app(c)
        tc = TestClient(app, raise_server_exceptions=True)
        tokens = _login(tc)
        token = tokens["access_token"]

        with tc.websocket_connect(f"/admin/ws/logs?token={token}") as ws:
            for i in range(3):
                c.log.emit(LogRecord(
                    ts=datetime(2025, 1, 1, 12, 0, i), level="INFO",
                    logger="test", msg=f"log {i}",
                ))

            msgs = [ws.receive_json(mode="text") for _ in range(3)]
            assert [m["msg"] for m in msgs] == ["log 0", "log 1", "log 2"]


# ---------------------------------------------------------------------------
# WS /admin/ws/logs — dynamic filter
# ---------------------------------------------------------------------------


class TestWSLogsFilter:
    def test_level_filter(
        self, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        c = _make_container(use_real_sink=True)
        _create_admin(c.admin_user_repo)  # type: ignore[arg-type]
        app = create_admin_app(c)
        tc = TestClient(app, raise_server_exceptions=True)
        tokens = _login(tc)
        token = tokens["access_token"]

        with tc.websocket_connect(f"/admin/ws/logs?token={token}") as ws:
            # Set filter to ERROR only
            ws.send_json({"type": "filter", "level": "ERROR", "keyword": ""})

            # Give filter time to apply
            time.sleep(0.05)

            # Emit INFO and ERROR logs
            c.log.emit(LogRecord(
                ts=datetime(2025, 1, 1, 12, 0, 0), level="INFO",
                logger="test", msg="info log",
            ))
            c.log.emit(LogRecord(
                ts=datetime(2025, 1, 1, 12, 0, 1), level="ERROR",
                logger="test", msg="error log",
            ))

            # Should only receive the ERROR log
            msg = ws.receive_json(mode="text")
            assert msg["level"] == "ERROR"
            assert msg["msg"] == "error log"

    def test_keyword_filter(
        self, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        c = _make_container(use_real_sink=True)
        _create_admin(c.admin_user_repo)  # type: ignore[arg-type]
        app = create_admin_app(c)
        tc = TestClient(app, raise_server_exceptions=True)
        tokens = _login(tc)
        token = tokens["access_token"]

        with tc.websocket_connect(f"/admin/ws/logs?token={token}") as ws:
            ws.send_json({"type": "filter", "level": None, "keyword": "important"})

            time.sleep(0.05)

            c.log.emit(LogRecord(
                ts=datetime(2025, 1, 1, 12, 0, 0), level="INFO",
                logger="test", msg="normal log",
            ))
            c.log.emit(LogRecord(
                ts=datetime(2025, 1, 1, 12, 0, 1), level="INFO",
                logger="test", msg="important event happened",
            ))

            msg = ws.receive_json(mode="text")
            assert "important" in msg["msg"]


# ---------------------------------------------------------------------------
# WS /admin/ws/status — auth
# ---------------------------------------------------------------------------


class TestWSStatusAuth:
    def test_no_token_closes_connection(self, client: TestClient) -> None:
        with client.websocket_connect("/admin/ws/status") as ws:
            with pytest.raises(Exception):
                ws.receive_json()

    def test_invalid_token_closes_connection(self, client: TestClient) -> None:
        with client.websocket_connect("/admin/ws/status?token=bad-token") as ws:
            with pytest.raises(Exception):
                ws.receive_json()

    def test_valid_token_connects(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo)
        tokens = _login(client)
        token = tokens["access_token"]
        with client.websocket_connect(f"/admin/ws/status?token={token}") as ws:
            # Should receive first status push within the interval
            msg = ws.receive_json(mode="text")
            assert msg["type"] == "status"


# ---------------------------------------------------------------------------
# WS /admin/ws/status — periodic push
# ---------------------------------------------------------------------------


class TestWSStatusPush:
    def test_status_push_contains_expected_fields(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo)
        tokens = _login(client)
        token = tokens["access_token"]

        with client.websocket_connect(f"/admin/ws/status?token={token}") as ws:
            msg = ws.receive_json(mode="text")
            assert msg["type"] == "status"
            assert "bot_online" in msg
            assert "features" in msg
            assert "model" in msg
            assert "memory_stats" in msg
            assert "observe_states" in msg

    def test_readonly_role_accepted(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo, username="viewer", role="readonly")
        tokens = _login(client, username="viewer")
        token = tokens["access_token"]

        with client.websocket_connect(f"/admin/ws/status?token={token}") as ws:
            msg = ws.receive_json(mode="text")
            assert msg["type"] == "status"


# ---------------------------------------------------------------------------
# WS /admin/ws/status — config_changed broadcast
# ---------------------------------------------------------------------------


class TestWSStatusConfigChange:
    def test_config_changed_broadcast_with_masking(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo)
        tokens = _login(client)
        token = tokens["access_token"]

        with client.websocket_connect(f"/admin/ws/status?token={token}") as ws:
            # Skip the initial status push
            first = ws.receive_json(mode="text")
            assert first["type"] == "status"

            # Change a non-secret config via REST
            resp = client.put(
                "/admin/api/config",
                json={"BOT_NAME": "新名字"},
                headers=_auth_headers(tokens),
            )
            assert resp.status_code == 200

            # Should receive a config_changed message
            # (might need to skip more status pushes)
            found = False
            for _ in range(10):
                msg = ws.receive_json(mode="text")
                if msg.get("type") == "config_changed" and msg.get("key") == "BOT_NAME":
                    found = True
                    assert msg["value"] == "新名字"  # not masked (not secret)
                    break
            assert found, "Did not receive config_changed for BOT_NAME"

    def test_secret_config_masked_in_broadcast(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo)
        tokens = _login(client)
        token = tokens["access_token"]

        with client.websocket_connect(f"/admin/ws/status?token={token}") as ws:
            # Skip the initial status push
            first = ws.receive_json(mode="text")
            assert first["type"] == "status"

            # Change a secret config key
            resp = client.put(
                "/admin/api/config",
                json={"OPENAI_API_KEY": "sk-new-secret-key-12345"},
                headers=_auth_headers(tokens),
            )
            assert resp.status_code == 200

            # Should receive a config_changed message with masked value
            found = False
            for _ in range(10):
                msg = ws.receive_json(mode="text")
                if msg.get("type") == "config_changed" and msg.get("key") == "OPENAI_API_KEY":
                    found = True
                    # Secret should be masked, not the raw value
                    assert msg["value"] != "sk-new-secret-key-12345"
                    assert "****" in str(msg["value"])
                    break
            assert found, "Did not receive config_changed for OPENAI_API_KEY"


# ---------------------------------------------------------------------------
# WS /admin/ws/logs — back-pressure (slow consumer)
# ---------------------------------------------------------------------------


class TestWSLogsBackPressure:
    def test_slow_consumer_does_not_block_emit(
        self, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        """Rapidly emitting many records should not block the emitter."""
        c = _make_container(use_real_sink=True)
        _create_admin(c.admin_user_repo)  # type: ignore[arg-type]
        app = create_admin_app(c)
        tc = TestClient(app, raise_server_exceptions=True)
        tokens = _login(tc)
        token = tokens["access_token"]

        with tc.websocket_connect(f"/admin/ws/logs?token={token}") as ws:
            # Emit a large burst of records quickly
            start = time.monotonic()
            for i in range(600):
                c.log.emit(LogRecord(
                    ts=datetime(2025, 1, 1, 12, 0, 0), level="INFO",
                    logger="burst", msg=f"burst {i}",
                ))
            elapsed = time.monotonic() - start

            # emit() should return quickly (not blocked by WS consumer)
            # 600 emits in well under 5s — any blocking would make this slow
            assert elapsed < 5.0, f"emit took {elapsed:.2f}s — possible back-pressure issue"

            # Consumer should receive at least some messages (queue size is 512)
            received = 0
            while received < 10:
                try:
                    msg = ws.receive_json(mode="text")
                    if msg.get("type") == "log":
                        received += 1
                except Exception:
                    break


# ---------------------------------------------------------------------------
# Disconnect cleanup — ensure unsubscribe is called
# ---------------------------------------------------------------------------


class TestWSDisconnectCleanup:
    def test_logs_unsubscribe_on_disconnect(
        self, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        c = _make_container(use_real_sink=True)
        _create_admin(c.admin_user_repo)  # type: ignore[arg-type]
        app = create_admin_app(c)
        tc = TestClient(app, raise_server_exceptions=True)
        tokens = _login(tc)
        token = tokens["access_token"]

        sink = c.log
        sub_count_before = len(sink._subscribers)

        with tc.websocket_connect(f"/admin/ws/logs?token={token}") as ws:
            time.sleep(0.05)
            # Subscriber should be registered
            assert len(sink._subscribers) > sub_count_before

        # After disconnect, subscriber should be cleaned up
        time.sleep(0.1)
        assert len(sink._subscribers) == sub_count_before

    def test_status_unsubscribe_on_disconnect(
        self, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        c = _make_container(use_real_sink=True)
        _create_admin(c.admin_user_repo)  # type: ignore[arg-type]
        app = create_admin_app(c)
        tc = TestClient(app, raise_server_exceptions=True)
        tokens = _login(tc)
        token = tokens["access_token"]

        config = c.config
        sub_count_before = len(config._subscribers)

        with tc.websocket_connect(f"/admin/ws/status?token={token}") as ws:
            # Consume initial status push to ensure connection is established
            ws.receive_json(mode="text")

        # After disconnect, config subscriber should be cleaned up
        time.sleep(0.1)
        assert len(config._subscribers) == sub_count_before
