"""Tests for core/dialogue.py — DialogueService & GroupReplyExecutor."""

from __future__ import annotations

import random

import pytest

from lingxuan.core.dialogue import (
    AdminCommandService,
    CommandContext,
    DialogueService,
    GroupReplyExecutor,
    MemoryService,
    UserMemoryService,
    _format_exchange,
)
from lingxuan.core.observation import ObservationService
from lingxuan.core.observation_state import ObservationStore
from lingxuan.core.persona import PersonaService
from lingxuan.core.prompting import PromptBuilder
from lingxuan.core.reply_planner import ReplyPlanner
from lingxuan.protocols.messaging import (
    Actor,
    InboundMessage,
    OutboundChunk,
    OutboundMessage,
    ReplyTarget,
    SessionId,
)
from lingxuan.protocols.repositories import StoredMessage
from tests.fakes.clock import FakeClock
from tests.fakes.config import FakeConfigProvider
from tests.fakes.llm import FakeLLMProvider
from tests.fakes.repositories import InMemorySessionRepository
from tests.fakes.transport import FakeTransport


# ── fake services for DialogueService dependencies ────────────────────────


class FakeMemoryService:
    """Implements MemoryService protocol with recording."""

    def __init__(self) -> None:
        self.appended_messages: list[tuple[SessionId, StoredMessage]] = []
        self.meta_updates: list[tuple[SessionId, dict]] = []
        self.summarize_scheduled: list[SessionId] = []

    async def append_message(self, session_id: SessionId, msg: StoredMessage) -> None:
        self.appended_messages.append((session_id, msg))

    async def update_meta(
        self,
        session_id: SessionId,
        *,
        nickname: str | None = None,
        group_id: int | None = None,
    ) -> None:
        self.meta_updates.append((session_id, {"nickname": nickname, "group_id": group_id}))

    def schedule_summarize(self, session_id: SessionId) -> None:
        self.summarize_scheduled.append(session_id)


class FakeUserMemoryService:
    """Implements UserMemoryService protocol with recording."""

    def __init__(self) -> None:
        self.on_user_message_calls: list[dict] = []
        self.cognition_refine_calls: list[dict] = []
        self.memory_extract_calls: list[dict] = []

    def on_user_message(
        self,
        user_id: int,
        text: str,
        *,
        nickname: str = "",
        is_private: bool = False,
        session_id: SessionId | None = None,
    ) -> None:
        self.on_user_message_calls.append({
            "user_id": user_id,
            "text": text,
            "nickname": nickname,
            "is_private": is_private,
            "session_id": session_id,
        })

    def schedule_cognition_refine(
        self,
        user_id: int,
        *,
        recent_exchange: str = "",
    ) -> None:
        self.cognition_refine_calls.append({
            "user_id": user_id,
            "recent_exchange": recent_exchange,
        })

    def schedule_memory_extract(
        self,
        user_id: int,
        text: str,
        *,
        nickname: str = "",
        group_id: int | None = None,
        context_lines: list[str] | None = None,
    ) -> None:
        self.memory_extract_calls.append({
            "user_id": user_id,
            "text": text,
            "nickname": nickname,
            "group_id": group_id,
            "context_lines": context_lines,
        })


class FakeAdminCommandService:
    """Implements AdminCommandService protocol with controllable responses."""

    def __init__(self, *, command_result: str = "admin reply") -> None:
        self.parse_calls: list[str] = []
        self.run_calls: list[tuple[str, list[str], CommandContext]] = []
        self._command_result = command_result
        self._next_parse_result: tuple[str, list[str]] | None = None

    def set_parse_result(self, result: tuple[str, list[str]] | None) -> None:
        self._next_parse_result = result

    def parse_command(self, text: str) -> tuple[str, list[str]] | None:
        self.parse_calls.append(text)
        if self._next_parse_result is not None:
            return self._next_parse_result
        return None

    async def run(self, cmd: str, args: list[str], ctx: CommandContext) -> str:
        self.run_calls.append((cmd, args, ctx))
        return self._command_result


