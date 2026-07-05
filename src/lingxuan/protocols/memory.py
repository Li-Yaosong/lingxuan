"""Memory service protocol interfaces.

Defines the surfaces needed by DialogueService and ObservationService.
Phase 2 (P2-10): methods are now async since the implementations
(SqlSessionRepository-backed) perform DB IO.
"""

from __future__ import annotations

from typing import Protocol

from lingxuan.protocols.messaging import SessionId
from lingxuan.protocols.repositories import StoredMessage


class MemoryService(Protocol):
    """Session memory service protocol.

    Minimal surface needed by core services.  The actual implementation
    lives in ``core/memory.py`` backed by SessionRepository.
    """

    async def append_message(
        self, session_id: SessionId, msg: StoredMessage
    ) -> None: ...

    async def update_meta(
        self,
        session_id: SessionId,
        *,
        nickname: str | None = None,
        group_id: int | None = None,
    ) -> None: ...

    def schedule_summarize(self, session_id: SessionId) -> None: ...


class UserMemoryService(Protocol):
    """User memory service protocol.

    Phase 2: methods are async since the DB-backed implementation
    performs IO.  ``schedule_*`` methods are fire-and-forget (they
    create internal asyncio tasks) but are still async to allow
    the initial setup to complete before returning.
    """

    async def on_user_message(
        self,
        user_id: int,
        text: str,
        *,
        nickname: str = "",
        is_private: bool = False,
        session_id: SessionId | None = None,
    ) -> None: ...

    async def schedule_cognition_refine(
        self,
        user_id: int,
        *,
        recent_exchange: str = "",
    ) -> None: ...

    async def schedule_memory_extract(
        self,
        user_id: int,
        text: str,
        *,
        nickname: str = "",
        group_id: int | None = None,
        context_lines: list[str] | None = None,
    ) -> None: ...

    async def merge_entity(
        self, session_id: object, name: str, user_id: int
    ) -> None: ...

    async def sync_entity_to_graph(
        self, name: str, user_id: int, session_id: str = ""
    ) -> None: ...

    async def index_name(self, name: str, user_id: int) -> None: ...

    async def apply_rule_extraction(
        self,
        user_id: int,
        text: str,
        *,
        nickname: str = "",
        group_id: int | None = None,
        at_user_ids: list[int] | None = None,
        session_id: str = "",
    ) -> bool: ...
