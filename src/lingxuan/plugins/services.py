"""PluginServices: aggregate object exposing controlled capabilities to plugins.

Passed to ``plugin.setup(host, config, services)`` so plugins can read session
data, query user memory, access the social graph, read config, and emit logs
without depending on concrete implementations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

if TYPE_CHECKING:
    from lingxuan.protocols.config import ConfigProvider
    from lingxuan.protocols.logging import LogSink
    from lingxuan.protocols.repositories import (
        SessionRepository,
        SocialGraphRepository,
        UserMemoryService,
        UserProfileRepository,
    )


# ---------------------------------------------------------------------------
# Read-only / controlled interfaces exposed to plugins
# ---------------------------------------------------------------------------


@runtime_checkable
class ReadOnlySessionRepo(Protocol):
    """Subset of SessionRepository that plugins may use (read-only)."""

    async def load_history(
        self, session_id: Any, *, limit: int | None = None
    ) -> list[Any]: ...

    async def count_messages(self, session_id: Any) -> int: ...

    async def get_summary(self, session_id: Any) -> str: ...

    async def get_entities(self, session_id: Any) -> dict[str, int]: ...


@runtime_checkable
class ReadOnlySocialGraph(Protocol):
    """Subset of SocialGraphRepository that plugins may use (read-only)."""

    async def edges_from(self, user_id: int) -> list[Any]: ...

    async def all_names(self) -> dict[str, int]: ...

    async def resolve_name(self, name: str) -> int | None: ...


@runtime_checkable
class ReadOnlyUserProfile(Protocol):
    """Subset of UserProfileRepository that plugins may use (read-only)."""

    async def get(self, user_id: int) -> Any | None: ...

    async def list_active_facts(
        self, user_id: int, *, limit: int | None = None
    ) -> list[Any]: ...


# ---------------------------------------------------------------------------
# PluginServices
# ---------------------------------------------------------------------------


class PluginServices:
    """Aggregate services object passed to ``plugin.setup(host, config, services)``.

    Exposes read-only / controlled surfaces so plugins can query data without
    mutating core state directly.  Write operations go through the plugin hook
    system (e.g. ``on_memory_extract`` to modify candidates before persist).
    """

    def __init__(
        self,
        *,
        sessions: SessionRepository,
        user_profiles: UserProfileRepository,
        social_graph: SocialGraphRepository,
        user_memory: UserMemoryService,
        config: ConfigProvider,
        log: LogSink,
    ) -> None:
        self._sessions = sessions
        self._user_profiles = user_profiles
        self._social_graph = social_graph
        self._user_memory = user_memory
        self._config = config
        self._log = log

    @property
    def sessions(self) -> ReadOnlySessionRepo:
        """Read-only session data access."""
        return cast(ReadOnlySessionRepo, self._sessions)

    @property
    def user_profiles(self) -> ReadOnlyUserProfile:
        """Read-only user profile access."""
        return cast(ReadOnlyUserProfile, self._user_profiles)

    @property
    def social_graph(self) -> ReadOnlySocialGraph:
        """Read-only social graph access."""
        return cast(ReadOnlySocialGraph, self._social_graph)

    @property
    def user_memory(self) -> UserMemoryService:
        """User memory service (for scheduling extraction / cognition)."""
        return self._user_memory

    @property
    def config(self) -> ConfigProvider:
        """Config provider (read-only for plugins; writes go through admin)."""
        return self._config

    @property
    def log(self) -> LogSink:
        """Log sink for structured logging."""
        return self._log