# ── helpers ──────────────────────────────────────────────────────────────


def _private_inbound(
    user_id: int = 1,
    nickname: str = "小明",
    text: str = "你好",
    is_admin: bool = False,
) -> InboundMessage:
    return InboundMessage(
        session_id=SessionId(kind="private", peer_id=user_id),
        actor=Actor(user_id=user_id, nickname=nickname, is_admin=is_admin),
        text=text,
    )


def _group_inbound(
    user_id: int = 1,
    nickname: str = "小明",
    text: str = "你好",
    group_id: int = 100,
    at_bot: bool = False,
    reply_to_bot: bool = False,
    is_admin: bool = False,
    is_self: bool = False,
    at_user_ids: list[int] | None = None,
) -> InboundMessage:
    return InboundMessage(
        session_id=SessionId(kind="group", peer_id=group_id),
        actor=Actor(
            user_id=user_id, nickname=nickname, is_admin=is_admin, is_self=is_self
        ),
        text=text,
        at_bot=at_bot,
        reply_to_bot=reply_to_bot,
        at_user_ids=at_user_ids or [],
        group_id=group_id,
    )


def _make_dialogue(
    **config_overrides: object,
) -> tuple[
    DialogueService,
    FakeConfigProvider,
    FakeLLMProvider,
    FakeTransport,
    FakeMemoryService,
    FakeUserMemoryService,
    FakeAdminCommandService,
    ObservationStore,
    InMemorySessionRepository,
]:
    config = FakeConfigProvider(config_overrides)
    clock = FakeClock()
    llm = FakeLLMProvider()
    transport = FakeTransport()
    memory = FakeMemoryService()
    user_memory = FakeUserMemoryService()
    admin_commands = FakeAdminCommandService()
    sessions = InMemorySessionRepository()
    obs_store = ObservationStore(config=config, clock=clock)
    persona = PersonaService(config)
    prompt = PromptBuilder(persona, config)
    planner = ReplyPlanner(config, rng=random.Random(42))

    observation = ObservationService(
        store=obs_store,
        llm=llm,
        prompt=prompt,
        planner=planner,
        sessions=sessions,
        transport=transport,
        config=config,
        clock=clock,
    )

    svc = DialogueService(
        config=config,
        llm=llm,
        prompt=prompt,
        planner=planner,
        transport=transport,
        memory=memory,
        user_memory=user_memory,
        admin_commands=admin_commands,
        persona=persona,
        observation=observation,
        observation_store=obs_store,
        sessions=sessions,
        clock=clock,
    )
    return (
        svc, config, llm, transport, memory, user_memory,
        admin_commands, obs_store, sessions,
    )


# ── _format_exchange ─────────────────────────────────────────────────────


class TestFormatExchange:
    def test_basic(self):
        result = _format_exchange("灵轩", "小明", "你好", "你好呀~")
        assert result == "用户[小明]: 你好\n灵轩: 你好呀~"

    def test_custom_bot_name(self):
        result = _format_exchange("小助手", "小红", "在吗", "在呢~")
        assert result == "用户[小红]: 在吗\n小助手: 在呢~"


# ── private chat ─────────────────────────────────────────────────────────


