"""Tests for core/observation.py — ObservationService."""

from __future__ import annotations

import asyncio
import random

import pytest

from lingxuan.core.observation import (
    ObservationService,
    is_directed_at_bot,
    is_introducing_other,
    is_knowledge_question,
    is_seeking_engagement,
)
from lingxuan.core.observation_state import ObservationStore
from lingxuan.core.persona import PersonaService
from lingxuan.core.prompting import PromptBuilder
from lingxuan.core.reply_planner import ReplyPlanner
from lingxuan.protocols.llm import ChatMessage
from lingxuan.protocols.messaging import (
    Actor,
    InboundMessage,
    ObservationEntry,
    ReplyTarget,
    SessionId,
)
from lingxuan.protocols.repositories import StoredMessage
from tests.fakes.clock import FakeClock
from tests.fakes.config import FakeConfigProvider
from tests.fakes.llm import FakeLLMProvider
from tests.fakes.repositories import InMemorySessionRepository
from tests.fakes.transport import FakeTransport


# ── helpers ──────────────────────────────────────────────────────────────


def _make_service(
    **config_overrides: object,
) -> tuple[ObservationService, FakeClock, FakeLLMProvider, FakeTransport, ObservationStore, InMemorySessionRepository]:
    config = FakeConfigProvider(config_overrides)
    clock = FakeClock()
    store = ObservationStore(config=config, clock=clock)
    llm = FakeLLMProvider()
    sessions = InMemorySessionRepository()
    transport = FakeTransport()
    persona = PersonaService(config)
    prompt = PromptBuilder(persona, config)
    planner = ReplyPlanner(config, rng=random.Random(42))
    svc = ObservationService(
        store=store,
        llm=llm,
        prompt=prompt,
        planner=planner,
        sessions=sessions,
        transport=transport,
        config=config,
        clock=clock,
    )
    return svc, clock, llm, transport, store, sessions


def _inbound(
    user_id: int = 1,
    nickname: str = "小明",
    text: str = "你好",
    group_id: int = 100,
    at_bot: bool = False,
    reply_to_bot: bool = False,
    at_user_ids: list[int] | None = None,
) -> InboundMessage:
    return InboundMessage(
        session_id=SessionId(kind="group", peer_id=group_id),
        actor=Actor(user_id=user_id, nickname=nickname),
        text=text,
        at_bot=at_bot,
        reply_to_bot=reply_to_bot,
        at_user_ids=at_user_ids or [],
        group_id=group_id,
    )


def _entry(
    user_id: int = 1,
    nickname: str = "小明",
    text: str = "你好",
    group_id: int = 100,
    at_bot: bool = False,
    reply_to_bot: bool = False,
    at_user_ids: list[int] | None = None,
    is_bot: bool = False,
    ts: float = 0.0,
) -> ObservationEntry:
    return ObservationEntry(
        user_id=user_id,
        nickname=nickname,
        text=text,
        at_bot=at_bot,
        reply_to_bot=reply_to_bot,
        at_user_ids=at_user_ids or [],
        is_bot=is_bot,
        ts=ts,
    )


# ── pure rule function tests ─────────────────────────────────────────────


class TestIsKnowledgeQuestion:
    def test_matches_known_hints(self):
        assert is_knowledge_question("你知道吗灵轩是谁") is True
        assert is_knowledge_question("还记得我说过什么吗") is True

    def test_no_match_random_text(self):
        assert is_knowledge_question("今天天气不错") is False

    def test_empty_text(self):
        assert is_knowledge_question("") is False
        assert is_knowledge_question("   ") is False


class TestIsIntroducingOther:
    def test_matches_intro_hints(self):
        e = _entry(text="这位是小红", at_user_ids=[222])
        assert is_introducing_other(e, "灵轩") is True

    def test_no_match_at_bot(self):
        e = _entry(text="这位是小红", at_bot=True, at_user_ids=[222])
        assert is_introducing_other(e, "灵轩") is False

    def test_no_match_no_at_ids(self):
        e = _entry(text="这位是小红")
        assert is_introducing_other(e, "灵轩") is False

    def test_no_match_no_hint(self):
        e = _entry(text="你好啊", at_user_ids=[222])
        assert is_introducing_other(e, "灵轩") is False


