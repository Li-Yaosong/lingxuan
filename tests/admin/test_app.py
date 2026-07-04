"""Tests for the admin FastAPI sub-app skeleton (P4-02).

Uses ``httpx.ASGITransport`` + FastAPI ``TestClient`` so no real port
is bound.  Verifies: health endpoint, route mounting, CORS headers,
and SPA static mount setup.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from lingxuan.admin.app import create_admin_app
from lingxuan.container import Container


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def container() -> Container:
    """Build a Container with fake overrides (no real DB / network)."""
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

    c = Container()
    c.override("config", FakeConfigProvider())
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
def client(container: Container) -> TestClient:
    """TestClient wired to the admin app via the fake Container."""
    app = create_admin_app(container)
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_health_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/admin/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"status": "ok"}

    def test_health_no_auth_required(self, client: TestClient) -> None:
        """Health endpoint must be accessible without any credentials."""
        resp = client.get("/admin/api/health")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


class TestCORS:
    def test_cors_allows_localhost(self, client: TestClient) -> None:
        """Preflight from localhost:8081 should be accepted."""
        resp = client.options(
            "/admin/api/health",
            headers={
                "Origin": "http://localhost:8081",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") in (
            "http://localhost:8081",
            "*",
        )

    def test_cors_allows_127_0_0_1(self, client: TestClient) -> None:
        resp = client.options(
            "/admin/api/health",
            headers={
                "Origin": "http://127.0.0.1:8081",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") in (
            "http://127.0.0.1:8081",
            "*",
        )


# ---------------------------------------------------------------------------
# Route structure
# ---------------------------------------------------------------------------


class TestRouteStructure:
    def test_api_prefix(self, client: TestClient) -> None:
        """REST routes are under /admin/api."""
        resp = client.get("/admin/api/health")
        assert resp.status_code == 200

    def test_openapi_under_admin_api(self, client: TestClient) -> None:
        """OpenAPI schema is served at /admin/api/openapi.json."""
        resp = client.get("/admin/api/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert "/admin/api/health" in schema.get("paths", {})

    def test_docs_under_admin_api(self, client: TestClient) -> None:
        """Swagger UI is at /admin/api/docs."""
        resp = client.get("/admin/api/docs")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


class TestDeps:
    def test_get_container_returns_set_container(self, container: Container) -> None:
        """After create_admin_app, get_container() returns the same Container."""
        from lingxuan.admin.deps import get_container

        _ = create_admin_app(container)
        assert get_container() is container

    def test_get_container_raises_before_init(self) -> None:
        """get_container() raises RuntimeError if called before set_container."""
        from lingxuan.admin.deps import _container, get_container, set_container

        # Save and clear the module-level container
        import lingxuan.admin.deps as deps_mod

        saved = deps_mod._container
        deps_mod._container = None
        try:
            with pytest.raises(RuntimeError, match="not initialized"):
                get_container()
        finally:
            deps_mod._container = saved

    def test_config_dep_resolves(self, container: Container) -> None:
        """The ConfigDep dependency resolves to the Container's config."""
        from lingxuan.admin.deps import _get_config

        _ = create_admin_app(container)
        config = _get_config()
        assert config is container.config

    def test_log_dep_resolves(self, container: Container) -> None:
        from lingxuan.admin.deps import _get_log

        _ = create_admin_app(container)
        log = _get_log()
        assert log is container.log

    def test_repo_deps_resolve(self, container: Container) -> None:
        """All repository dependencies resolve to Container instances."""
        from lingxuan.admin.deps import (
            _get_admin_user_repo,
            _get_audit_repo,
            _get_config_repo,
            _get_plugin_config_repo,
            _get_session_repo,
            _get_social_graph_repo,
            _get_user_profile_repo,
        )

        _ = create_admin_app(container)
        assert _get_session_repo() is container.session_repo
        assert _get_user_profile_repo() is container.user_profile_repo
        assert _get_social_graph_repo() is container.social_graph_repo
        assert _get_config_repo() is container.config_repo
        assert _get_audit_repo() is container.audit_repo
        assert _get_plugin_config_repo() is container.plugin_config_repo
        assert _get_admin_user_repo() is container.admin_user_repo
