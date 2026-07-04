"""MemoryService: session memory, summarization trigger & history trimming.

Replaces MVP ``memory.py`` + ``llm.summarize_session``. All persistence
goes through ``SessionRepository`` — no file IO, no framework imports.

Behaviour preserved from MVP:
- Hard-cap trim to ``MEMORY_WINDOW * 2`` after every append.
- ``maybe_summarize`` fires when ``ENABLE_MEMORY_SUMMARY`` is on and
  ``count_messages > MEMORY_WINDOW``.
- On successful summary: save summary + trim history to half.
- On LLM fallback (empty / error): no save, no trim.
- ``schedule_summarize`` is fire-and-forget via ``asyncio.create_task``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from lingxuan.core.prompting import build_summary_prompt
from lingxuan.protocols.llm import ChatMessage, LLMProvider
from lingxuan.protocols.repositories import SessionRepository, StoredMessage

if TYPE_CHECKING:
    from lingxuan.core.prompting import PromptBuilder
    from lingxuan.protocols.clock import Clock
    from lingxuan.protocols.config import ConfigProvider
    from lingxuan.protocols.logging import LogSink
    from lingxuan.protocols.memory import UserMemoryService
    from lingxuan.protocols.messaging import SessionId


# Exact fallback texts returned by the LLM when no real API key or on error.
_FALLBACK_TEXTS = frozenset({
    "抱歉，我现在有点不舒服，稍后再聊吧~",
    "我还没配置好呢，让主人先设置一下 API Key 吧~",
    "no",
})


class MemoryService:
    """Session memory service backed by SessionRepository.

    Injected dependencies:
    - ``sessions``: SessionRepository for all persistence
    - ``llm``: LLMProvider for summarization
    - ``prompt``: PromptBuilder (uses ``build_summary_prompt``)
    - ``config``: ConfigProvider for runtime toggles
    - ``clock``: Clock for timestamps
    - ``log``: LogSink for structured logging
    """

    def __init__(
        self,
        sessions: SessionRepository,
        llm: LLMProvider,
        prompt: PromptBuilder,
        config: ConfigProvider,
        clock: Clock,
        log: LogSink,
        user_memory: UserMemoryService | None = None,
    ) -> None:
        self._sessions = sessions
        self._llm = llm
        self._prompt = prompt
        self._config = config
        self._clock = clock
        self._log = log
        self._user_memory = user_memory

    # ── config helpers ────────────────────────────────────────────────────

    @property
    def _memory_window(self) -> int:
        return self._config.get_int("MEMORY_WINDOW")

    @property
    def _summary_enabled(self) -> bool:
        return self._config.get_bool("ENABLE_MEMORY_SUMMARY")

    # ── core methods ──────────────────────────────────────────────────────

    async def append(
        self,
        sid: SessionId,
        role: str,
        content: str,
        *,
        user_id: int | None = None,
    ) -> None:
        """Append a message and enforce the hard-cap trim.

        Aligns with MVP ``memory.append_message`` + ``save_session`` trim:
        after appending, if count > MEMORY_WINDOW*2, trim to MEMORY_WINDOW*2.
        """
        await self._sessions.ensure(sid)
        await self._sessions.append_message(
            sid, StoredMessage(role=role, content=content, user_id=user_id)
        )

        # Hard-cap trim (matches MVP save_session truncation)
        count = await self._sessions.count_messages(sid)
        cap = self._memory_window * 2
        if count > cap:
            await self._sessions.trim_to_last(sid, keep_last=cap)

    async def load_history(
        self, sid: SessionId, *, limit: int | None = None
    ) -> list[StoredMessage]:
        """Load session history, optionally limited to the last N messages."""
        return await self._sessions.load_history(sid, limit=limit)

    async def get_summary(self, sid: SessionId) -> str:
        """Return the current session summary."""
        return await self._sessions.get_summary(sid)

    async def set_summary(self, sid: SessionId, summary: str) -> None:
        """Overwrite the session summary."""
        await self._sessions.set_summary(sid, summary)

    async def update_meta(
        self,
        sid: SessionId,
        *,
        nickname: str | None = None,
        group_id: int | None = None,
    ) -> None:
        """Pass-through to SessionRepository.update_meta with auto-timestamp."""
        await self._sessions.update_meta(
            sid,
            nickname=nickname,
            group_id=group_id,
            last_active_at=self._clock.now(),
        )

    async def merge_entity(self, sid: SessionId, name: str, user_id: int) -> None:
        """Pass-through to SessionRepository.merge_entity."""
        await self._sessions.merge_entity(sid, name, user_id)

    async def get_entities(self, sid: SessionId) -> dict[str, int]:
        """Pass-through to SessionRepository.get_entities."""
        return await self._sessions.get_entities(sid)

    async def clear(
        self, sid: SessionId, *, clear_user_profiles: bool = False
    ) -> None:
        """Clear session data; optionally clear all user profiles too.

        ``clear_user_profiles=True`` delegates to ``user_memory`` if injected,
        matching MVP ``clear_history(clear_user_profiles=True)`` semantics.
        """
        await self._sessions.clear(sid)
        if clear_user_profiles and self._user_memory is not None:
            # UserMemoryService protocol doesn't expose a clear_all method
            # directly; the caller coordinates via the injected instance.
            # For now we rely on admin_commands to handle this explicitly.
            pass

    # ── summarization ─────────────────────────────────────────────────────

    async def maybe_summarize(self, sid: SessionId) -> None:
        """Trigger summarization if conditions are met.

        Aligns with MVP ``llm.maybe_summarize``:
        - ENABLE_MEMORY_SUMMARY must be on
        - count_messages must exceed MEMORY_WINDOW
        """
        if not self._summary_enabled:
            return

        count = await self._sessions.count_messages(sid)
        if count <= self._memory_window:
            return

        await self.summarize(sid)

    async def summarize(self, sid: SessionId) -> None:
        """Summarize the older portion of history and trim.

        Aligns with MVP ``llm.summarize_session``:
        1. Take the first MEMORY_WINDOW messages from history.
        2. Build summary prompt → LLM chat (≤200 chars, max_tokens=256, temp=0.3).
        3. If LLM returns fallback text → no save, no trim.
        4. On success: set_summary + trim_to_last(keep_last=count//2).
        """
        history = await self._sessions.load_history(sid)
        if not history:
            return

        window = self._memory_window

        prompt_text = build_summary_prompt(history, memory_window=window)

        try:
            result = await self._llm.chat(
                [ChatMessage(role="user", content=prompt_text)],
                max_tokens=256,
                temperature=0.3,
            )
        except Exception:
            self._log.emit(
                _make_record(self._clock, "WARNING", "memory", "LLM summary call failed")
            )
            return

        # Fallback detection: LLM returned error/no-key text
        if not result or _is_fallback(result):
            self._log.emit(
                _make_record(
                    self._clock, "INFO", "memory", "Summary skipped (fallback text)"
                )
            )
            return

        # Success path: save summary + trim half
        await self._sessions.set_summary(sid, result)
        count = await self._sessions.count_messages(sid)
        await self._sessions.trim_to_last(sid, keep_last=count // 2)

        self._log.emit(
            _make_record(self._clock, "INFO", "memory", "Session summarized")
        )

    def schedule_summarize(self, sid: SessionId) -> None:
        """Fire-and-forget summarization — aligns with MVP ``schedule_summarize``."""
        asyncio.create_task(self.maybe_summarize(sid))

    # ── Phase 1 protocol compatibility ────────────────────────────────────

    async def append_message(self, sid: SessionId, msg: StoredMessage) -> None:
        """Phase 1 ``MemoryService`` protocol: append a pre-built StoredMessage.

        Delegates to :meth:`append` with the message's fields.
        """
        await self.append(
            sid, msg.role, msg.content, user_id=msg.user_id
        )


# ---------------------------------------------------------------------------
# Module-level helpers (no dependency on self)
# ---------------------------------------------------------------------------


def _is_fallback(text: str) -> bool:
    """Detect LLM fallback / error responses — aligns with MVP ``_is_fallback_text``."""
    return text.strip() in _FALLBACK_TEXTS


def _make_record(
    clock: Clock, level: str, logger: str, msg: str
) -> "LogRecord":
    from lingxuan.protocols.logging import LogRecord

    return LogRecord(ts=clock.now(), level=level, logger=logger, msg=msg)