class TestIsDirectedAtBot:
    def test_matches_name_mention(self):
        assert is_directed_at_bot("灵轩你好", "灵轩") is True

    def test_matches_directed_hints_with_you(self):
        assert is_directed_at_bot("问你一个问题", "灵轩") is True

    def test_hint_requires_you(self):
        # "回复一下" matches hint but lacks "你" → False
        assert is_directed_at_bot("回复一下", "灵轩") is False
        # "叫你一下" matches hint AND has "你" → True
        assert is_directed_at_bot("叫你一下", "灵轩") is True
        # No hint at all
        assert is_directed_at_bot("天气真好", "灵轩") is False

    def test_empty_text(self):
        assert is_directed_at_bot("", "灵轩") is False


class TestIsSeekingEngagement:
    def test_matches_emotional(self):
        assert is_seeking_engagement("好孤独啊") is True
        assert is_seeking_engagement("晚安") is True

    def test_matches_asking_with_marker(self):
        assert is_seeking_engagement("有人能帮我吗") is True
        assert is_seeking_engagement("怎么办呢") is True

    def test_no_match_asking_without_marker(self):
        assert is_seeking_engagement("怎么办") is False

    def test_empty_text(self):
        assert is_seeking_engagement("") is False


# ── short-circuit tests ──────────────────────────────────────────────────


class TestShortcircuitJudge:
    def test_at_bot_shortcircuits(self):
        svc, clock, llm, transport, store, _ = _make_service()
        store.append_entry(100, _entry(user_id=1, nickname="小明", text="你好", at_bot=True))
        hit, reason = svc._should_shortcircuit_judge(100)
        assert hit is True
        assert reason == "at_bot"

    def test_reply_to_bot_shortcircuits(self):
        svc, clock, llm, transport, store, _ = _make_service()
        store.append_entry(100, _entry(user_id=1, nickname="小明", text="你好", reply_to_bot=True))
        hit, reason = svc._should_shortcircuit_judge(100)
        assert hit is True
        assert reason == "reply_to_bot"

    def test_name_mention_shortcircuits(self):
        svc, clock, llm, transport, store, _ = _make_service()
        store.append_entry(100, _entry(user_id=1, nickname="小明", text="灵轩你好"))
        hit, reason = svc._should_shortcircuit_judge(100)
        assert hit is True
        assert reason == "name_mention"

    def test_directed_request_shortcircuits(self):
        svc, clock, llm, transport, store, _ = _make_service()
        store.append_entry(100, _entry(user_id=1, nickname="小明", text="问你一个问题"))
        hit, reason = svc._should_shortcircuit_judge(100)
        assert hit is True
        assert reason == "directed_request"

    def test_engagement_shortcircuits(self):
        svc, clock, llm, transport, store, _ = _make_service()
        store.append_entry(100, _entry(user_id=1, nickname="小明", text="好孤独啊"))
        hit, reason = svc._should_shortcircuit_judge(100)
        assert hit is True
        assert reason == "engagement"

    def test_intro_other_shortcircuits(self):
        svc, clock, llm, transport, store, _ = _make_service()
        store.append_entry(100, _entry(user_id=1, nickname="小明", text="这位是小红", at_user_ids=[222]))
        hit, reason = svc._should_shortcircuit_judge(100)
        assert hit is True
        assert reason == "intro_other"

    def test_followup_shortcircuits(self):
        svc, clock, llm, transport, store, _ = _make_service()
        clock = FakeClock(monotonic_start=100.0)
        config = FakeConfigProvider()
        store = ObservationStore(config=config, clock=clock)
        # Bot replied at t=100
        store.append_bot_message(100, "你好呀")
        # Same user follow-up within window
        store.append_entry(100, _entry(user_id=1, nickname="小明", text="嗯嗯", ts=105.0))
        # Mark last reply user
        store.state(100).last_reply_user_id = 1

        persona = PersonaService(config)
        prompt = PromptBuilder(persona, config)
        planner = ReplyPlanner(config, rng=random.Random(42))
        llm = FakeLLMProvider()
        sessions = InMemorySessionRepository()
        transport = FakeTransport()
        svc = ObservationService(store, llm, prompt, planner, sessions, transport, config, clock)

        hit, reason = svc._should_shortcircuit_judge(100)
        assert hit is True
        assert reason == "followup"

    def test_no_shortcircuit_for_normal_chat(self):
        svc, clock, llm, transport, store, _ = _make_service()
        store.append_entry(100, _entry(user_id=1, nickname="小明", text="今天天气不错"))
        hit, reason = svc._should_shortcircuit_judge(100)
        assert hit is False
        assert reason == ""


