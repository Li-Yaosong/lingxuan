"""Phase 1 memory service protocol stubs.

Minimal surfaces needed by DialogueService and ObservationService.
The actual implementations wrap the old MVP modules until Phase 2
migrates to Repository-based implementations.
"""

from __future__ import annotations

from typing import Protocol

from lingxuan.protocols.messaging import SessionId
from lingxuan.protocols.repositories import StoredMessage


class MemoryService(Protocol):
    """Session memory service protocol — Phase 1 placeholder interface.

    Minimal surface needed by core services.  The actual implementation
    lives in adapters (wrapping the old MVP memory module until Phase 2
    migrates to SessionRepository).
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
    """User memory service protocol — Phase 1 placeholder interface.

    Minimal surface needed by core services.  The actual implementation
    wraps the old MVP user_memory module until Phase 2.
    """

    def on_user_message(
        self,
        user_id: int,
        text: str,
        *,
        nickname: str = "",
        is_private: bool = False,
        session_id: SessionId | None = None,
    ) -> None: ...

    def schedule_cognition_refine(
        self,
        user_id: int,
        *,
        recent_exchange: str = "",
    ) -> None: ...

    def schedule_memory_extract(
        self,
        user_id: int,
        text: str,
        *,
        nickname: str = "",
        group_id: int | None = None,
        context_lines: list[str] | None = None,
    ) -> None: ...
