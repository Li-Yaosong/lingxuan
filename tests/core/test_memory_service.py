"""Tests for core/memory.py — MemoryService with InMemorySessionRepository + FakeLLM.

Acceptance criteria from P2-08:
- Append beyond 41 messages → auto-trim to 40 (MEMORY_WINDOW*2).
- History > MEMORY_WINDOW with ENABLE_MEMORY_SUMMARY on → maybe_summarize
  calls LLM; success → summary stored + history halved.
- LLM returns fallback text → no summary saved, no trim.
"""

from __future__ import annotations

import asyncio

import pytest

from lingxuan.core.memory import MemoryService, _is_fallback
from lingxuan.core.persona import PersonaService
from lingxuan.core.prompting import PromptBuilder
from lingxuan.core.user_memory import UserMemoryService
from lingxuan.protocols.messaging import SessionId
from lingxuan.protocols.repositories import StoredMessage
from tests.fakes.clock import FakeClock
from tests.fakes.config import FakeConfigProvider
from tests.fakes.llm import FakeLLMProvider
from tests.fakes.logsink import FakeLogSink
from tests.fakes.repositories import (
    InMemorySessionRepository,
    InMemorySocialGraphRepository,
    InMemoryUserProfileRepository,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sid(peer: int = 1) -> SessionId:
    return SessionId(kind="private", peer_id=peer)


def _make_service(
    *,
    memory_window: int = 20,
    enable_summary: bool = True,
    llm: FakeLLMProvider | None = None,
) -> tuple[MemoryService, InMemorySessionRepository, FakeLLMProvider]:
    config = FakeConfigProvider({
        "MEMORY_WINDOW": memory_window,
        "ENABLE_MEMORY_SUMMARY": enable_summary,
        "ENABLE_USER_MEMORY": False,
    })
    sessions = InMemorySessionRepository()
    fake_llm = llm or FakeLLMProvider()
    persona = PersonaService(config)
    prompt = PromptBuilder(persona, config)
    clock = FakeClock()
    log = FakeLogSink()

    svc = MemoryService(
        sessions=sessions,
        llm=fake_llm,
        prompt=prompt,
        config=config,
        clock=clock,
        log=log,
    )
    return svc, sessions, fake_llm


# ---------------------------------------------------------------------------
# Hard-cap trim after append
# ---------------------------------------------------------------------------

class TestAppendHardCap:
    """append() must enforce MEMORY_WINDOW*2 hard cap."""

    @pytest.mark.asyncio
    async def test_trim_at_cap(self) -> None:
        """Appending the 41st message trims to 40 (MEMORY_WINDOW=20, cap=40)."""
        svc, sessions, _ = _make_service(memory_window=20)
        sid = _sid()

        # Append 41 messages
        for i in range(41):
            await svc.append(sid, "user", f"msg-{i}")

        count = await sessions.count_messages(sid)
        assert count == 40

    @pytest.mark.asyncio
    async def test_no_trim_below_cap(self) -> None:
        """40 messages (exactly at cap) should NOT be trimmed."""
        svc, sessions, _ = _make_service(memory_window=20)
        sid = _sid()

        for i in range(40):
            await svc.append(sid, "user", f"msg-{i}")

        count = await sessions.count_messages(sid)
        assert count == 40

    @pytest.mark.asyncio
    async def test_trim_keeps_latest(self) -> None:
        """After trim, the remaining messages should be the latest ones."""
        svc, sessions, _ = _make_service(memory_window=20)
        sid = _sid()

        for i in range(41):
            await svc.append(sid, "user", f"msg-{i}")

        history = await sessions.load_history(sid)
        # Should keep messages 1..40 (0-indexed), i.e. "msg-1" through "msg-40"
        assert history[0].content == "msg-1"
        assert history[-1].content == "msg-40"

    @pytest.mark.asyncio
    async def test_small_window_custom(self) -> None:
        """With MEMORY_WINDOW=5, cap is 10; 11th message triggers trim to 10."""
        svc, sessions, _ = _make_service(memory_window=5)
        sid = _sid()

        for i in range(11):
            await svc.append(sid, "user", f"msg-{i}")

        count = await sessions.count_messages(sid)
        assert count == 10

    @pytest.mark.asyncio
    async def test_append_with_user_id(self) -> None:
        """append() passes user_id through to StoredMessage."""
        svc, sessions, _ = _make_service()
        sid = _sid()

        await svc.append(sid, "user", "hello", user_id=123)

        history = await sessions.load_history(sid)
        assert len(history) == 1
        assert history[0].user_id == 123
        assert history[0].role == "user"
        assert history[0].content == "hello"


# ---------------------------------------------------------------------------
# load_history / get_summary / set_summary / update_meta / entities
# ---------------------------------------------------------------------------

class TestPassthroughMethods:
    """Methods that delegate directly to SessionRepository."""

    @pytest.mark.asyncio
    async def test_load_history(self) -> None:
        svc, _, _ = _make_service()
        sid = _sid()

        await svc.append(sid, "user", "hi")
        await svc.append(sid, "assistant", "hello")

        history = await svc.load_history(sid)
        assert len(history) == 2

    @pytest.mark.asyncio
    async def test_load_history_with_limit(self) -> None:
        svc, _, _ = _make_service()
        sid = _sid()

        for i in range(5):
            await svc.append(sid, "user", f"msg-{i}")

        history = await svc.load_history(sid, limit=3)
        assert len(history) == 3
        assert history[0].content == "msg-2"

    @pytest.mark.asyncio
    async def test_get_set_summary(self) -> None:
        svc, sessions, _ = _make_service()
        sid = _sid()

        # Ensure session exists first (set_summary is a no-op if session doesn't exist)
        await sessions.ensure(sid)

        assert await svc.get_summary(sid) == ""
        await svc.set_summary(sid, "test summary")
        assert await svc.get_summary(sid) == "test summary"

    @pytest.mark.asyncio
    async def test_update_meta(self) -> None:
        svc, sessions, _ = _make_service()
        sid = _sid()

        await svc.append(sid, "user", "hi")  # ensure session
        await svc.update_meta(sid, nickname="Alice", group_id=42)

        session = await sessions.get(sid)
        assert session is not None
        assert session.nickname == "Alice"
        assert session.group_id == 42
        assert session.last_active_at is not None

    @pytest.mark.asyncio
    async def test_merge_entity_and_get_entities(self) -> None:
        svc, _, _ = _make_service()
        sid = _sid()

        await svc.append(sid, "user", "hi")  # ensure session
        await svc.merge_entity(sid, "小堞宝", 111)
        entities = await svc.get_entities(sid)
        assert entities == {"小堞宝": 111}

    @pytest.mark.asyncio
    async def test_clear(self) -> None:
        svc, sessions, _ = _make_service()
        sid = _sid()

        for i in range(5):
            await svc.append(sid, "user", f"msg-{i}")
        await svc.clear(sid)

        count = await sessions.count_messages(sid)
        assert count == 0

    @pytest.mark.asyncio
    async def test_clear_with_user_profiles(self) -> None:
        """clear(clear_user_profiles=True) delegates to user_memory.clear_all()."""
        config = FakeConfigProvider({
            "MEMORY_WINDOW": 20,
            "ENABLE_MEMORY_SUMMARY": True,
            "ENABLE_USER_MEMORY": True,
        })
        sessions = InMemorySessionRepository()
        profiles = InMemoryUserProfileRepository()
        graph = InMemorySocialGraphRepository()
        fake_llm = FakeLLMProvider()
        persona = PersonaService(config)
        prompt = PromptBuilder(persona, config)
        clock = FakeClock()
        log = FakeLogSink()

        user_memory = UserMemoryService(
            profiles=profiles,
            graph=graph,
            llm=fake_llm,
            config=config,
            clock=clock,
            log=log,
        )

        svc = MemoryService(
            sessions=sessions,
            llm=fake_llm,
            prompt=prompt,
            config=config,
            clock=clock,
            log=log,
            user_memory=user_memory,
        )

        sid = _sid()
        # Create some session data
        for i in range(3):
            await svc.append(sid, "user", f"msg-{i}")

        # Create a user profile
        await profiles.upsert(
            __import__("lingxuan.protocols.repositories", fromlist=["UserProfile"]).UserProfile(
                user_id=42, preferred_name="Alice"
            )
        )
        assert len(await profiles.list_user_ids()) == 1

        # Clear with clear_user_profiles=True
        await svc.clear(sid, clear_user_profiles=True)

        # Session should be cleared
        count = await sessions.count_messages(sid)
        assert count == 0

        # User profiles should also be cleared
        assert len(await profiles.list_user_ids()) == 0

    @pytest.mark.asyncio
    async def test_clear_without_user_profiles(self) -> None:
        """clear(clear_user_profiles=False) does NOT clear user profiles."""
        config = FakeConfigProvider({
            "MEMORY_WINDOW": 20,
            "ENABLE_MEMORY_SUMMARY": True,
            "ENABLE_USER_MEMORY": True,
        })
        sessions = InMemorySessionRepository()
        profiles = InMemoryUserProfileRepository()
        graph = InMemorySocialGraphRepository()
        fake_llm = FakeLLMProvider()
        persona = PersonaService(config)
        prompt = PromptBuilder(persona, config)
        clock = FakeClock()
        log = FakeLogSink()

        user_memory = UserMemoryService(
            profiles=profiles,
            graph=graph,
            llm=fake_llm,
            config=config,
            clock=clock,
            log=log,
        )

        svc = MemoryService(
            sessions=sessions,
            llm=fake_llm,
            prompt=prompt,
            config=config,
            clock=clock,
            log=log,
            user_memory=user_memory,
        )

        sid = _sid()
        await svc.append(sid, "user", "hello")

        # Create a user profile
        await profiles.upsert(
            __import__("lingxuan.protocols.repositories", fromlist=["UserProfile"]).UserProfile(
                user_id=42, preferred_name="Alice"
            )
        )

        # Clear without clear_user_profiles
        await svc.clear(sid)

        # Session should be cleared
        count = await sessions.count_messages(sid)
        assert count == 0

        # User profiles should NOT be cleared
        assert len(await profiles.list_user_ids()) == 1


# ---------------------------------------------------------------------------
# maybe_summarize — trigger logic
# ---------------------------------------------------------------------------

class TestMaybeSummarize:
    """maybe_summarize triggers only when conditions are met."""

    @pytest.mark.asyncio
    async def test_disabled_no_call(self) -> None:
        """ENABLE_MEMORY_SUMMARY=False → no LLM call regardless of history."""
        svc, _, llm = _make_service(enable_summary=False, memory_window=20)
        sid = _sid()

        for i in range(25):
            await svc.append(sid, "user", f"msg-{i}")

        await svc.maybe_summarize(sid)
        assert len(llm.chat_calls) == 0

    @pytest.mark.asyncio
    async def test_below_window_no_call(self) -> None:
        """count <= MEMORY_WINDOW → no LLM call."""
        svc, _, llm = _make_service(enable_summary=True, memory_window=20)
        sid = _sid()

        for i in range(20):  # exactly at window, not above
            await svc.append(sid, "user", f"msg-{i}")

        await svc.maybe_summarize(sid)
        assert len(llm.chat_calls) == 0

    @pytest.mark.asyncio
    async def test_above_window_triggers(self) -> None:
        """count > MEMORY_WINDOW → LLM called."""
        svc, _, llm = _make_service(enable_summary=True, memory_window=20)
        sid = _sid()
        llm.set_chat_response("这是一段摘要")

        for i in range(21):
            await svc.append(sid, "user", f"msg-{i}")

        await svc.maybe_summarize(sid)
        assert len(llm.chat_calls) == 1


# ---------------------------------------------------------------------------
# summarize — success / fallback / error
# ---------------------------------------------------------------------------

class TestSummarize:
    """Summarization: success saves + trims half; fallback/error does nothing."""

    @pytest.mark.asyncio
    async def test_success_saves_and_halves(self) -> None:
        """Successful summary: summary stored, history halved."""
        svc, sessions, llm = _make_service(memory_window=20)
        sid = _sid()
        llm.set_chat_response("用户讨论了天气")

        # 25 messages (> MEMORY_WINDOW=20)
        for i in range(25):
            await svc.append(sid, "user", f"msg-{i}")

        await svc.summarize(sid)

        # Summary should be stored
        summary = await sessions.get_summary(sid)
        assert summary == "用户讨论了天气"

        # History should be halved: 25 // 2 = 12
        count = await sessions.count_messages(sid)
        assert count == 12

    @pytest.mark.asyncio
    async def test_fallback_no_save_no_trim(self) -> None:
        """LLM returns fallback text → no summary, no trim."""
        svc, sessions, llm = _make_service(memory_window=20)
        sid = _sid()
        llm.set_chat_response("抱歉，我现在有点不舒服，稍后再聊吧~")

        for i in range(25):
            await svc.append(sid, "user", f"msg-{i}")

        await svc.summarize(sid)

        # Summary should remain empty
        summary = await sessions.get_summary(sid)
        assert summary == ""

        # History should be unchanged (25)
        count = await sessions.count_messages(sid)
        assert count == 25

    @pytest.mark.asyncio
    async def test_empty_response_no_save_no_trim(self) -> None:
        """LLM returns empty string → no summary, no trim."""
        svc, sessions, llm = _make_service(memory_window=20)
        sid = _sid()
        llm.set_chat_response("")

        for i in range(25):
            await svc.append(sid, "user", f"msg-{i}")

        await svc.summarize(sid)

        summary = await sessions.get_summary(sid)
        assert summary == ""

        count = await sessions.count_messages(sid)
        assert count == 25

    @pytest.mark.asyncio
    async def test_llm_exception_no_save_no_trim(self) -> None:
        """LLM raises exception → no summary, no trim."""
        svc, sessions, llm = _make_service(memory_window=20)
        sid = _sid()

        # Make LLM.chat raise by setting no responses (FakeLLM returns "")
        # Actually we need it to raise. Let's override:
        for i in range(25):
            await svc.append(sid, "user", f"msg-{i}")

        # Override chat to raise
        original_chat = llm.chat

        async def _raise_chat(*args, **kwargs):
            raise RuntimeError("API error")

        llm.chat = _raise_chat  # type: ignore[assignment]

        await svc.summarize(sid)

        summary = await sessions.get_summary(sid)
        assert summary == ""

        count = await sessions.count_messages(sid)
        assert count == 25

        # Restore
        llm.chat = original_chat  # type: ignore[assignment]

    @pytest.mark.asyncio
    async def test_no_history_no_op(self) -> None:
        """Empty session → summarize does nothing."""
        svc, sessions, llm = _make_service()
        sid = _sid()

        await svc.summarize(sid)

        assert len(llm.chat_calls) == 0

    @pytest.mark.asyncio
    async def test_llm_called_with_correct_params(self) -> None:
        """LLM is called with max_tokens=256, temperature=0.3."""
        svc, _, llm = _make_service(memory_window=20)
        sid = _sid()
        llm.set_chat_response("摘要")

        for i in range(25):
            await svc.append(sid, "user", f"msg-{i}")

        await svc.summarize(sid)

        assert len(llm.chat_kwargs) == 1
        assert llm.chat_kwargs[0]["max_tokens"] == 256
        assert llm.chat_kwargs[0]["temperature"] == 0.3


# ---------------------------------------------------------------------------
# schedule_summarize — fire-and-forget
# ---------------------------------------------------------------------------

class TestScheduleSummarize:
    """schedule_summarize fires maybe_summarize as an asyncio.Task."""

    @pytest.mark.asyncio
    async def test_fire_and_forget(self) -> None:
        """schedule_summarize creates a task that eventually runs."""
        svc, sessions, llm = _make_service(memory_window=20)
        sid = _sid()
        llm.set_chat_response("摘要文本")

        for i in range(25):
            await svc.append(sid, "user", f"msg-{i}")

        # Fire and forget
        svc.schedule_summarize(sid)

        # Give the task a chance to run
        await asyncio.sleep(0.05)

        # LLM should have been called
        assert len(llm.chat_calls) == 1

        # Summary should be stored
        summary = await sessions.get_summary(sid)
        assert summary == "摘要文本"

    @pytest.mark.asyncio
    async def test_disabled_no_task_effect(self) -> None:
        """schedule_summarize with summary disabled → task runs but does nothing."""
        svc, _, llm = _make_service(enable_summary=False, memory_window=20)
        sid = _sid()

        for i in range(25):
            await svc.append(sid, "user", f"msg-{i}")

        svc.schedule_summarize(sid)
        await asyncio.sleep(0.05)

        assert len(llm.chat_calls) == 0


# ---------------------------------------------------------------------------
# append_message — Phase 1 protocol compatibility
# ---------------------------------------------------------------------------

class TestAppendMessageCompat:
    """append_message() delegates to append() for Phase 1 protocol."""

    @pytest.mark.asyncio
    async def test_append_message_delegates(self) -> None:
        svc, sessions, _ = _make_service()
        sid = _sid()

        msg = StoredMessage(role="user", content="hello", user_id=42)
        await svc.append_message(sid, msg)

        history = await sessions.load_history(sid)
        assert len(history) == 1
        assert history[0].content == "hello"
        assert history[0].user_id == 42

    @pytest.mark.asyncio
    async def test_append_message_respects_cap(self) -> None:
        """append_message also triggers hard-cap trim."""
        svc, sessions, _ = _make_service(memory_window=20)
        sid = _sid()

        for i in range(41):
            await svc.append_message(
                sid, StoredMessage(role="user", content=f"msg-{i}")
            )

        count = await sessions.count_messages(sid)
        assert count == 40


# ---------------------------------------------------------------------------
# _is_fallback — unit test
# ---------------------------------------------------------------------------

class TestIsFallback:
    """_is_fallback matches MVP _is_fallback_text."""

    def test_exact_fallback(self) -> None:
        assert _is_fallback("抱歉，我现在有点不舒服，稍后再聊吧~")

    def test_no_key_fallback(self) -> None:
        assert _is_fallback("我还没配置好呢，让主人先设置一下 API Key 吧~")

    def test_no_fallback(self) -> None:
        assert _is_fallback("no")

    def test_normal_text_not_fallback(self) -> None:
        assert not _is_fallback("用户讨论了天气和出行计划")

    def test_partial_match_not_fallback(self) -> None:
        """Substring match should NOT trigger — MVP uses exact match."""
        assert not _is_fallback("抱歉我来晚了")

    def test_whitespace_trimmed(self) -> None:
        assert _is_fallback("  抱歉，我现在有点不舒服，稍后再聊吧~  ")
