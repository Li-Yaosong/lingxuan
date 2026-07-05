"""Tests for admin auth: JWT, RBAC, bootstrap, rate limiting, password management.

Uses ``httpx.ASGITransport`` + FastAPI ``TestClient`` with in-memory fakes.
No real DB or network required.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from lingxuan.admin.app import create_admin_app
from lingxuan.admin.auth import (
    LoginRateLimiter,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_rate_limiter,
    get_refresh_store,
    hash_password,
    verify_password,
)
from lingxuan.container import Container
from lingxuan.protocols.repositories import AdminUserRow
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
    """Build a Container with fake overrides and a proper SECRET_KEY."""
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
    # Reset singletons between tests
    get_refresh_store()._valid.clear()
    get_rate_limiter()._failures.clear()
    get_rate_limiter()._locked_until.clear()
    app = create_admin_app(container)
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def admin_repo(container: Container) -> InMemoryAdminUserRepository:
    return container.admin_user_repo  # type: ignore[return-value]


@pytest.fixture
def config(container: Container) -> FakeConfigProvider:
    return container.config  # type: ignore[return-value]


def _create_admin(
    repo: InMemoryAdminUserRepository,
    username: str = "admin",
    password: str = "Admin123!",
    role: str = "admin",
    must_change_password: bool = False,
) -> None:
    """Helper: create an admin user directly in the repo."""
    import asyncio

    async def _do() -> None:
        await repo.create(
            username=username,
            password_hash=hash_password(password),
            role=role,
            must_change_password=must_change_password,
        )

    asyncio.run(_do())


def _login(client: TestClient, username: str = "admin", password: str = "Admin123!") -> dict:
    """Helper: login and return token dict."""
    resp = client.post("/admin/api/auth/login", json={
        "username": username,
        "password": password,
    })
    return resp


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


class TestPasswordHashing:
    def test_hash_and_verify(self) -> None:
        pw = "MySecureP@ss1"
        hashed = hash_password(pw)
        assert hashed != pw
        assert verify_password(pw, hashed)

    def test_wrong_password_fails(self) -> None:
        hashed = hash_password("correct")
        assert not verify_password("wrong", hashed)

    def test_empty_password_fails(self) -> None:
        hashed = hash_password("")
        # Empty password should still hash/verify correctly
        assert verify_password("", hashed)

    def test_hash_is_argon2(self) -> None:
        hashed = hash_password("test")
        assert hashed.startswith("$argon2")


# ---------------------------------------------------------------------------
# JWT token operations
# ---------------------------------------------------------------------------


class TestJWTTokens:
    def test_create_access_token(self, config: FakeConfigProvider) -> None:
        token = create_access_token(config, username="admin", role="admin")
        payload = decode_token(config, token)
        assert payload["sub"] == "admin"
        assert payload["role"] == "admin"
        assert payload["type"] == "access"
        assert "exp" in payload

    def test_create_refresh_token(self, config: FakeConfigProvider) -> None:
        token, jti = create_refresh_token(config, username="admin", role="admin")
        payload = decode_token(config, token)
        assert payload["sub"] == "admin"
        assert payload["role"] == "admin"
        assert payload["type"] == "refresh"
        assert payload["jti"] == jti

    def test_expired_token_rejected(self, config: FakeConfigProvider) -> None:
        from lingxuan.admin.auth import InvalidTokenError
        # Create token that expires immediately
        config._data["JWT_ACCESS_TTL"] = -1  # Negative TTL → already expired
        token = create_access_token(config, username="admin", role="admin")
        with pytest.raises(InvalidTokenError):
            decode_token(config, token)
        config._data["JWT_ACCESS_TTL"] = 900  # Reset

    def test_wrong_secret_key_rejected(self, config: FakeConfigProvider) -> None:
        from lingxuan.admin.auth import InvalidTokenError
        token = create_access_token(config, username="admin", role="admin")
        # Decode with wrong key
        bad_config = FakeConfigProvider(overrides={"SECRET_KEY": "wrong-key"})
        with pytest.raises(InvalidTokenError):
            decode_token(bad_config, token)

    def test_no_secret_key_raises(self) -> None:
        from lingxuan.admin.auth import _secret_key
        bad_config = FakeConfigProvider(overrides={"SECRET_KEY": ""})
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            _secret_key(bad_config)


# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------


class TestLogin:
    def test_login_success(self, client: TestClient, admin_repo: InMemoryAdminUserRepository) -> None:
        _create_admin(admin_repo)
        resp = _login(client)
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data

    def test_login_wrong_password(self, client: TestClient, admin_repo: InMemoryAdminUserRepository) -> None:
        _create_admin(admin_repo)
        resp = _login(client, password="wrong")
        assert resp.status_code == 401

    def test_login_nonexistent_user(self, client: TestClient) -> None:
        resp = _login(client, username="nonexistent")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Refresh flow
# ---------------------------------------------------------------------------


class TestRefresh:
    def test_refresh_success(self, client: TestClient, admin_repo: InMemoryAdminUserRepository) -> None:
        _create_admin(admin_repo)
        login_resp = _login(client)
        tokens = login_resp.json()

        resp = client.post("/admin/api/auth/refresh", json={
            "refresh_token": tokens["refresh_token"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        # New tokens should differ from old
        assert data["refresh_token"] != tokens["refresh_token"]

    def test_refresh_with_access_token_fails(self, client: TestClient, admin_repo: InMemoryAdminUserRepository) -> None:
        _create_admin(admin_repo)
        login_resp = _login(client)
        tokens = login_resp.json()

        resp = client.post("/admin/api/auth/refresh", json={
            "refresh_token": tokens["access_token"],
        })
        assert resp.status_code == 401

    def test_refresh_revoked_token_fails(self, client: TestClient, admin_repo: InMemoryAdminUserRepository) -> None:
        _create_admin(admin_repo)
        login_resp = _login(client)
        tokens = login_resp.json()

        # Logout first
        client.post("/admin/api/auth/logout", json={
            "refresh_token": tokens["refresh_token"],
        }, headers={"Authorization": f"Bearer {tokens['access_token']}"})

        # Try to refresh with the now-revoked token
        resp = client.post("/admin/api/auth/refresh", json={
            "refresh_token": tokens["refresh_token"],
        })
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Logout flow
# ---------------------------------------------------------------------------


class TestLogout:
    def test_logout_success(self, client: TestClient, admin_repo: InMemoryAdminUserRepository) -> None:
        _create_admin(admin_repo)
        login_resp = _login(client)
        tokens = login_resp.json()

        resp = client.post("/admin/api/auth/logout", json={
            "refresh_token": tokens["refresh_token"],
        }, headers={"Authorization": f"Bearer {tokens['access_token']}"})
        assert resp.status_code == 200

    def test_logout_revokes_refresh(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo)
        login_resp = _login(client)
        tokens = login_resp.json()

        # Logout
        client.post("/admin/api/auth/logout", json={
            "refresh_token": tokens["refresh_token"],
        }, headers={"Authorization": f"Bearer {tokens['access_token']}"})

        # Refresh should fail
        resp = client.post("/admin/api/auth/refresh", json={
            "refresh_token": tokens["refresh_token"],
        })
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Change password
# ---------------------------------------------------------------------------


class TestChangePassword:
    def test_change_password_success(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo)
        login_resp = _login(client)
        tokens = login_resp.json()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        resp = client.post("/admin/api/auth/change-password", json={
            "old_password": "Admin123!",
            "new_password": "NewPass456!",
        }, headers=headers)
        assert resp.status_code == 200

        # Login with new password should work
        new_login = _login(client, password="NewPass456!")
        assert new_login.status_code == 200

    def test_change_password_wrong_old(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo)
        login_resp = _login(client)
        tokens = login_resp.json()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        resp = client.post("/admin/api/auth/change-password", json={
            "old_password": "wrong_old_pass",
            "new_password": "NewPass456!",
        }, headers=headers)
        assert resp.status_code == 400

    def test_change_password_clears_must_change(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo, must_change_password=True)
        login_resp = _login(client)
        tokens = login_resp.json()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        # Before change, /me should show must_change_password=True
        me_resp = client.get("/admin/api/auth/me", headers=headers)
        assert me_resp.json()["must_change_password"] is True

        # Change password
        resp = client.post("/admin/api/auth/change-password", json={
            "old_password": "Admin123!",
            "new_password": "NewPass456!",
        }, headers=headers)
        assert resp.status_code == 200

        # After change, login again to get fresh tokens
        new_login = _login(client, password="NewPass456!")
        new_tokens = new_login.json()
        new_headers = {"Authorization": f"Bearer {new_tokens['access_token']}"}

        me_resp = client.get("/admin/api/auth/me", headers=new_headers)
        assert me_resp.json()["must_change_password"] is False


# ---------------------------------------------------------------------------
# /me endpoint
# ---------------------------------------------------------------------------


class TestMe:
    def test_me_authenticated(self, client: TestClient, admin_repo: InMemoryAdminUserRepository) -> None:
        _create_admin(admin_repo)
        login_resp = _login(client)
        tokens = login_resp.json()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        resp = client.get("/admin/api/auth/me", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "admin"
        assert data["role"] == "admin"
        assert data["must_change_password"] is False

    def test_me_unauthenticated(self, client: TestClient) -> None:
        resp = client.get("/admin/api/auth/me")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# RBAC guards
# ---------------------------------------------------------------------------


class TestRBAC:
    def test_readonly_user_can_access_me(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo, username="viewer", role="readonly")
        login_resp = _login(client, username="viewer")
        tokens = login_resp.json()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        resp = client.get("/admin/api/auth/me", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["role"] == "readonly"

    def test_readonly_user_gets_403_on_admin_only_endpoint(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        """Readonly user accessing admin-only write endpoint returns 403."""
        _create_admin(admin_repo, username="viewer", role="readonly")
        login_resp = _login(client, username="viewer")
        tokens = login_resp.json()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        # PUT /config requires admin role
        resp = client.put(
            "/admin/api/config",
            json={"BOT_NAME": "hacked"},
            headers=headers,
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# must_change_password enforcement
# ---------------------------------------------------------------------------


class TestMustChangePassword:
    def test_must_change_user_can_access_me(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo, must_change_password=True)
        login_resp = _login(client)
        tokens = login_resp.json()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        # /me and /change-password should still work
        resp = client.get("/admin/api/auth/me", headers=headers)
        assert resp.status_code == 200

        resp = client.post("/admin/api/auth/change-password", json={
            "old_password": "Admin123!",
            "new_password": "NewPass456!",
        }, headers=headers)
        assert resp.status_code == 200

    def test_must_change_user_can_logout(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo, must_change_password=True)
        login_resp = _login(client)
        tokens = login_resp.json()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        resp = client.post("/admin/api/auth/logout", json={
            "refresh_token": tokens["refresh_token"],
        }, headers=headers)
        assert resp.status_code == 200

    def test_must_change_user_blocked_from_non_auth_endpoints(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        """User with must_change_password=True is blocked (428) from non-auth endpoints."""
        _create_admin(admin_repo, must_change_password=True)
        login_resp = _login(client)
        tokens = login_resp.json()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        # GET /config should return 428 (must change password first)
        resp = client.get("/admin/api/config", headers=headers)
        assert resp.status_code == 428

        # GET /status should also return 428
        resp = client.get("/admin/api/status", headers=headers)
        assert resp.status_code == 428


# ---------------------------------------------------------------------------
# Login rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    def test_locked_after_max_failures(self) -> None:
        limiter = LoginRateLimiter(max_failures=3, window_seconds=60, lockout_seconds=60)
        username = "testuser"

        for _ in range(3):
            limiter.record_failure(username)

        assert limiter.is_locked(username)

    def test_not_locked_before_max_failures(self) -> None:
        limiter = LoginRateLimiter(max_failures=5, window_seconds=60, lockout_seconds=60)
        username = "testuser"

        for _ in range(4):
            limiter.record_failure(username)

        assert not limiter.is_locked(username)

    def test_success_clears_failures(self) -> None:
        limiter = LoginRateLimiter(max_failures=3, window_seconds=60, lockout_seconds=60)
        username = "testuser"

        limiter.record_failure(username)
        limiter.record_failure(username)
        limiter.record_success(username)

        # Should not be locked even after more failures (count reset)
        limiter.record_failure(username)
        assert not limiter.is_locked(username)

    def test_rate_limiter_returns_429(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo)

        # Use a fresh rate limiter with low threshold for this test
        from lingxuan.admin import auth as auth_mod
        test_limiter = LoginRateLimiter(max_failures=3, window_seconds=60, lockout_seconds=60)
        original = auth_mod._rate_limiter
        auth_mod._rate_limiter = test_limiter

        try:
            # Fail 3 times
            for _ in range(3):
                _login(client, password="wrong")

            # 4th attempt should get 429
            resp = _login(client, password="wrong")
            assert resp.status_code == 429
        finally:
            auth_mod._rate_limiter = original


# ---------------------------------------------------------------------------
# Bootstrap flow
# ---------------------------------------------------------------------------


class TestBootstrap:
    def test_bootstrap_info_when_no_admins(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        # No admin users exist
        assert admin_repo._users == {}

        resp = client.get("/admin/api/auth/bootstrap-info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["bootstrap_required"] is True
        assert data["bootstrap_token"] is not None

    def test_bootstrap_info_when_admins_exist(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo)

        resp = client.get("/admin/api/auth/bootstrap-info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["bootstrap_required"] is False
        assert data["bootstrap_token"] is None

    def test_bootstrap_login_creates_admin(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        # Get bootstrap token
        info_resp = client.get("/admin/api/auth/bootstrap-info")
        bootstrap_token = info_resp.json()["bootstrap_token"]

        # Use it to create first admin
        resp = client.post("/admin/api/auth/bootstrap-login", json={
            "bootstrap_token": bootstrap_token,
            "username": "firstadmin",
            "password": "FirstAdmin123!",
        })
        assert resp.status_code == 200
        tokens = resp.json()
        assert "access_token" in tokens

        # Verify the admin was created with must_change_password=True
        me_resp = client.get("/admin/api/auth/me", headers={
            "Authorization": f"Bearer {tokens['access_token']}",
        })
        assert me_resp.json()["must_change_password"] is True

    def test_bootstrap_login_wrong_token(
        self, client: TestClient
    ) -> None:
        resp = client.post("/admin/api/auth/bootstrap-login", json={
            "bootstrap_token": "invalid-token",
            "username": "firstadmin",
            "password": "FirstAdmin123!",
        })
        assert resp.status_code == 401

    def test_bootstrap_login_fails_when_admins_exist(
        self, client: TestClient, admin_repo: InMemoryAdminUserRepository
    ) -> None:
        _create_admin(admin_repo)

        resp = client.post("/admin/api/auth/bootstrap-login", json={
            "bootstrap_token": "any-token",
            "username": "secondadmin",
            "password": "SecondAdmin123!",
        })
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Refresh token revocation store
# ---------------------------------------------------------------------------


class TestRefreshTokenStore:
    def test_register_and_validate(self) -> None:
        from lingxuan.admin.auth import RefreshTokenStore
        store = RefreshTokenStore()
        store.register("jti-1")
        assert store.is_valid("jti-1")
        assert not store.is_valid("jti-2")

    def test_revoke(self) -> None:
        from lingxuan.admin.auth import RefreshTokenStore
        store = RefreshTokenStore()
        store.register("jti-1")
        store.revoke("jti-1")
        assert not store.is_valid("jti-1")


# ---------------------------------------------------------------------------
# SECRET_KEY missing → auth rejection
# ---------------------------------------------------------------------------


class TestMissingSecretKey:
    def test_auth_endpoints_fail_without_secret_key(self) -> None:
        """When SECRET_KEY is empty, auth endpoints should fail."""
        c = Container()
        c.override("config", FakeConfigProvider(overrides={"SECRET_KEY": ""}))
        c.override("log", FakeLogSink())
        c.override("session_repo", InMemorySessionRepository())
        c.override("user_profile_repo", InMemoryUserProfileRepository())
        c.override("social_graph_repo", InMemorySocialGraphRepository())
        c.override("config_repo", InMemoryConfigRepository())
        c.override("audit_repo", InMemoryAuditRepository())
        c.override("plugin_config_repo", InMemoryPluginConfigRepository())
        c.override("admin_user_repo", InMemoryAdminUserRepository())

        app = create_admin_app(c)
        # Don't raise server exceptions — we want to observe the 500 response
        client = TestClient(app, raise_server_exceptions=False)

        # Create admin directly (no JWT needed for creation)
        _create_admin(c.admin_user_repo, username="admin")
        resp = client.post("/admin/api/auth/login", json={
            "username": "admin",
            "password": "Admin123!",
        })
        # SECRET_KEY missing → RuntimeError in JWT creation → 500
        assert resp.status_code == 500

    def test_health_still_works_without_secret_key(self) -> None:
        c = Container()
        c.override("config", FakeConfigProvider(overrides={"SECRET_KEY": ""}))
        c.override("log", FakeLogSink())
        c.override("session_repo", InMemorySessionRepository())
        c.override("user_profile_repo", InMemoryUserProfileRepository())
        c.override("social_graph_repo", InMemorySocialGraphRepository())
        c.override("config_repo", InMemoryConfigRepository())
        c.override("audit_repo", InMemoryAuditRepository())
        c.override("plugin_config_repo", InMemoryPluginConfigRepository())
        c.override("admin_user_repo", InMemoryAdminUserRepository())

        app = create_admin_app(c)
        client = TestClient(app, raise_server_exceptions=True)

        resp = client.get("/admin/api/health")
        assert resp.status_code == 200
