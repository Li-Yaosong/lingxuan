"""FastAPI dependency injection wiring for the admin sub-app.

Provides ``get_container()`` and per-service/repository convenience
dependencies so route handlers can declare what they need via
``Depends()``.  Auth-related dependencies (current user, role guards)
are implemented here.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from lingxuan.admin.auth import InvalidTokenError, decode_token
from lingxuan.adapters.storage.db import Database
from lingxuan.protocols.config import ConfigProvider
from lingxuan.protocols.llm import LLMProvider
from lingxuan.protocols.logging import LogSink
from lingxuan.protocols.messaging import MessageTransport
from lingxuan.protocols.repositories import (
    AdminUserRepository,
    AuditRepository,
    ConfigRepository,
    PluginConfigRepository,
    SessionRepository,
    SocialGraphRepository,
    UserProfileRepository,
)


# ── container holder ─────────────────────────────────────────────────────

_container: Container | None = None


def set_container(container: Container) -> None:
    """Set the Container reference.  Called once during bootstrap."""
    global _container
    _container = container


def get_container() -> Container:
    """Return the Container.  Raises if not yet initialized."""
    if _container is None:
        raise RuntimeError("Admin container not initialized; call set_container() first")
    return _container


# ── convenience dependencies ─────────────────────────────────────────────
# These factory functions are called at request time by FastAPI's DI.
# The actual Container is resolved lazily via get_container().


def _get_config() -> ConfigProvider:
    return get_container().config


def _get_log() -> LogSink:
    return get_container().log


def _get_session_repo() -> SessionRepository:
    return get_container().session_repo


def _get_user_profile_repo() -> UserProfileRepository:
    return get_container().user_profile_repo


def _get_social_graph_repo() -> SocialGraphRepository:
    return get_container().social_graph_repo


def _get_config_repo() -> ConfigRepository:
    return get_container().config_repo


def _get_audit_repo() -> AuditRepository:
    return get_container().audit_repo


def _get_plugin_config_repo() -> PluginConfigRepository:
    return get_container().plugin_config_repo


def _get_admin_user_repo() -> AdminUserRepository:
    return get_container().admin_user_repo


def _get_transport() -> MessageTransport:
    return get_container().transport


def _get_llm() -> LLMProvider:
    return get_container().llm


def _get_observation_store() -> object:
    return get_container().observation_store


def _get_stats_service() -> object:
    return get_container().stats_service


def _get_db() -> Database:
    return get_container().db


# Annotated aliases — use these in route handlers
ConfigDep = Annotated[ConfigProvider, Depends(_get_config)]
LogDep = Annotated[LogSink, Depends(_get_log)]
SessionRepoDep = Annotated[SessionRepository, Depends(_get_session_repo)]
UserProfileRepoDep = Annotated[UserProfileRepository, Depends(_get_user_profile_repo)]
SocialGraphRepoDep = Annotated[SocialGraphRepository, Depends(_get_social_graph_repo)]
ConfigRepoDep = Annotated[ConfigRepository, Depends(_get_config_repo)]
AuditRepoDep = Annotated[AuditRepository, Depends(_get_audit_repo)]
PluginConfigRepoDep = Annotated[PluginConfigRepository, Depends(_get_plugin_config_repo)]
AdminUserRepoDep = Annotated[AdminUserRepository, Depends(_get_admin_user_repo)]
TransportDep = Annotated[MessageTransport, Depends(_get_transport)]
LLMDep = Annotated[LLMProvider, Depends(_get_llm)]
ObservationStoreDep = Annotated[Any, Depends(_get_observation_store)]
StatsServiceDep = Annotated[Any, Depends(_get_stats_service)]
DatabaseDep = Annotated[Database, Depends(_get_db)]


# ── auth dependencies ────────────────────────────────────────────────────

_bearer_scheme = HTTPBearer(auto_error=False)


async def _get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    config: ConfigDep,
    repo: AdminUserRepoDep,
) -> dict[str, Any]:
    """Decode the Bearer access token and return user info dict.

    Returns ``{"username", "role", "must_change_password"}``.
    Raises 401 if no token / invalid / expired.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(config, credentials.credentials)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not an access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    username: str = payload.get("sub", "")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing subject",
        )

    # Look up current user state from DB (role/must_change_password may have changed)
    row = await repo.get_by_username(username)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists",
        )

    return {
        "username": row.username,
        "role": row.role,
        "must_change_password": row.must_change_password,
    }


async def _require_user(
    user: Annotated[dict[str, Any], Depends(_get_current_user)],
) -> dict[str, Any]:
    """Return current user; if must_change_password, block with 428
    unless the request is for change-password / me / logout (those routes
    must bypass this guard by using _get_current_user directly).
    """
    if user.get("must_change_password"):
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="Password change required before accessing this resource",
        )
    return user


async def _require_admin(
    user: Annotated[dict[str, Any], Depends(_require_user)],
) -> dict[str, Any]:
    """Require role == 'admin'; 403 otherwise."""
    if user["role"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return user


async def _require_readonly_ok(
    user: Annotated[dict[str, Any], Depends(_require_user)],
) -> dict[str, Any]:
    """Any authenticated user (admin or readonly) is allowed."""
    return user


# Annotated aliases for auth guards
CurrentUser = Annotated[dict[str, Any], Depends(_get_current_user)]
RequireUser = Annotated[dict[str, Any], Depends(_require_user)]
RequireAdmin = Annotated[dict[str, Any], Depends(_require_admin)]
RequireReadonlyOk = Annotated[dict[str, Any], Depends(_require_readonly_ok)]
