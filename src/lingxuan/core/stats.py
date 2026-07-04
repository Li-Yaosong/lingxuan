"""StatsService: aggregates counts from all repositories for the status API."""

from __future__ import annotations

from dataclasses import dataclass

from lingxuan.protocols.repositories import (
    SessionRepository,
    SocialGraphRepository,
    UserProfileRepository,
)


@dataclass(frozen=True)
class MemoryStats:
    sessions: int
    messages: int
    users: int
    active_facts: int
    edges: int


class StatsService:
    """Aggregates memory statistics from repositories.

    Injected with the three data repositories; each ``count_*`` method
    is delegated to the corresponding repo.
    """

    def __init__(
        self,
        sessions: SessionRepository,
        users: UserProfileRepository,
        graph: SocialGraphRepository,
    ) -> None:
        self._sessions = sessions
        self._users = users
        self._graph = graph

    async def memory_stats(self) -> MemoryStats:
        return MemoryStats(
            sessions=await self._sessions.count_sessions(),
            messages=await self._sessions.count_total_messages(),
            users=await self._users.count_users(),
            active_facts=await self._users.count_active_facts(),
            edges=await self._graph.count_edges(),
        )