# ── cooldown tests ───────────────────────────────────────────────────────


class TestCooldown:
    def test_cooldown_blocks_non_bypass(self):
        svc, clock, llm, transport, store, _ = _make_service(GROUP_OBSERVE_COOLDOWN=30.0)
        # Trigger a reply at t=0, setting cooldown_until=30
        store.append_entry(100, _entry(user_id=1, at_bot=True, text="你好"))
        svc.mark_last_trigger(100, reply_user_id=1)
        assert svc.is_in_cooldown(100) is True

    def test_bypass_overrides_cooldown(self):
        svc, clock, llm, transport, store, _ = _make_service(GROUP_OBSERVE_COOLDOWN=30.0)
        svc.mark_last_trigger(100, reply_user_id=1)
        # @bot message should bypass cooldown
        store.append_entry(100, _entry(user_id=2, nickname="小红", text="灵轩", at_bot=True))
        assert svc.should_bypass_cooldown(100) is True

    def test_cooldown_expires_after_time(self):
        svc, clock, llm, transport, store, _ = _make_service(GROUP_OBSERVE_COOLDOWN=30.0)
        svc.mark_last_trigger(100, reply_user_id=1)
        assert svc.is_in_cooldown(100) is True
        clock.advance(31.0)
        assert svc.is_in_cooldown(100) is False


# ── skip observe tests ───────────────────────────────────────────────────


class TestShouldSkipObserve:
    def test_skip_when_at_others_only(self):
        svc, clock, llm, transport, store, _ = _make_service()
        store.append_entry(100, _entry(user_id=1, nickname="小明", text="你好", at_user_ids=[222]))
        assert svc.should_skip_observe(100) is True

    def test_not_skip_when_at_bot(self):
        svc, clock, llm, transport, store, _ = _make_service()
        store.append_entry(100, _entry(user_id=1, nickname="小明", text="你好", at_bot=True))
        assert svc.should_skip_observe(100) is False

    def test_not_skip_when_introducing(self):
        svc, clock, llm, transport, store, _ = _make_service()
        store.append_entry(100, _entry(user_id=1, nickname="小明", text="这位是小红", at_user_ids=[222]))
        assert svc.should_skip_observe(100) is False


# ── reply target tests ──────────────────────────────────────────────────


class TestGetReplyTarget:
    def test_at_bot_returns_sender(self):
        svc, clock, llm, transport, store, _ = _make_service()
        store.append_entry(100, _entry(user_id=1, nickname="小明", text="你好", at_bot=True))
        target = svc.get_reply_target(100)
        assert target == (1, "小明")

    def test_at_others_returns_first_at_target(self):
        svc, clock, llm, transport, store, _ = _make_service()
        store.remember_nickname(100, 222, "小红")
        store.append_entry(100, _entry(user_id=1, nickname="小明", text="你好", at_user_ids=[222]))
        target = svc.get_reply_target(100)
        assert target == (222, "小红")

    def test_no_at_returns_sender(self):
        svc, clock, llm, transport, store, _ = _make_service()
        store.append_entry(100, _entry(user_id=1, nickname="小明", text="你好"))
        target = svc.get_reply_target(100)
        assert target == (1, "小明")

    def test_empty_buffer_returns_none(self):
        svc, clock, llm, transport, store, _ = _make_service()
        assert svc.get_reply_target(100) is None