class TestPrivateChat:
    @pytest.mark.asyncio
    async def test_normal_message_flow(self):
        """Private normal message: memory.append twice + transport.send once + summarize scheduled."""
        svc, config, llm, transport, memory, user_memory, admin, obs, sessions = _make_dialogue()
        llm.set_chat_response("你好呀~")

        inbound = _private_inbound(user_id=1, text="你好")
        await svc.handle_inbound(inbound)

        # memory.append called twice: user + assistant
        assert len(memory.appended_messages) == 2
        assert memory.appended_messages[0][1].role == "user"
        assert memory.appended_messages[0][1].content == "你好"
        assert memory.appended_messages[1][1].role == "assistant"
        assert memory.appended_messages[1][1].content == "你好呀~"

        # transport.send called once
        assert len(transport.sent_messages) == 1
        assert transport.sent_messages[0].chunks[0].text == "你好呀~"

        # summarize scheduled
        assert len(memory.summarize_scheduled) == 1
        assert memory.summarize_scheduled[0] == inbound.session_id

        # user_memory.on_user_message called
        assert len(user_memory.on_user_message_calls) == 1
        assert user_memory.on_user_message_calls[0]["is_private"] is True

        # cognition refine scheduled
        assert len(user_memory.cognition_refine_calls) == 1

    @pytest.mark.asyncio
    async def test_disabled_returns_early(self):
        """ENABLE_PRIVATE_CHAT off → no processing."""
        svc, *_ = _make_dialogue(ENABLE_PRIVATE_CHAT=False)
        memory = svc._memory

        await svc.handle_inbound(_private_inbound(text="你好"))

        assert len(memory.appended_messages) == 0

    @pytest.mark.asyncio
    async def test_empty_text_returns_early(self):
        """Empty text → no processing."""
        svc, *_ = _make_dialogue()
        memory = svc._memory

        await svc.handle_inbound(_private_inbound(text="   "))

        assert len(memory.appended_messages) == 0

    @pytest.mark.asyncio
    async def test_admin_command_dispatched(self):
        """Admin command: admin_commands.run called, LLM not called, reply sent."""
        svc, config, llm, transport, memory, user_memory, admin, obs, sessions = _make_dialogue()
        admin.set_parse_result(("status", []))

        inbound = _private_inbound(user_id=1, text="/灵轩 status", is_admin=True)
        await svc.handle_inbound(inbound)

        # admin command was dispatched
        assert len(admin.run_calls) == 1
        assert admin.run_calls[0][0] == "status"

        # transport.send called with admin reply
        assert len(transport.sent_messages) == 1
        assert transport.sent_messages[0].chunks[0].text == "admin reply"

        # LLM not called
        assert len(llm.chat_calls) == 0

        # No memory writes
        assert len(memory.appended_messages) == 0

    @pytest.mark.asyncio
    async def test_admin_no_command_match_goes_normal(self):
        """Admin user but no command match → normal chat flow."""
        svc, config, llm, transport, memory, user_memory, admin, obs, sessions = _make_dialogue()
        llm.set_chat_response("好的")
        # parse_command returns None (default)
        admin.set_parse_result(None)

        inbound = _private_inbound(user_id=1, text="你好", is_admin=True)
        await svc.handle_inbound(inbound)

        # Normal flow: LLM called
        assert len(llm.chat_calls) == 1
        assert len(memory.appended_messages) == 2

    @pytest.mark.asyncio
    async def test_non_admin_no_command_check(self):
        """Non-admin user: parse_command not called."""
        svc, config, llm, transport, memory, user_memory, admin, obs, sessions = _make_dialogue()
        llm.set_chat_response("嗯嗯")

        inbound = _private_inbound(user_id=1, text="/灵轩 status", is_admin=False)
        await svc.handle_inbound(inbound)

        # parse_command not called (is_admin=False)
        assert len(admin.parse_calls) == 0
        # Normal flow
        assert len(llm.chat_calls) == 1


# ── group chat ───────────────────────────────────────────────────────────


