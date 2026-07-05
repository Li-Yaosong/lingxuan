"""Tests for admin data management API: sessions, users, social-graph, export/import.

Covers: list pagination, single query, delete semantics, export/import,
permission 403, and audit records.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from lingxuan.admin.app import create_admin_app
from lingxuan.admin.auth import hash_password
from lingxuan.container import Container
from lingxuan.protocols.messaging import SessionId
from lingxuan.protocols.repositories import (
    SocialEdge,
    StoredMessage,
    UserFact,
    UserProfile,
)
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
from tests.fakes.transport import FakeTransport


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
    c.override("transport", FakeTransport(connected=True))
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
def session_repo(container: Container) -> InMemorySessionRepository:
    return container.session_repo  # type: ignore[return-value]


@pytest.fixture
def user_profile_repo(container: Container) -> InMemoryUserProfileRepository:
    return container.user_profile_repo  # type: ignore[return-value]


@pytest.fixture
def social_graph_repo(container: Container) -> InMemorySocialGraphRepository:
    return container.social_graph_repo  # type: ignore[return-value]


@pytest.fixture
def audit_repo(container: Container) -> InMemoryAuditRepository:
    return container.audit_repo  # type: ignore[return-value]


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


def _create_readonly(
    repo: InMemoryAdminUserRepository,
    username: str = "viewer",
    password: str = "Viewer123!",
) -> None:
    async def _do() -> None:
        await repo.create(
            username=username,
            password_hash=hash_password(password),
            role="readonly",
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


def _seed_session(
    repo: InMemorySessionRepository,
    session_id: str,
    kind: str = "private",
    messages: int = 3,
) -> None:
    """Seed a session with some messages."""
    async def _do() -> None:
        sid = SessionId.parse(session_id)
        await repo.ensure(sid)
        await repo.update_meta(sid, nickname="测试用户", last_active_at=datetime.now(timezone.utc))
        for i in range(messages):
            await repo.append_message(sid, StoredMessage(
                role="user" if i % 2 == 0 else "assistant",
                content=f"消息 {i}",
                user_id=12345 if i % 2 == 0 else None,
            ))
        await repo.set_summary(sid, f"摘要-{session_id}")
    asyncio.run(_do())


def _seed_user(
    repo: InMemoryUserProfileRepository,
    user_id: int = 12345,
    preferred_name: str = "小明",
    stage: str = "familiar",
    fact_count: int = 2,
) -> None:
    """Seed a user profile with some facts."""
    async def _do() -> None:
        profile = UserProfile(
            user_id=user_id,
            preferred_name=preferred_name,
            stage=stage,
            interaction_count=10,
            seen_in_private=True,
            seen_in_group=True,
            last_seen_at=datetime.now(timezone.utc),
        )
        await repo.upsert(profile)
        for i in range(fact_count):
            fact = UserFact(
                id=f"fact-{user_id}-{i}",
                content=f"事实 {i} for user {user_id}",
                category="general",
                source_user_id=user_id,
                confidence=0.9,
                active=True,
            )
            await repo.add_fact(user_id, fact)
    asyncio.run(_do())


def _seed_edge(
    repo: InMemorySocialGraphRepository,
    from_id: int = 12345,
    to_id: int = 67890,
    relation: str = "friend_of",
    label: str = "朋友",
) -> None:
    """Seed a social edge + name index."""
    async def _do() -> None:
        edge = SocialEdge(
            from_user_id=from_id,
            to_user_id=to_id,
            relation=relation,
            label=label,
            group_id=111,
        )
        await repo.add_edge(edge)
        await repo.index_name(f"user{from_id}", from_id)
    asyncio.run(_do())


# ===========================================================================
# GET /data/sessions
# ===========================================================================


class TestListSessions:
    def test_empty_list(self, client: TestClient, admin_repo: InMemoryAdminUserRepository) -> None:
        _create_admin(admin_repo)
        headers = _auth_headers(_login(client))
        resp = client.get("/admin/api/data/sessions", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["has_more"] is False

    def test_returns_sessions_with_message_count(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        session_repo: InMemorySessionRepository,
    ) -> None:
        _create_admin(admin_repo)
        _seed_session(session_repo, "private_1001", messages=5)
        _seed_session(session_repo, "group_2001", messages=3)

        headers = _auth_headers(_login(client))
        resp = client.get("/admin/api/data/sessions", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        # Find each session
        by_id = {item["id"]: item for item in data["items"]}
        assert by_id["private_1001"]["message_count"] == 5
        assert by_id["group_2001"]["message_count"] == 3
        assert by_id["private_1001"]["kind"] == "private"
        assert by_id["group_2001"]["kind"] == "group"

    def test_keyset_pagination(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        session_repo: InMemorySessionRepository,
    ) -> None:
        _create_admin(admin_repo)
        # Create sessions with sequential IDs: private_1000..private_1004
        for i in range(5):
            _seed_session(session_repo, f"private_{1000 + i}", messages=1)

        headers = _auth_headers(_login(client))
        # First page: limit=2 — gets the first 2 by session_id (ascending)
        resp = client.get("/admin/api/data/sessions?limit=2", headers=headers)
        assert resp.status_code == 200
        page1 = resp.json()
        assert len(page1["items"]) == 2
        assert page1["has_more"] is True
        # First page should have private_1000 and private_1001
        assert page1["items"][0]["id"] == "private_1000"
        assert page1["items"][1]["id"] == "private_1001"

        # Second page: before_id=last item of page 1
        last_id = page1["items"][-1]["id"]
        resp2 = client.get(f"/admin/api/data/sessions?limit=2&before_id={last_id}", headers=headers)
        assert resp2.status_code == 200
        page2 = resp2.json()
        # No sessions have id < "private_1000" — before_id="private_1001" gives "private_1000"
        assert len(page2["items"]) == 1
        assert page2["items"][0]["id"] == "private_1000"
        assert page2["has_more"] is False


# ===========================================================================
# GET /data/sessions/{id}/messages
# ===========================================================================


class TestListMessages:
    def test_session_not_found(self, client: TestClient, admin_repo: InMemoryAdminUserRepository) -> None:
        _create_admin(admin_repo)
        headers = _auth_headers(_login(client))
        resp = client.get("/admin/api/data/sessions/private_9999/messages", headers=headers)
        assert resp.status_code == 404

    def test_returns_messages(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        session_repo: InMemorySessionRepository,
    ) -> None:
        _create_admin(admin_repo)
        _seed_session(session_repo, "private_1001", messages=5)

        headers = _auth_headers(_login(client))
        resp = client.get("/admin/api/data/sessions/private_1001/messages", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 5
        assert data["has_more"] is False
        # Messages should be ordered by seq ascending
        seqs = [m["seq"] for m in data["items"]]
        assert seqs == sorted(seqs)

    def test_keyset_pagination(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        session_repo: InMemorySessionRepository,
    ) -> None:
        _create_admin(admin_repo)
        _seed_session(session_repo, "private_1001", messages=10)

        headers = _auth_headers(_login(client))
        # First page
        resp = client.get("/admin/api/data/sessions/private_1001/messages?limit=3", headers=headers)
        page1 = resp.json()
        assert len(page1["items"]) == 3
        assert page1["has_more"] is True

        # Second page: before_seq
        last_seq = page1["items"][-1]["seq"]
        resp2 = client.get(
            f"/admin/api/data/sessions/private_1001/messages?limit=3&before_seq={last_seq}",
            headers=headers,
        )
        page2 = resp2.json()
        assert len(page2["items"]) == 3
        assert page2["has_more"] is True


# ===========================================================================
# GET /data/sessions/{id}/summary
# ===========================================================================


class TestGetSummary:
    def test_not_found(self, client: TestClient, admin_repo: InMemoryAdminUserRepository) -> None:
        _create_admin(admin_repo)
        headers = _auth_headers(_login(client))
        resp = client.get("/admin/api/data/sessions/group_9999/summary", headers=headers)
        assert resp.status_code == 404

    def test_returns_summary(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        session_repo: InMemorySessionRepository,
    ) -> None:
        _create_admin(admin_repo)
        _seed_session(session_repo, "private_1001")

        headers = _auth_headers(_login(client))
        resp = client.get("/admin/api/data/sessions/private_1001/summary", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "private_1001"
        assert data["kind"] == "private"
        assert "摘要" in data["summary"]


# ===========================================================================
# DELETE /data/sessions/{id}
# ===========================================================================


class TestDeleteSession:
    def test_not_found(self, client: TestClient, admin_repo: InMemoryAdminUserRepository) -> None:
        _create_admin(admin_repo)
        headers = _auth_headers(_login(client))
        resp = client.delete("/admin/api/data/sessions/private_9999", headers=headers)
        assert resp.status_code == 404

    def test_deletes_session(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        session_repo: InMemorySessionRepository,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        _create_admin(admin_repo)
        _seed_session(session_repo, "private_1001")

        headers = _auth_headers(_login(client))
        resp = client.delete("/admin/api/data/sessions/private_1001", headers=headers)
        assert resp.status_code == 204

        # Verify session gone
        session = asyncio.run(session_repo.get(SessionId.parse("private_1001")))
        assert session is None

        # Verify audit
        entries = asyncio.run(audit_repo.query(action="data.delete_session"))
        assert len(entries) == 1
        assert entries[0].target == "private_1001"

    def test_readonly_cannot_delete(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        session_repo: InMemorySessionRepository,
    ) -> None:
        _create_admin(admin_repo)
        _create_readonly(admin_repo)
        _seed_session(session_repo, "private_1001")

        tokens = _login(client, username="viewer", password="Viewer123!")
        headers = _auth_headers(tokens)
        resp = client.delete("/admin/api/data/sessions/private_1001", headers=headers)
        assert resp.status_code == 403


# ===========================================================================
# GET /data/users
# ===========================================================================


class TestListUsers:
    def test_empty(self, client: TestClient, admin_repo: InMemoryAdminUserRepository) -> None:
        _create_admin(admin_repo)
        headers = _auth_headers(_login(client))
        resp = client.get("/admin/api/data/users", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["has_more"] is False

    def test_returns_users(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        user_profile_repo: InMemoryUserProfileRepository,
    ) -> None:
        _create_admin(admin_repo)
        _seed_user(user_profile_repo, 1001, "用户A")
        _seed_user(user_profile_repo, 1002, "用户B")

        headers = _auth_headers(_login(client))
        resp = client.get("/admin/api/data/users", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        by_uid = {item["user_id"]: item for item in data["items"]}
        assert by_uid[1001]["preferred_name"] == "用户A"
        assert by_uid[1002]["preferred_name"] == "用户B"

    def test_keyset_pagination(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        user_profile_repo: InMemoryUserProfileRepository,
    ) -> None:
        _create_admin(admin_repo)
        for i in range(5):
            _seed_user(user_profile_repo, 1000 + i, f"U{i}")

        headers = _auth_headers(_login(client))
        # First page (ascending by user_id)
        resp = client.get("/admin/api/data/users?limit=2", headers=headers)
        page1 = resp.json()
        assert len(page1["items"]) == 2
        assert page1["has_more"] is True

        # Next page: before_user_id
        before = page1["items"][-1]["user_id"]
        resp2 = client.get(f"/admin/api/data/users?limit=2&before_user_id={before}", headers=headers)
        page2 = resp2.json()
        # Only 1 user with id < 1001
        assert len(page2["items"]) == 1
        assert page2["has_more"] is False


# ===========================================================================
# GET /data/users/{uid}
# ===========================================================================


class TestGetUser:
    def test_not_found(self, client: TestClient, admin_repo: InMemoryAdminUserRepository) -> None:
        _create_admin(admin_repo)
        headers = _auth_headers(_login(client))
        resp = client.get("/admin/api/data/users/9999", headers=headers)
        assert resp.status_code == 404

    def test_returns_profile_with_active_facts(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        user_profile_repo: InMemoryUserProfileRepository,
    ) -> None:
        _create_admin(admin_repo)
        _seed_user(user_profile_repo, 1001, "小明", fact_count=3)

        headers = _auth_headers(_login(client))
        resp = client.get("/admin/api/data/users/1001", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == 1001
        assert data["preferred_name"] == "小明"
        assert data["stage"] == "familiar"
        # Only active facts
        assert len(data["facts"]) == 3
        for f in data["facts"]:
            assert f["active"] is True


# ===========================================================================
# DELETE /data/users/{uid}
# ===========================================================================


class TestDeleteUser:
    def test_not_found(self, client: TestClient, admin_repo: InMemoryAdminUserRepository) -> None:
        _create_admin(admin_repo)
        headers = _auth_headers(_login(client))
        resp = client.delete("/admin/api/data/users/9999", headers=headers)
        assert resp.status_code == 404

    def test_deletes_user(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        user_profile_repo: InMemoryUserProfileRepository,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        _create_admin(admin_repo)
        _seed_user(user_profile_repo, 1001)

        headers = _auth_headers(_login(client))
        resp = client.delete("/admin/api/data/users/1001", headers=headers)
        assert resp.status_code == 204

        # Verify user gone
        profile = asyncio.run(user_profile_repo.get(1001))
        assert profile is None

        # Verify audit
        entries = asyncio.run(audit_repo.query(action="data.delete_user"))
        assert len(entries) == 1
        assert entries[0].target == "1001"

    def test_readonly_cannot_delete(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        user_profile_repo: InMemoryUserProfileRepository,
    ) -> None:
        _create_admin(admin_repo)
        _create_readonly(admin_repo)
        _seed_user(user_profile_repo, 1001)

        tokens = _login(client, username="viewer", password="Viewer123!")
        headers = _auth_headers(tokens)
        resp = client.delete("/admin/api/data/users/1001", headers=headers)
        assert resp.status_code == 403


# ===========================================================================
# DELETE /data/users — clear all
# ===========================================================================


class TestDeleteAllUsers:
    def test_clears_all_users_and_graph(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        user_profile_repo: InMemoryUserProfileRepository,
        social_graph_repo: InMemorySocialGraphRepository,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        _create_admin(admin_repo)
        _seed_user(user_profile_repo, 1001)
        _seed_user(user_profile_repo, 1002)
        _seed_edge(social_graph_repo)

        headers = _auth_headers(_login(client))
        resp = client.delete("/admin/api/data/users", headers=headers)
        assert resp.status_code == 204

        # All users deleted
        ids = asyncio.run(user_profile_repo.list_user_ids())
        assert ids == []

        # Graph cleared
        count = asyncio.run(social_graph_repo.count_edges())
        assert count == 0

        # Audit
        entries = asyncio.run(audit_repo.query(action="data.delete_all_users"))
        assert len(entries) == 1
        assert entries[0].detail["deleted_count"] == 2

    def test_readonly_cannot_delete_all(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        user_profile_repo: InMemoryUserProfileRepository,
    ) -> None:
        _create_admin(admin_repo)
        _create_readonly(admin_repo)
        _seed_user(user_profile_repo, 1001)

        tokens = _login(client, username="viewer", password="Viewer123!")
        headers = _auth_headers(tokens)
        resp = client.delete("/admin/api/data/users", headers=headers)
        assert resp.status_code == 403


# ===========================================================================
# GET /data/social-graph
# ===========================================================================


class TestGetSocialGraph:
    def test_empty(self, client: TestClient, admin_repo: InMemoryAdminUserRepository) -> None:
        _create_admin(admin_repo)
        headers = _auth_headers(_login(client))
        resp = client.get("/admin/api/data/social-graph", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["edges"] == []
        assert data["name_index"] == {}

    def test_returns_edges_and_names(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        social_graph_repo: InMemorySocialGraphRepository,
    ) -> None:
        _create_admin(admin_repo)
        _seed_edge(social_graph_repo, 1001, 1002, "friend_of", "朋友")
        _seed_edge(social_graph_repo, 1001, 1003, "also_known_as", "小明")

        headers = _auth_headers(_login(client))
        resp = client.get("/admin/api/data/social-graph", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["edges"]) == 2
        assert "user1001" in data["name_index"]


# ===========================================================================
# DELETE /data/social-graph
# ===========================================================================


class TestDeleteSocialGraph:
    def test_clears_graph(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        social_graph_repo: InMemorySocialGraphRepository,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        _create_admin(admin_repo)
        _seed_edge(social_graph_repo)

        headers = _auth_headers(_login(client))
        resp = client.delete("/admin/api/data/social-graph", headers=headers)
        assert resp.status_code == 204

        count = asyncio.run(social_graph_repo.count_edges())
        assert count == 0

        # Audit
        entries = asyncio.run(audit_repo.query(action="data.delete_social_graph"))
        assert len(entries) == 1

    def test_readonly_cannot_delete(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        social_graph_repo: InMemorySocialGraphRepository,
    ) -> None:
        _create_admin(admin_repo)
        _create_readonly(admin_repo)
        _seed_edge(social_graph_repo)

        tokens = _login(client, username="viewer", password="Viewer123!")
        headers = _auth_headers(tokens)
        resp = client.delete("/admin/api/data/social-graph", headers=headers)
        assert resp.status_code == 403


# ===========================================================================
# GET /data/export
# ===========================================================================


class TestExport:
    def test_export_empty(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        _create_admin(admin_repo)
        headers = _auth_headers(_login(client))
        resp = client.get("/admin/api/data/export", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["sessions"] == []
        assert data["messages"] == []
        assert data["user_profiles"] == []
        assert data["user_facts"] == []
        assert data["social_edges"] == []
        assert data["name_index"] == {}

        # Audit
        entries = asyncio.run(audit_repo.query(action="data.export"))
        assert len(entries) == 1

    def test_export_with_data(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        session_repo: InMemorySessionRepository,
        user_profile_repo: InMemoryUserProfileRepository,
        social_graph_repo: InMemorySocialGraphRepository,
    ) -> None:
        _create_admin(admin_repo)
        _seed_session(session_repo, "private_1001", messages=3)
        _seed_user(user_profile_repo, 1001, "小明", fact_count=2)
        _seed_edge(social_graph_repo, 1001, 1002)

        headers = _auth_headers(_login(client))
        resp = client.get("/admin/api/data/export", headers=headers)
        assert resp.status_code == 200
        data = resp.json()

        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["session_id"] == "private_1001"
        assert len(data["messages"]) == 3
        assert len(data["user_profiles"]) == 1
        assert len(data["user_facts"]) == 2
        assert len(data["social_edges"]) == 1

    def test_readonly_cannot_export(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
    ) -> None:
        _create_admin(admin_repo)
        _create_readonly(admin_repo)

        tokens = _login(client, username="viewer", password="Viewer123!")
        headers = _auth_headers(tokens)
        resp = client.get("/admin/api/data/export", headers=headers)
        assert resp.status_code == 403


# ===========================================================================
# POST /data/import
# ===========================================================================


class TestImport:
    def test_requires_confirm(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
    ) -> None:
        _create_admin(admin_repo)
        headers = _auth_headers(_login(client))

        payload = {
            "confirm": False,
            "data": {
                "sessions": [],
                "messages": [],
                "entities": [],
                "user_profiles": [],
                "user_facts": [],
                "social_edges": [],
                "name_index": {},
                "settings": {},
                "plugin_configs": {},
            },
        }
        resp = client.post("/admin/api/data/import", json=payload, headers=headers)
        assert resp.status_code == 400
        assert "confirm" in resp.json()["detail"].lower()

    def test_import_sessions(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        session_repo: InMemorySessionRepository,
    ) -> None:
        _create_admin(admin_repo)
        headers = _auth_headers(_login(client))

        payload = {
            "confirm": True,
            "data": {
                "sessions": [
                    {"session_id": "private_1001", "kind": "private", "nickname": "用户1", "summary": "摘要1"},
                ],
                "messages": [
                    {"session_id": "private_1001", "seq": 0, "role": "user", "content": "你好", "user_id": 1001},
                    {"session_id": "private_1001", "seq": 1, "role": "assistant", "content": "你好呀"},
                ],
                "entities": [
                    {"session_id": "private_1001", "name": "用户1", "user_id": 1001},
                ],
                "user_profiles": [],
                "user_facts": [],
                "social_edges": [],
                "name_index": {},
                "settings": {},
                "plugin_configs": {},
            },
        }
        resp = client.post("/admin/api/data/import", json=payload, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["imported"]["sessions"] == 1
        assert data["imported"]["messages"] == 2
        assert data["imported"]["entities"] == 1

        # Verify session exists
        session = asyncio.run(session_repo.get(SessionId.parse("private_1001")))
        assert session is not None

    def test_import_users_and_facts(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        user_profile_repo: InMemoryUserProfileRepository,
    ) -> None:
        _create_admin(admin_repo)
        headers = _auth_headers(_login(client))

        payload = {
            "confirm": True,
            "data": {
                "sessions": [],
                "messages": [],
                "entities": [],
                "user_profiles": [
                    {"user_id": 1001, "preferred_name": "小明", "stage": "familiar", "interaction_count": 5},
                ],
                "user_facts": [
                    {"id": "f1", "user_id": 1001, "content": "喜欢猫", "category": "hobby"},
                ],
                "social_edges": [
                    {"from_user_id": 1001, "to_user_id": 1002, "relation": "friend_of", "label": "朋友"},
                ],
                "name_index": {"小明": 1001},
                "settings": {},
                "plugin_configs": {},
            },
        }
        resp = client.post("/admin/api/data/import", json=payload, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"]["user_profiles"] == 1
        assert data["imported"]["user_facts"] == 1
        assert data["imported"]["social_edges"] == 1
        assert data["imported"]["name_index"] == 1

        # Verify user exists
        profile = asyncio.run(user_profile_repo.get(1001))
        assert profile is not None
        assert profile.preferred_name == "小明"

    def test_import_audits(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        _create_admin(admin_repo)
        headers = _auth_headers(_login(client))

        payload = {
            "confirm": True,
            "data": {
                "sessions": [],
                "messages": [],
                "entities": [],
                "user_profiles": [],
                "user_facts": [],
                "social_edges": [],
                "name_index": {},
                "settings": {},
                "plugin_configs": {},
            },
        }
        resp = client.post("/admin/api/data/import", json=payload, headers=headers)
        assert resp.status_code == 200

        # Verify audit
        entries = asyncio.run(audit_repo.query(action="data.import"))
        assert len(entries) == 1

    def test_readonly_cannot_import(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
    ) -> None:
        _create_admin(admin_repo)
        _create_readonly(admin_repo)

        tokens = _login(client, username="viewer", password="Viewer123!")
        headers = _auth_headers(tokens)

        payload = {
            "confirm": True,
            "data": {
                "sessions": [],
                "messages": [],
                "entities": [],
                "user_profiles": [],
                "user_facts": [],
                "social_edges": [],
                "name_index": {},
                "settings": {},
                "plugin_configs": {},
            },
        }
        resp = client.post("/admin/api/data/import", json=payload, headers=headers)
        assert resp.status_code == 403


# ===========================================================================
# Export → Import roundtrip
# ===========================================================================


class TestExportImportRoundtrip:
    def test_roundtrip_preserves_data(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
        session_repo: InMemorySessionRepository,
        user_profile_repo: InMemoryUserProfileRepository,
        social_graph_repo: InMemorySocialGraphRepository,
    ) -> None:
        _create_admin(admin_repo)
        # Seed data
        _seed_session(session_repo, "private_1001", messages=3)
        _seed_user(user_profile_repo, 1001, "小明", fact_count=2)
        _seed_edge(social_graph_repo, 1001, 1002)

        headers = _auth_headers(_login(client))

        # Export
        export_resp = client.get("/admin/api/data/export", headers=headers)
        assert export_resp.status_code == 200
        export_data = export_resp.json()

        # Clear everything
        asyncio.run(session_repo.clear(SessionId.parse("private_1001")))
        asyncio.run(user_profile_repo.delete(1001))
        asyncio.run(social_graph_repo.clear())

        # Verify cleared
        assert asyncio.run(session_repo.get(SessionId.parse("private_1001"))) is None

        # Import
        import_payload = {"confirm": True, "data": export_data}
        import_resp = client.post("/admin/api/data/import", json=import_payload, headers=headers)
        assert import_resp.status_code == 200

        # Verify restored
        session = asyncio.run(session_repo.get(SessionId.parse("private_1001")))
        assert session is not None

        profile = asyncio.run(user_profile_repo.get(1001))
        assert profile is not None
        assert profile.preferred_name == "小明"


# ===========================================================================
# Permission: unauthenticated
# ===========================================================================


class TestUnauthenticated:
    def test_all_endpoints_require_auth(self, client: TestClient) -> None:
        """All data endpoints require authentication."""
        endpoints = [
            ("GET", "/admin/api/data/sessions"),
            ("GET", "/admin/api/data/users"),
            ("GET", "/admin/api/data/social-graph"),
            ("GET", "/admin/api/data/export"),
            ("POST", "/admin/api/data/import"),
        ]
        for method, url in endpoints:
            if method == "GET":
                resp = client.get(url)
            else:
                resp = client.post(url, json={})
            assert resp.status_code == 401, f"{method} {url} should return 401"


# ===========================================================================
# Invalid session_id format
# ===========================================================================


class TestInvalidSessionId:
    def test_invalid_format_returns_400(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository,
    ) -> None:
        _create_admin(admin_repo)
        headers = _auth_headers(_login(client))
        resp = client.get("/admin/api/data/sessions/invalid_format/messages", headers=headers)
        assert resp.status_code == 400
