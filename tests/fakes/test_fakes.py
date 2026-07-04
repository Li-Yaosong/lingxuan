"""Tests for fake implementations: verify controllable behavior and business semantics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lingxuan.protocols.llm import ChatMessage
from lingxuan.protocols.logging import LogRecord
from lingxuan.protocols.messaging import (
    Actor,
    InboundMessage,
    OutboundChunk,
    OutboundMessage,
    ReplyTarget,
    SessionId,
)
from lingxuan.protocols.repositories import SocialEdge, StoredMessage, UserFact

from tests.fakes.clock import FakeClock
from tests.fakes.config import FakeConfigProvider
from tests.fakes.llm import FakeLLMProvider
from tests.fakes.logsink import FakeLogSink
from tests.fakes.repositories import (
    InMemorySessionRepository,
    InMemorySocialGraphRepository,
    InMemoryUserProfileRepository,
)
from tests.fakes.transport import FakeTransport


# ── FakeClock ─────────────────────────────────────────────────────────────


class TestFakeClock:
    def test_initial_now(self) -> None:
        clock = FakeClock()
        assert clock.now().year == 2025

    def test_advance_updates_now_and_monotonic(self) -> None:
        clock = FakeClock()
        clock.advance(60.0)
        assert clock.monotonic() == 60.0
        assert clock.now() == datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(
            seconds=60
        )

    async def test_sleep_records_and_advances_monotonic(
        self, fake_clock: FakeClock
    ) -> None:
        await fake_clock.sleep(2.5)
        assert fake_clock.sleep_calls == [2.5]
        assert fake_clock.monotonic() == 2.5

    def test_set_now(self) -> None:
        clock = FakeClock()
        dt = datetime(2030, 6, 15, tzinfo=timezone.utc)
        clock.set_now(dt)
        assert clock.now() == dt


# ── FakeLLMProvider ───────────────────────────────────────────────────────


class TestFakeLLMProvider:
    async def test_chat_returns_preset(self, fake_llm: FakeLLMProvider) -> None:
        fake_llm.set_chat_response("hello world")
        msgs = [ChatMessage(role="user", content="hi")]
        result = await fake_llm.chat(msgs)
        assert result == "hello world"
        assert fake_llm.chat_calls == [msgs]

    async def test_chat_returns_sequence(self, fake_llm: FakeLLMProvider) -> None:
        fake_llm.set_chat_responses(["first", "second"])
        msgs = [ChatMessage(role="user", content="hi")]
        assert await fake_llm.chat(msgs) == "first"
        assert await fake_llm.chat(msgs) == "second"

    async def test_chat_empty_when_no_preset(self, fake_llm: FakeLLMProvider) -> None:
        result = await fake_llm.chat([ChatMessage(role="user", content="hi")])
        assert result == ""

    async def test_judge_returns_preset(self, fake_llm: FakeLLMProvider) -> None:
        fake_llm.set_judge_results([True, False])
        assert await fake_llm.judge("prompt1") is True
        assert await fake_llm.judge("prompt2") is False

    async def test_judge_default_when_no_preset(
        self, fake_llm: FakeLLMProvider
    ) -> None:
        assert await fake_llm.judge("prompt", default=False) is False


# ── FakeConfigProvider ────────────────────────────────────────────────────


class TestFakeConfigProvider:
    def test_get_default(self, fake_config: FakeConfigProvider) -> None:
        assert fake_config.get("BOT_NAME") == "灵轩"

    def test_get_str(self, fake_config: FakeConfigProvider) -> None:
        assert fake_config.get_str("BOT_NAME") == "灵轩"

    def test_get_int(self, fake_config: FakeConfigProvider) -> None:
        assert fake_config.get_int("MEMORY_WINDOW") == 20

    def test_get_float(self, fake_config: FakeConfigProvider) -> None:
        assert fake_config.get_float("GROUP_OBSERVE_DELAY") == 1.5

    def test_get_bool(self, fake_config: FakeConfigProvider) -> None:
        assert fake_config.get_bool("ENABLE_PRIVATE_CHAT") is True

    def test_get_int_list(self, fake_config: FakeConfigProvider) -> None:
        assert fake_config.get_int_list("BOT_ADMINS") == []

    def test_get_unknown_raises(self, fake_config: FakeConfigProvider) -> None:
        with pytest.raises(KeyError):
            fake_config.get("NONEXISTENT_KEY")

    async def test_set_and_get(self, fake_config: FakeConfigProvider) -> None:
        await fake_config.set("BOT_NAME", "小灵")
        assert fake_config.get("BOT_NAME") == "小灵"

    async def test_set_triggers_subscribe(self, fake_config: FakeConfigProvider) -> None:
        changes: list[tuple[str, object]] = []
        fake_config.subscribe(lambda k, v: changes.append((k, v)))
        await fake_config.set("BOT_NAME", "小灵")
        assert changes == [("BOT_NAME", "小灵")]

    async def test_get_all_masks_secrets(self, fake_config: FakeConfigProvider) -> None:
        result = await fake_config.get_all(mask_secrets=True)
        # Empty secret values show "(未配置)" per mask_secret()
        assert result["OPENAI_API_KEY"] == "(未配置)"
        assert result["SECRET_KEY"] == "(未配置)"
        assert result["BOT_NAME"] == "灵轩"

    async def test_get_all_no_mask(self, fake_config: FakeConfigProvider) -> None:
        result = await fake_config.get_all(mask_secrets=False)
        assert result["OPENAI_API_KEY"] == ""

    def test_overrides(self) -> None:
        cfg = FakeConfigProvider(overrides={"BOT_NAME": "测试名"})
        assert cfg.get("BOT_NAME") == "测试名"


# ── FakeTransport ─────────────────────────────────────────────────────────


class TestFakeTransport:
    async def test_send_records_message(self, fake_transport: FakeTransport) -> None:
        target = ReplyTarget(session_id=SessionId(kind="private", peer_id=123))
        msg = OutboundMessage(target=target, chunks=[OutboundChunk(text="hi")])
        await fake_transport.send(msg)
        assert len(fake_transport.sent_messages) == 1
        assert fake_transport.sent_messages[0].chunks[0].text == "hi"

    async def test_resolve_self_id(self, fake_transport: FakeTransport) -> None:
        assert await fake_transport.resolve_self_id() == 9999

    async def test_inject_calls_handler(self, fake_transport: FakeTransport) -> None:
        received: list[InboundMessage] = []
        fake_transport.start(lambda m: _async_append(received, m))
        inbound = InboundMessage(
            session_id=SessionId(kind="private", peer_id=123),
            actor=Actor(user_id=456),
            text="hello",
        )
        await fake_transport.inject(inbound)
        assert len(received) == 1
        assert received[0].text == "hello"


async def _async_append(lst: list, item: object) -> None:
    lst.append(item)


# ── InMemorySessionRepository ─────────────────────────────────────────────


class TestInMemorySessionRepository:
    async def test_ensure_creates_session(
        self, session_repo: InMemorySessionRepository
    ) -> None:
        sid = SessionId(kind="private", peer_id=100)
        s = await session_repo.ensure(sid, nickname="Alice")
        assert s.session_id == sid
        assert s.nickname == "Alice"

    async def test_ensure_idempotent(
        self, session_repo: InMemorySessionRepository
    ) -> None:
        sid = SessionId(kind="private", peer_id=100)
        s1 = await session_repo.ensure(sid, nickname="Alice")
        s2 = await session_repo.ensure(sid, nickname="Bob")
        assert s1 is s2
        assert s1.nickname == "Alice"

    async def test_append_and_load(
        self, session_repo: InMemorySessionRepository
    ) -> None:
        sid = SessionId(kind="group", peer_id=999)
        await session_repo.append_message(
            sid, StoredMessage(role="user", content="hi", user_id=1)
        )
        await session_repo.append_message(
            sid, StoredMessage(role="assistant", content="hello")
        )
        history = await session_repo.load_history(sid)
        assert len(history) == 2
        assert history[0].content == "hi"
        assert history[1].content == "hello"

    async def test_load_with_limit(
        self, session_repo: InMemorySessionRepository
    ) -> None:
        sid = SessionId(kind="private", peer_id=1)
        for i in range(5):
            await session_repo.append_message(
                sid, StoredMessage(role="user", content=f"msg{i}")
            )
        result = await session_repo.load_history(sid, limit=2)
        assert len(result) == 2
        assert result[0].content == "msg3"
        assert result[1].content == "msg4"

    async def test_trim_to_last_removes_oldest(
        self, session_repo: InMemorySessionRepository
    ) -> None:
        sid = SessionId(kind="private", peer_id=1)
        for i in range(10):
            await session_repo.append_message(
                sid, StoredMessage(role="user", content=f"msg{i}")
            )
        removed = await session_repo.trim_to_last(sid, keep_last=4)
        assert removed == 6
        history = await session_repo.load_history(sid)
        assert len(history) == 4
        assert history[0].content == "msg6"

    async def test_trim_when_fewer_than_keep(
        self, session_repo: InMemorySessionRepository
    ) -> None:
        sid = SessionId(kind="private", peer_id=1)
        await session_repo.append_message(
            sid, StoredMessage(role="user", content="only")
        )
        removed = await session_repo.trim_to_last(sid, keep_last=10)
        assert removed == 0

    async def test_summary(self, session_repo: InMemorySessionRepository) -> None:
        sid = SessionId(kind="private", peer_id=1)
        await session_repo.ensure(sid)
        assert await session_repo.get_summary(sid) == ""
        await session_repo.set_summary(sid, "test summary")
        assert await session_repo.get_summary(sid) == "test summary"

    async def test_clear(self, session_repo: InMemorySessionRepository) -> None:
        sid = SessionId(kind="private", peer_id=1)
        await session_repo.append_message(
            sid, StoredMessage(role="user", content="hi")
        )
        await session_repo.clear(sid)
        assert await session_repo.get(sid) is None
        assert await session_repo.load_history(sid) == []

    async def test_merge_entity(
        self, session_repo: InMemorySessionRepository
    ) -> None:
        sid = SessionId(kind="group", peer_id=999)
        await session_repo.merge_entity(sid, "Alice", 111)
        await session_repo.merge_entity(sid, "Bob", 222)
        entities = await session_repo.get_entities(sid)
        assert entities == {"Alice": 111, "Bob": 222}


# ── InMemoryUserProfileRepository ─────────────────────────────────────────


class TestInMemoryUserProfileRepository:
    async def test_add_fact_and_list(
        self, user_profile_repo: InMemoryUserProfileRepository
    ) -> None:
        await user_profile_repo.add_fact(
            100,
            UserFact(id="f1", content="likes cats", category="hobby"),
        )
        facts = await user_profile_repo.list_active_facts(100)
        assert len(facts) == 1
        assert facts[0].content == "likes cats"

    async def test_duplicate_active_content_skipped(
        self, user_profile_repo: InMemoryUserProfileRepository
    ) -> None:
        await user_profile_repo.add_fact(
            100, UserFact(id="f1", content="likes cats")
        )
        await user_profile_repo.add_fact(
            100, UserFact(id="f2", content="likes cats")
        )
        facts = await user_profile_repo.list_active_facts(100)
        assert len(facts) == 1

    async def test_fact_soft_delete_over_limit(
        self, user_profile_repo: InMemoryUserProfileRepository
    ) -> None:
        repo = InMemoryUserProfileRepository(max_active_facts=3)
        base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        for i in range(5):
            await repo.add_fact(
                100,
                UserFact(
                    id=f"f{i}",
                    content=f"fact {i}",
                    learned_at=base_time + timedelta(hours=i),
                ),
            )
        facts = await repo.list_active_facts(100)
        assert len(facts) == 3
        # Oldest facts (0, 1) should be deactivated
        active_contents = {f.content for f in facts}
        assert "fact 0" not in active_contents
        assert "fact 1" not in active_contents
        assert "fact 2" in active_contents

    async def test_deactivate_facts(
        self, user_profile_repo: InMemoryUserProfileRepository
    ) -> None:
        await user_profile_repo.add_fact(
            100, UserFact(id="f1", content="a")
        )
        await user_profile_repo.add_fact(
            100, UserFact(id="f2", content="b")
        )
        await user_profile_repo.deactivate_facts(100, ["f1"])
        facts = await user_profile_repo.list_active_facts(100)
        assert len(facts) == 1
        assert facts[0].id == "f2"

    async def test_upsert_and_get(
        self, user_profile_repo: InMemoryUserProfileRepository
    ) -> None:
        from lingxuan.protocols.repositories import UserProfile

        profile = UserProfile(user_id=100, preferred_name="Alice")
        await user_profile_repo.upsert(profile)
        result = await user_profile_repo.get(100)
        assert result is not None
        assert result.preferred_name == "Alice"

    async def test_delete(self, user_profile_repo: InMemoryUserProfileRepository) -> None:
        from lingxuan.protocols.repositories import UserProfile

        await user_profile_repo.upsert(UserProfile(user_id=100))
        assert await user_profile_repo.delete(100) is True
        assert await user_profile_repo.get(100) is None
        assert await user_profile_repo.delete(100) is False


# ── InMemorySocialGraphRepository ─────────────────────────────────────────


class TestInMemorySocialGraphRepository:
    async def test_add_edge_returns_true(
        self, social_graph_repo: InMemorySocialGraphRepository
    ) -> None:
        edge = SocialEdge(
            from_user_id=1,
            to_user_id=2,
            relation="friend_of",
            label="Alice",
        )
        assert await social_graph_repo.add_edge(edge) is True

    async def test_add_duplicate_edge_returns_false(
        self, social_graph_repo: InMemorySocialGraphRepository
    ) -> None:
        edge = SocialEdge(
            from_user_id=1,
            to_user_id=2,
            relation="friend_of",
            label="Alice",
        )
        await social_graph_repo.add_edge(edge)
        assert await social_graph_repo.add_edge(edge) is False

    async def test_different_label_not_deduped(
        self, social_graph_repo: InMemorySocialGraphRepository
    ) -> None:
        e1 = SocialEdge(from_user_id=1, to_user_id=2, relation="friend_of", label="A")
        e2 = SocialEdge(from_user_id=1, to_user_id=2, relation="friend_of", label="B")
        assert await social_graph_repo.add_edge(e1) is True
        assert await social_graph_repo.add_edge(e2) is True

    async def test_name_index(
        self, social_graph_repo: InMemorySocialGraphRepository
    ) -> None:
        await social_graph_repo.index_name("Alice", 100)
        assert await social_graph_repo.resolve_name("Alice") == 100
        assert await social_graph_repo.resolve_name("Bob") is None

    async def test_edges_from(
        self, social_graph_repo: InMemorySocialGraphRepository
    ) -> None:
        await social_graph_repo.add_edge(
            SocialEdge(from_user_id=1, to_user_id=2, relation="friend_of")
        )
        await social_graph_repo.add_edge(
            SocialEdge(from_user_id=1, to_user_id=3, relation="introduced_as")
        )
        edges = await social_graph_repo.edges_from(1)
        assert len(edges) == 2

    async def test_clear(
        self, social_graph_repo: InMemorySocialGraphRepository
    ) -> None:
        await social_graph_repo.add_edge(
            SocialEdge(from_user_id=1, to_user_id=2, relation="friend_of")
        )
        await social_graph_repo.index_name("A", 1)
        await social_graph_repo.clear()
        assert await social_graph_repo.edges_from(1) == []
        assert await social_graph_repo.all_names() == {}


# ── FakeLogSink ───────────────────────────────────────────────────────────


class TestFakeLogSink:
    def test_emit_and_tail(self, fake_logsink: FakeLogSink) -> None:
        fake_logsink.emit(
            LogRecord(ts=datetime.now(timezone.utc), level="INFO", logger="test", msg="hello")
        )
        records = fake_logsink.tail()
        assert len(records) == 1
        assert records[0].msg == "hello"

    def test_tail_filter_by_level(self, fake_logsink: FakeLogSink) -> None:
        fake_logsink.emit(
            LogRecord(ts=datetime.now(timezone.utc), level="INFO", logger="t", msg="info msg")
        )
        fake_logsink.emit(
            LogRecord(ts=datetime.now(timezone.utc), level="ERROR", logger="t", msg="err msg")
        )
        assert len(fake_logsink.tail(level="ERROR")) == 1
        assert fake_logsink.tail(level="ERROR")[0].msg == "err msg"

    def test_tail_filter_by_keyword(self, fake_logsink: FakeLogSink) -> None:
        fake_logsink.emit(
            LogRecord(ts=datetime.now(timezone.utc), level="INFO", logger="t", msg="hello world")
        )
        fake_logsink.emit(
            LogRecord(ts=datetime.now(timezone.utc), level="INFO", logger="t", msg="goodbye")
        )
        assert len(fake_logsink.tail(keyword="hello")) == 1

    def test_subscribe(self, fake_logsink: FakeLogSink) -> None:
        received: list[LogRecord] = []
        unsub = fake_logsink.subscribe(lambda r: received.append(r))
        fake_logsink.emit(
            LogRecord(ts=datetime.now(timezone.utc), level="INFO", logger="t", msg="test")
        )
        assert len(received) == 1
        unsub()
        fake_logsink.emit(
            LogRecord(ts=datetime.now(timezone.utc), level="INFO", logger="t", msg="test2")
        )
        assert len(received) == 1