class TestGroupChat:
    @pytest.mark.asyncio
    async def test_self_message_ignored(self):
        """is_self=True → no processing."""
        svc, *_ = _make_dialogue()
        memory = svc._memory

        inbound = _group_inbound(user_id=1, is_self=True, text="你好")
        await svc.handle_inbound(inbound)

        assert len(memory.appended_messages) == 0

    @pytest.mark.asyncio
    async def test_disabled_returns_early(self):
        """ENABLE_GROUP_CHAT off → no processing."""
        svc, *_ = _make_dialogue(ENABLE_GROUP_CHAT=False)
        memory = svc._memory

        inbound = _group_inbound(user_id=1, text="你好")
        await svc.handle_inbound(inbound)

        assert len(memory.appended_messages) == 0

    @pytest.mark.asyncio
    async def test_admin_command_dispatched(self):
        """Admin command in group: dispatched, LLM not called."""
        svc, config, llm, transport, memory, user_memory, admin, obs, sessions = _make_dialogue()
        admin.set_parse_result(("status", []))

        inbound = _group_inbound(
            user_id=1, text="/灵轩 status", is_admin=True, group_id=100
        )
        await svc.handle_inbound(inbound)

        assert len(admin.run_calls) == 1
        cmd, args, ctx = admin.run_calls[0]
        assert cmd == "status"
        assert ctx.is_group is True
        assert ctx.group_id == 100

        # LLM not called
        assert len(llm.chat_calls) == 0
        assert len(llm.stream_calls) == 0

    @pytest.mark.asyncio
    async def test_empty_text_non_at_skipped(self):
        """Empty text without at_bot → skipped."""
        svc, *_ = _make_dialogue()
        memory = svc._memory

        inbound = _group_inbound(user_id=1, text="   ", at_bot=False)
        await svc.handle_inbound(inbound)

        assert len(memory.appended_messages) == 0

    @pytest.mark.asyncio
    async def test_at_bot_direct_reply(self):
        """@bot: stream reply sent, memory written, observation marked."""
        svc, config, llm, transport, memory, user_memory, admin, obs_store, sessions = _make_dialogue()
        llm.set_stream_tokens(["你", "好", "呀", "~"])

        inbound = _group_inbound(
            user_id=1, nickname="小明", text="你好", group_id=100, at_bot=True
        )
        await svc.handle_inbound(inbound)

        # Stream reply sent
        assert len(transport.sent_stream_chunks) == 1
        # First chunk should have at_user_id
        first_chunk = transport.sent_stream_chunks[0][0]
        assert first_chunk.at_user_id == 1

        # Memory: user message + assistant message
        assert len(memory.appended_messages) == 2
        assert memory.appended_messages[0][1].role == "user"
        assert "[小明]: 你好" in memory.appended_messages[0][1].content
        assert memory.appended_messages[1][1].role == "assistant"

        # Observation store: bot message recorded
        buf = obs_store.buffer(100)
        assert any(e.is_bot for e in buf)

        # Summarize scheduled
        assert len(memory.summarize_scheduled) == 1

        # Cognition refine scheduled
        assert len(user_memory.cognition_refine_calls) == 1

        # Memory extract scheduled
        assert len(user_memory.memory_extract_calls) == 1

    @pytest.mark.asyncio
    async def test_at_bot_empty_text_uses_placeholder(self):
        """@bot with empty text → uses '在呢' as clean_message."""
        svc, config, llm, transport, memory, user_memory, admin, obs_store, sessions = _make_dialogue()
        llm.set_stream_tokens(["在", "呢", "~"])

        inbound = _group_inbound(
            user_id=1, nickname="小明", text="", group_id=100, at_bot=True
        )
        await svc.handle_inbound(inbound)

        # User message in memory should contain "在呢"
        assert len(memory.appended_messages) >= 1
        assert "在呢" in memory.appended_messages[0][1].content

        # Memory extract called with "在呢"
        assert len(user_memory.memory_extract_calls) == 1
        assert user_memory.memory_extract_calls[0]["text"] == "在呢"

    @pytest.mark.asyncio
    async def test_reply_to_bot_treated_as_at_bot(self):
        """reply_to_bot=True → same as at_bot direct reply path."""
        svc, config, llm, transport, memory, user_memory, admin, obs_store, sessions = _make_dialogue()
        llm.set_stream_tokens(["嗯", "嗯"])

        inbound = _group_inbound(
            user_id=1, text="继续说", group_id=100, reply_to_bot=True
        )
        await svc.handle_inbound(inbound)

        # Stream reply sent (direct reply path)
        assert len(transport.sent_stream_chunks) == 1

    @pytest.mark.asyncio
    async def test_non_at_delegates_to_observation(self):
        """Non-@ message → delegated to observation.on_group_message."""
        svc, config, llm, transport, memory, user_memory, admin, obs_store, sessions = _make_dialogue()

        inbound = _group_inbound(
            user_id=1, nickname="小明", text="今天天气不错", group_id=100, at_bot=False
        )
        await svc.handle_inbound(inbound)

        # No direct reply sent
        assert len(transport.sent_stream_chunks) == 0
        assert len(transport.sent_messages) == 0

        # No memory.append from DialogueService (observation handles its own)
        # But memory.update_meta was called
        assert len(memory.meta_updates) == 1

        # Memory extract was scheduled
        assert len(user_memory.memory_extract_calls) == 1

        # Observation store should have the entry buffered
        buf = obs_store.buffer(100)
        assert len(buf) == 1
        assert buf[0].text == "今天天气不错"

    @pytest.mark.asyncio
    async def test_meta_updated_on_group_message(self):
        """Group message updates session meta with nickname and group_id."""
        svc, config, llm, transport, memory, user_memory, admin, obs_store, sessions = _make_dialogue()

        inbound = _group_inbound(
            user_id=1, nickname="小红", text="嗨", group_id=200, at_bot=False
        )
        await svc.handle_inbound(inbound)

        assert len(memory.meta_updates) == 1
        sid, kwargs = memory.meta_updates[0]
        assert kwargs["nickname"] == "小红"
        assert kwargs["group_id"] == 200


