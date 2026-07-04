"""FastAPI dependency injection wiring for the admin sub-app.

Provides ``get_container()`` and per-service/repository convenience
dependencies so route handlers can declare what they need via
``Depends()``.  Auth-related dependencies (current user) will be
filled in P4-03.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from lingxuan.protocols.config import ConfigProvider
from lingxuan.protocols.logging import LogSink
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