# ── format observation tests ────────────────────────────────────────────


class TestFormatObservation:
    def test_basic_format(self):
        svc, clock, llm, transport, store, _ = _make_service()
        store.append_entry(100, _entry(user_id=1, nickname="小明", text="你好", ts=1.0))
        obs = svc.format_observation(100)
        assert obs == "[小明]: 你好"

    def test_bot_entry(self):
        svc, clock, llm, transport, store, _ = _make_service()
        store.append_entry(100, _entry(user_id=1, nickname="小明", text="你好", ts=1.0))
        store.append_bot_message(100, "你好呀")
        obs = svc.format_observation(100)
        assert "[灵轩]: 你好呀" in obs

    def test_at_bot_marker(self):
        svc, clock, llm, transport, store, _ = _make_service()
        store.append_entry(100, _entry(user_id=1, nickname="小明", text="你好", at_bot=True, ts=1.0))
        obs = svc.format_observation(100)
        assert "@灵轩" in obs

    def test_burst_merge(self):
        svc, clock, llm, transport, store, _ = _make_service(GROUP_BURST_MERGE_WINDOW=10.0)
        store.append_entry(100, _entry(user_id=1, nickname="小明", text="你好", ts=1.0))
        store.append_entry(100, _entry(user_id=1, nickname="小明", text="最近怎么样", ts=3.0))
        obs = svc.format_observation(100)
        assert "你好 / 最近怎么样" in obs


# ── full orchestration tests ────────────────────────────────────────────


class TestObserveShortcircuitNoJudge:
    """Short-circuit hit → should NOT call llm.judge."""

    @pytest.mark.asyncio
    async def test_at_bot_shortcircuit_skips_judge(self):
        svc, clock, llm, transport, store, sessions = _make_service()
        llm.set_stream_tokens(["你好呀", "！"])

        msg = _inbound(user_id=1, nickname="小明", text="灵轩你好", at_bot=True, group_id=100)
        await svc.on_group_message(msg)

        # Trigger the debounce
        clock.advance(2.0)  # past the GROUP_OBSERVE_DELAY (1.5s default)
        # Let the debounce task run
        await asyncio.sleep(0)

        # Short-circuit should NOT call judge
        assert len(llm.judge_calls) == 0


class TestCooldownBlocksNonBypass:
    """Cooldown blocks non-bypass messages; bypass overrides."""

    @pytest.mark.asyncio
    async def test_cooldown_blocks_normal_message(self):
        svc, clock, llm, transport, store, sessions = _make_service(
            GROUP_OBSERVE_COOLDOWN=30.0,
            GROUP_OBSERVE_DELAY=0.01,
        )

        # First: trigger a reply to set cooldown
        llm.set_judge_results([True])
        llm.set_stream_tokens(["好的"])

        msg1 = _inbound(user_id=1, nickname="小明", text="你好灵轩", group_id=100)
        # Manually set cooldown state
        svc.mark_last_trigger(100, reply_user_id=1)
        clock.advance(5.0)  # still in cooldown (30s total)

        # Now send a non-bypass message
        msg2 = _inbound(user_id=2, nickname="小红", text="今天天气不错", group_id=100)
        await svc.on_group_message(msg2)

        clock.advance(0.02)  # past debounce delay
        await asyncio.sleep(0)

        # Should NOT reply because of cooldown
        assert len(llm.judge_calls) == 0
        assert len(transport.sent_stream_chunks) == 0

    @pytest.mark.asyncio
    async def test_at_bot_bypasses_cooldown(self):
        svc, clock, llm, transport, store, sessions = _make_service(
            GROUP_OBSERVE_COOLDOWN=30.0,
            GROUP_OBSERVE_DELAY=0.01,
        )

        svc.mark_last_trigger(100, reply_user_id=1)
        clock.advance(5.0)

        # @bot should bypass cooldown
        store.append_entry(100, _entry(user_id=2, nickname="小红", text="你好", at_bot=True))
        assert svc.should_bypass_cooldown(100) is True