# ── GroupReplyExecutor ───────────────────────────────────────────────────


class TestGroupReplyExecutor:
    @pytest.mark.asyncio
    async def test_execute_with_observation(self):
        """Observation path: extra_user block included in prompt."""
        config = FakeConfigProvider()
        llm = FakeLLMProvider()
        llm.set_stream_tokens(["回", "复"])
        transport = FakeTransport()
        sessions = InMemorySessionRepository()
        persona = PersonaService(config)
        prompt = PromptBuilder(persona, config)
        planner = ReplyPlanner(config, rng=random.Random(42))

        executor = GroupReplyExecutor(
            prompt=prompt, llm=llm, planner=planner,
            transport=transport, sessions=sessions, config=config,
        )

        session_id = SessionId(kind="group", peer_id=100)
        reply = await executor.execute(
            session_id=session_id,
            observation_text="观察文本",
            at_user_id=1,
        )

        assert reply == "回复"
        # Stream was called
        assert len(llm.stream_calls) == 1
        # Check that the extra_user block contains observation text
        messages = llm.stream_calls[0]
        has_obs = any("观察文本" in m.content for m in messages)
        assert has_obs

    @pytest.mark.asyncio
    async def test_execute_without_observation(self):
        """Direct-@ path: no extra_user block in prompt."""
        config = FakeConfigProvider()
        llm = FakeLLMProvider()
        llm.set_stream_tokens(["好", "的"])
        transport = FakeTransport()
        sessions = InMemorySessionRepository()
        persona = PersonaService(config)
        prompt = PromptBuilder(persona, config)
        planner = ReplyPlanner(config, rng=random.Random(42))

        executor = GroupReplyExecutor(
            prompt=prompt, llm=llm, planner=planner,
            transport=transport, sessions=sessions, config=config,
        )

        session_id = SessionId(kind="group", peer_id=100)
        reply = await executor.execute(
            session_id=session_id,
            observation_text=None,
            at_user_id=1,
        )

        assert reply == "好的"
        # No observation block in messages
        messages = llm.stream_calls[0]
        has_obs_block = any("当前群聊观察" in m.content for m in messages)
        assert not has_obs_block


# ── CommandContext ────────────────────────────────────────────────────────


class TestCommandContext:
    def test_defaults(self):
        ctx = CommandContext(user_id=1, session_id=SessionId(kind="private", peer_id=1))
        assert ctx.is_group is False
        assert ctx.group_id is None
        assert ctx.nickname == ""

    def test_group_context(self):
        sid = SessionId(kind="group", peer_id=100)
        ctx = CommandContext(user_id=1, session_id=sid, is_group=True, group_id=100, nickname="小明")
        assert ctx.is_group is True
        assert ctx.group_id == 100
