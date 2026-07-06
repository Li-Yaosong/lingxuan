"""Tests for P5-03: Core hook integration — verify all 5 hooks fire and can affect flow.

Uses fakes + fake plugins to exercise each hook without IO.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from lingxuan.core.dialogue import DialogueService
from lingxuan.core.group_reply_executor import GroupReplyExecutor
from lingxuan.core.observation import ObservationService
from lingxuan.core.observation_state import ObservationStore
from lingxuan.core.persona import PersonaService
from lingxuan.core.prompting import PromptBuilder
from lingxuan.core.reply_planner import ReplyPlanner
from lingxuan.core.user_memory import UserMemoryService
from lingxuan.plugins.host import DefaultPluginHost
from lingxuan.protocols.messaging import (
    Actor,
    InboundMessage,
    OutboundChunk,
    OutboundMessage,
    ReplyPlan,
    ReplyTarget,
    SessionId,
)
from lingxuan.protocols.plugins import HookHandler, HookType, Plugin, PluginContext
from lingxuan.protocols.repositories import StoredMessage
from tests.fakes.clock import FakeClock
from tests.fakes.config import FakeConfigProvider
from tests.fakes.llm import FakeLLMProvider
from tests.fakes.logsink import FakeLogSink
from tests.fakes.repositories import InMemorySessionRepository
from tests.fakes.transport import FakeTransport


# ---------------------------------------------------------------------------
# Fakes for DialogueService dependencies
# ---------------------------------------------------------------------------


class FakeMemoryService:
    def __init__(self) -> None:
        self.appended: list[tuple[SessionId, StoredMessage]] = []
        self.summarize_scheduled: list[SessionId] = []

    async def append_message(self, session_id: SessionId, msg: StoredMessage) -> None:
        self.appended.append((session_id, msg))

    async def update_meta(
        self, session_id: SessionId, *, nickname: str | None = None, group_id: int | None = None,
    ) -> None:
        pass

    def schedule_summarize(self, session_id: SessionId) -> None:
        self.summarize_scheduled.append(session_id)


class FakeUserMemoryService:
    def __init__(self) -> None:
        self.cognition_calls: list[dict] = []
        self.extract_calls: list[dict] = []

    async def on_user_message(self, *a: Any, **kw: Any) -> None:
        pass

    async def schedule_cognition_refine(self, user_id: int, *, recent_exchange: str = "") -> None:
        self.cognition_calls.append({"user_id": user_id, "exchange": recent_exchange})

    async def schedule_memory_extract(self, *a: Any, **kw: Any) -> None:
        self.extract_calls.append({"args": a, "kwargs": kw})


class FakeAdminCommandService:
    def parse_command(self, text: str) -> tuple[str, list[str]] | None:
        return None

    async def run(self, cmd: str, args: list[str], ctx: Any) -> str:
        return ""


# ---------------------------------------------------------------------------
# Test plugins
# ---------------------------------------------------------------------------


class CancelInboundPlugin:
    """Plugin that cancels inbound messages."""

    name = "cancel_inbound"
    version = "0.1.0"

    def setup(self, host: Any, config: dict, services: object) -> None:
        host.subscribe(HookType.on_inbound_message, self._on_inbound)

    async def teardown(self) -> None:
        pass

    @staticmethod
    async def _on_inbound(ctx: PluginContext) -> PluginContext:
        ctx.cancelled = True
        return ctx


class ModifyReplyPlanPlugin:
    """Plugin that sets should_reply=False on on_before_reply."""

    name = "modify_reply_plan"
    version = "0.1.0"

    def setup(self, host: Any, config: dict, services: object) -> None:
        host.subscribe(HookType.on_before_reply, self._on_before)

    async def teardown(self) -> None:
        pass

    @staticmethod
    async def _on_before(ctx: PluginContext) -> PluginContext:
        if ctx.reply_plan is not None:
            ctx.reply_plan.should_reply = False
            ctx.reply_plan.reason = "plugin_blocked"
        return ctx


class RecordHookPlugin:
    """Plugin that records all hook calls it receives."""

    name = "recorder"
    version = "0.1.0"

    def __init__(self) -> None:
        self.calls: list[tuple[HookType, dict]] = []

    def setup(self, host: Any, config: dict, services: object) -> None:
        for hook in HookType:
            host.subscribe(hook, self._record)

    async def teardown(self) -> None:
        pass

    async def _record(self, ctx: PluginContext) -> PluginContext:
        self.calls.append((ctx.hook, dict(ctx.extra)))
        return ctx


class ModifyMemoryExtractPlugin:
    """Plugin that modifies memory extraction candidates."""

    name = "modify_memory_extract"
    version = "0.1.0"

    def setup(self, host: Any, config: dict, services: object) -> None:
        host.subscribe(HookType.on_memory_extract, self._on_extract)

    async def teardown(self) -> None:
        pass

    @staticmethod
    async def _on_extract(ctx: PluginContext) -> PluginContext:
        # Remove all facts — simulates a plugin filtering out candidates
        ctx.extra["facts"] = []
        return ctx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_private_inbound(text: str = "你好", user_id: int = 123) -> InboundMessage:
    return InboundMessage(
        session_id=SessionId(kind="private", peer_id=user_id),
        actor=Actor(user_id=user_id, nickname="测试用户"),
        text=text,
    )


def _make_group_inbound(
    text: str = "你好",
    user_id: int = 123,
    group_id: int = 456,
    at_bot: bool = True,
) -> InboundMessage:
    return InboundMessage(
        session_id=SessionId(kind="group", peer_id=group_id),
        actor=Actor(user_id=user_id, nickname="测试用户"),
        text=text,
        at_bot=at_bot,
        group_id=group_id,
    )


def _make_dialogue(
    plugin_host: DefaultPluginHost | None = None,
) -> DialogueService:
    config = FakeConfigProvider(overrides={
        "ENABLE_PRIVATE_CHAT": True,
        "ENABLE_GROUP_CHAT": True,
        "ENABLE_GROUP_OBSERVE": False,
        "BOT_NAME": "灵轩",
    })
    llm = FakeLLMProvider()
    llm.set_chat_response("你好呀~")  # private chat response
    llm.set_stream_tokens(["你", "好", "呀", "~"])  # group stream response
    transport = FakeTransport()
    clock = FakeClock()
    log = FakeLogSink()
    sessions = InMemorySessionRepository()
    memory = FakeMemoryService()
    user_memory = FakeUserMemoryService()
    admin = FakeAdminCommandService()
    persona = PersonaService(config)
    prompt = PromptBuilder(persona, config)
    planner = ReplyPlanner(config)
    obs_store = ObservationStore(config, clock)
    executor = GroupReplyExecutor(
        prompt=prompt, llm=llm, planner=planner,
        transport=transport, sessions=sessions, config=config,
    )
    observation = ObservationService(
        store=obs_store, executor=executor, llm=llm,
        sessions=sessions, memory=memory, user_memory=user_memory,
        config=config, clock=clock, plugin_host=plugin_host,
    )

    return DialogueService(
        config=config, llm=llm, prompt=prompt, planner=planner,
        transport=transport, memory=memory, user_memory=user_memory,
        admin_commands=admin, persona=persona, observation=observation,
        observation_store=obs_store, sessions=sessions, clock=clock,
        group_executor=executor, plugin_host=plugin_host,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOnInboundMessage:
    """on_inbound_message: cancel → skip processing."""

    @pytest.mark.asyncio
    async def test_cancel_inbound_skips_processing(self) -> None:
        host = DefaultPluginHost()
        host.register(CancelInboundPlugin())
        host.enable("cancel_inbound")

        dialogue = _make_dialogue(plugin_host=host)
        inbound = _make_private_inbound()

        await dialogue.handle_inbound(inbound)

        # Memory should NOT have been written — the message was cancelled
        assert len(dialogue._memory.appended) == 0

    @pytest.mark.asyncio
    async def test_no_plugin_inbound_proceeds_normally(self) -> None:
        dialogue = _make_dialogue(plugin_host=None)
        inbound = _make_private_inbound()

        await dialogue.handle_inbound(inbound)

        # Memory should have the user message appended
        assert len(dialogue._memory.appended) >= 1


class TestOnBeforeReply:
    """on_before_reply: modify plan → can block reply."""

    @pytest.mark.asyncio
    async def test_before_reply_can_block_private_reply(self) -> None:
        host = DefaultPluginHost()
        host.register(ModifyReplyPlanPlugin())
        host.enable("modify_reply_plan")

        dialogue = _make_dialogue(plugin_host=host)
        inbound = _make_private_inbound()

        await dialogue.handle_inbound(inbound)

        # No assistant message in memory (reply was blocked)
        roles = [msg.role for _, msg in dialogue._memory.appended]
        assert "assistant" not in roles

    @pytest.mark.asyncio
    async def test_before_reply_can_block_group_reply(self) -> None:
        host = DefaultPluginHost()
        host.register(ModifyReplyPlanPlugin())
        host.enable("modify_reply_plan")

        dialogue = _make_dialogue(plugin_host=host)
        inbound = _make_group_inbound(at_bot=True)

        await dialogue.handle_inbound(inbound)

        # No assistant message in memory (reply was blocked)
        roles = [msg.role for _, msg in dialogue._memory.appended]
        assert "assistant" not in roles


class TestOnAfterReply:
    """on_after_reply: fires after reply is sent."""

    @pytest.mark.asyncio
    async def test_after_reply_fires_for_private(self) -> None:
        recorder = RecordHookPlugin()
        host = DefaultPluginHost()
        host.register(recorder)
        host.enable("recorder")

        dialogue = _make_dialogue(plugin_host=host)
        inbound = _make_private_inbound()

        await dialogue.handle_inbound(inbound)

        after_calls = [
            (h, e) for h, e in recorder.calls if h == HookType.on_after_reply
        ]
        assert len(after_calls) == 1
        assert "reply_text" in after_calls[0][1]

    @pytest.mark.asyncio
    async def test_after_reply_fires_for_group_at(self) -> None:
        recorder = RecordHookPlugin()
        host = DefaultPluginHost()
        host.register(recorder)
        host.enable("recorder")

        dialogue = _make_dialogue(plugin_host=host)
        inbound = _make_group_inbound(at_bot=True)

        await dialogue.handle_inbound(inbound)

        after_calls = [
            (h, e) for h, e in recorder.calls if h == HookType.on_after_reply
        ]
        assert len(after_calls) == 1
        assert "reply_text" in after_calls[0][1]


class TestOnMemoryExtract:
    """on_memory_extract: can modify candidates before persist."""

    @pytest.mark.asyncio
    async def test_memory_extract_hook_modifies_candidates(self) -> None:
        config = FakeConfigProvider(overrides={
            "ENABLE_USER_MEMORY": True,
            "USER_MEMORY_BURST_MERGE": 0.01,
        })
        llm = FakeLLMProvider()
        clock = FakeClock()
        log = FakeLogSink()
        sessions = InMemorySessionRepository()

        from tests.fakes.repositories import (
            InMemorySocialGraphRepository,
            InMemoryUserProfileRepository,
        )

        profiles = InMemoryUserProfileRepository()
        graph = InMemorySocialGraphRepository()

        host = DefaultPluginHost()
        host.register(ModifyMemoryExtractPlugin())
        host.enable("modify_memory_extract")

        svc = UserMemoryService(
            profiles=profiles, graph=graph, llm=llm,
            config=config, clock=clock, log=log, plugin_host=host,
        )

        # Directly call _llm_extract_memory with a payload that would
        # normally produce facts — the plugin should clear them
        payload = {
            "user_id": 123,
            "text": "我叫小明",
            "nickname": "小明",
            "group_id": None,
            "context_lines": [],
        }

        # The FakeLLMProvider returns a fixed string, so the JSON parse
        # will likely fail. We need to make the LLM return valid JSON.
        # Patch the LLM to return valid extraction JSON.
        async def _fake_chat(
            messages: list[Any],
            **kwargs: Any,
        ) -> str:
            return '{"facts": [{"about_user_id": 123, "content": "喜欢猫", "category": "preference"}], "edges": [], "impression_delta": ""}'

        llm.chat = _fake_chat  # type: ignore[assignment]

        await svc._llm_extract_memory(payload)

        # The plugin cleared all facts, so no fact should have been added
        all_facts = await profiles.list_active_facts(123)
        assert len(all_facts) == 0


class TestOnConfigChange:
    """on_config_change: dispatches when config is set."""

    @pytest.mark.asyncio
    async def test_config_change_dispatches(self) -> None:
        recorder = RecordHookPlugin()
        host = DefaultPluginHost()
        host.register(recorder)
        host.enable("recorder")

        config = FakeConfigProvider()

        # Wire the bridge manually (same as Container.wire_config_change_bridge)
        import asyncio

        def _on_config_change(key: str, value: object) -> None:
            ctx = PluginContext(
                hook=HookType.on_config_change,
                extra={"key": key, "value": value},
            )
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(host.dispatch(ctx))
            except RuntimeError:
                pass

        config.subscribe(_on_config_change)

        # Trigger a config change
        await config.set("BOT_NAME", "新灵轩")

        # Give the async dispatch time to complete
        await asyncio.sleep(0.05)

        config_calls = [
            (h, e) for h, e in recorder.calls if h == HookType.on_config_change
        ]
        assert len(config_calls) == 1
        assert config_calls[0][1]["key"] == "BOT_NAME"
        assert config_calls[0][1]["value"] == "新灵轩"


class TestNoPluginNoSideEffect:
    """No plugin → behaviour identical to Phase 4 (dispatch is a no-op)."""

    @pytest.mark.asyncio
    async def test_private_chat_without_plugin(self) -> None:
        dialogue = _make_dialogue(plugin_host=None)
        inbound = _make_private_inbound()

        await dialogue.handle_inbound(inbound)

        # User + assistant messages should be written
        assert len(dialogue._memory.appended) >= 2
        roles = [msg.role for _, msg in dialogue._memory.appended]
        assert "user" in roles
        assert "assistant" in roles

    @pytest.mark.asyncio
    async def test_group_at_without_plugin(self) -> None:
        dialogue = _make_dialogue(plugin_host=None)
        inbound = _make_group_inbound(at_bot=True)

        await dialogue.handle_inbound(inbound)

        # User + assistant messages should be written
        assert len(dialogue._memory.appended) >= 2


class TestOnInboundMessageGroup:
    """on_inbound_message: cancel for group messages."""

    @pytest.mark.asyncio
    async def test_cancel_group_inbound_skips_processing(self) -> None:
        host = DefaultPluginHost()
        host.register(CancelInboundPlugin())
        host.enable("cancel_inbound")

        dialogue = _make_dialogue(plugin_host=host)
        inbound = _make_group_inbound(at_bot=True)

        await dialogue.handle_inbound(inbound)

        # Memory should NOT have been written — the message was cancelled
        assert len(dialogue._memory.appended) == 0


class TestOnBeforeReplyModifyPlan:
    """on_before_reply: plugin can modify reply_plan fields (not just cancel)."""

    @pytest.mark.asyncio
    async def test_before_reply_can_modify_reason(self) -> None:
        """Plugin modifies reply_plan.reason without blocking the reply."""

        class ModifyReasonPlugin:
            name = "modify_reason"
            version = "0.1.0"

            def setup(self, host: Any, config: dict, services: object) -> None:
                host.subscribe(HookType.on_before_reply, self._on_before)

            async def teardown(self) -> None:
                pass

            @staticmethod
            async def _on_before(ctx: PluginContext) -> PluginContext:
                if ctx.reply_plan is not None:
                    ctx.reply_plan.reason = "plugin_modified"
                return ctx

        host = DefaultPluginHost()
        host.register(ModifyReasonPlugin())
        host.enable("modify_reason")

        recorder = RecordHookPlugin()
        host.register(recorder)
        host.enable("recorder")

        dialogue = _make_dialogue(plugin_host=host)
        inbound = _make_private_inbound()

        await dialogue.handle_inbound(inbound)

        # Reply should still be sent (not blocked)
        roles = [msg.role for _, msg in dialogue._memory.appended]
        assert "assistant" in roles

        # The before_reply hook should have fired
        before_calls = [
            (h, e) for h, e in recorder.calls if h == HookType.on_before_reply
        ]
        assert len(before_calls) >= 1


class TestObservationPathHooks:
    """on_before_reply / on_after_reply in the observation path."""

    @pytest.mark.asyncio
    async def test_observation_before_reply_can_block(self) -> None:
        """Plugin blocks observation reply via on_before_reply."""
        host = DefaultPluginHost()
        host.register(ModifyReplyPlanPlugin())
        host.enable("modify_reply_plan")

        config = FakeConfigProvider(overrides={
            "ENABLE_PRIVATE_CHAT": True,
            "ENABLE_GROUP_CHAT": True,
            "ENABLE_GROUP_OBSERVE": True,
            "GROUP_OBSERVE_DELAY": 0.0,  # no debounce delay
            "GROUP_OBSERVE_COOLDOWN": 0,
            "GROUP_BURST_MERGE_WINDOW": 0,
            "GROUP_FOLLOWUP_WINDOW": 0,
            "GROUP_CHAT_CONTEXT": 6,
            "BOT_NAME": "灵轩",
        })
        llm = FakeLLMProvider()
        llm.set_chat_response("观察回复")
        llm.set_stream_tokens(["观", "察", "回", "复"])
        llm.set_judge_results([True])  # judge says "yes, reply"
        transport = FakeTransport()
        clock = FakeClock()
        log = FakeLogSink()
        sessions = InMemorySessionRepository()
        memory = FakeMemoryService()
        user_memory = FakeUserMemoryService()
        admin = FakeAdminCommandService()
        persona = PersonaService(config)
        prompt = PromptBuilder(persona, config)
        planner = ReplyPlanner(config)
        obs_store = ObservationStore(config, clock)
        executor = GroupReplyExecutor(
            prompt=prompt, llm=llm, planner=planner,
            transport=transport, sessions=sessions, config=config,
        )
        observation = ObservationService(
            store=obs_store, executor=executor, llm=llm,
            sessions=sessions, memory=memory, user_memory=user_memory,
            config=config, clock=clock, plugin_host=host,
        )

        # Seed the observation buffer so _observe has something to work with
        from lingxuan.core.observation_state import ObservationEntry
        obs_entry = ObservationEntry(
            user_id=123, nickname="测试用户", text="大家觉得呢",
            at_bot=False, reply_to_bot=False, at_user_ids=[],
            ts=clock.monotonic(),
        )
        obs_store.append_entry(456, obs_entry)

        # Run observe directly (bypass debounce)
        await observation._observe(456)

        # The observation should have been blocked by the plugin
        # (ModifyReplyPlanPlugin sets should_reply=False)
        assert len(transport.sent_messages) == 0
        assert len(transport.sent_stream_chunks) == 0

    @pytest.mark.asyncio
    async def test_observation_after_reply_fires(self) -> None:
        """on_after_reply fires after observation reply is sent."""
        recorder = RecordHookPlugin()
        host = DefaultPluginHost()
        host.register(recorder)
        host.enable("recorder")

        config = FakeConfigProvider(overrides={
            "ENABLE_PRIVATE_CHAT": True,
            "ENABLE_GROUP_CHAT": True,
            "ENABLE_GROUP_OBSERVE": True,
            "GROUP_OBSERVE_DELAY": 0.0,
            "GROUP_OBSERVE_COOLDOWN": 0,
            "GROUP_BURST_MERGE_WINDOW": 0,
            "GROUP_FOLLOWUP_WINDOW": 0,
            "GROUP_CHAT_CONTEXT": 6,
            "BOT_NAME": "灵轩",
        })
        llm = FakeLLMProvider()
        llm.set_chat_response("观察回复")
        llm.set_stream_tokens(["观", "察", "回", "复"])
        llm.set_judge_results([True])
        transport = FakeTransport()
        clock = FakeClock()
        log = FakeLogSink()
        sessions = InMemorySessionRepository()
        memory = FakeMemoryService()
        user_memory = FakeUserMemoryService()
        admin = FakeAdminCommandService()
        persona = PersonaService(config)
        prompt = PromptBuilder(persona, config)
        planner = ReplyPlanner(config)
        obs_store = ObservationStore(config, clock)
        executor = GroupReplyExecutor(
            prompt=prompt, llm=llm, planner=planner,
            transport=transport, sessions=sessions, config=config,
        )
        observation = ObservationService(
            store=obs_store, executor=executor, llm=llm,
            sessions=sessions, memory=memory, user_memory=user_memory,
            config=config, clock=clock, plugin_host=host,
        )

        # Seed the observation buffer
        from lingxuan.core.observation_state import ObservationEntry
        obs_entry = ObservationEntry(
            user_id=123, nickname="测试用户", text="大家觉得呢",
            at_bot=False, reply_to_bot=False, at_user_ids=[],
            ts=clock.monotonic(),
        )
        obs_store.append_entry(456, obs_entry)

        # Run observe directly
        await observation._observe(456)

        # on_after_reply should have been recorded for the observation path
        after_calls = [
            (h, e) for h, e in recorder.calls if h == HookType.on_after_reply
        ]
        assert len(after_calls) >= 1