class TestJudgeNoDoesNotReply:
    """Judge returns no → no reply sent."""

    @pytest.mark.asyncio
    async def test_judge_no_no_reply(self):
        svc, clock, llm, transport, store, sessions = _make_service(
            GROUP_OBSERVE_DELAY=0.01,
        )
        llm.set_judge_results([False])

        msg = _inbound(user_id=1, nickname="小明", text="大家好", group_id=100)
        await svc.on_group_message(msg)

        clock.advance(0.02)
        await asyncio.sleep(0)

        # Judge was called and returned False
        assert len(llm.judge_calls) == 1
        assert len(transport.sent_stream_chunks) == 0


class TestJudgeYesReplyAndSend:
    """Judge returns yes → reply sent as stream chunks."""

    @pytest.mark.asyncio
    async def test_judge_yes_reply_sent(self):
        svc, clock, llm, transport, store, sessions = _make_service(
            GROUP_OBSERVE_DELAY=0.01,
        )
        llm.set_judge_results([True])
        llm.set_stream_tokens(["你好呀", "！"])

        # "大家好" doesn't trigger any shortcircuit rule, so it goes to judge
        msg = _inbound(user_id=1, nickname="小明", text="大家好", group_id=100)
        await svc.on_group_message(msg)

        clock.advance(0.02)
        # Need to actually let async tasks complete
        for _ in range(10):
            await asyncio.sleep(0)

        # Judge was called
        assert len(llm.judge_calls) == 1
        # Reply sent via transport
        assert len(transport.sent_stream_chunks) >= 1


class TestDebounce:
    """Multiple messages in debounce window → only one observation."""

    @pytest.mark.asyncio
    async def test_debounce_merges_multiple_messages(self):
        svc, clock, llm, transport, store, sessions = _make_service(
            GROUP_OBSERVE_DELAY=1.5,
        )
        llm.set_judge_results([True])
        llm.set_stream_tokens(["好的"])

        # Send 3 messages quickly
        for i in range(3):
            msg = _inbound(user_id=1, nickname="小明", text=f"消息{i}", group_id=100)
            await svc.on_group_message(msg)
            clock.advance(0.3)  # each within debounce window

        # Advance past debounce delay
        clock.advance(1.5)
        for _ in range(10):
            await asyncio.sleep(0)

        # Only one judge call (debounce merged)
        assert len(llm.judge_calls) == 1


class TestLocalSkip:
    """should_skip_reply_locally skips judge for teasing messages."""

    @pytest.mark.asyncio
    async def test_local_skip_no_judge(self):
        svc, clock, llm, transport, store, sessions = _make_service(
            GROUP_OBSERVE_DELAY=0.01,
        )

        msg = _inbound(user_id=1, nickname="小明", text="哈哈", group_id=100)
        await svc.on_group_message(msg)

        clock.advance(0.02)
        for _ in range(10):
            await asyncio.sleep(0)

        # Not shortcircuit, not bypass → goes to local skip check
        # "哈哈" ≤6 chars, no question mark, contains "哈" → local skip
        # So no judge call
        assert len(llm.judge_calls) == 0
        assert len(transport.sent_stream_chunks) == 0


class TestMarkObserved:
    """After observation, mark_observed should be called."""

    @pytest.mark.asyncio
    async def test_mark_observed_after_judge_no(self):
        svc, clock, llm, transport, store, sessions = _make_service(
            GROUP_OBSERVE_DELAY=0.01,
        )
        llm.set_judge_results([False])

        msg = _inbound(user_id=1, nickname="小明", text="大家好", group_id=100)
        await svc.on_group_message(msg)

        clock.advance(0.02)
        for _ in range(10):
            await asyncio.sleep(0)

        # After observation, has_new_since_observe should be False
        assert store.has_new_since_observe(100) is False
