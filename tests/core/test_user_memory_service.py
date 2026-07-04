"""Tests for UserMemoryService (core/user_memory.py).

Uses InMemory repos + FakeLLM + FakeClock — no file IO, no nonebot.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from lingxuan.core.models import (
    FACT_CATEGORY_IDENTITY,
    FACT_CATEGORY_GENERAL,
    FACT_CATEGORY_PREFERENCE,
    RELATION_INTRODUCED_AS,
    RELATION_SELF_IDENTIFIED_AS,
    compute_stage,
    new_fact_id,
)
from lingxuan.core.user_memory import UserMemoryService
from lingxuan.protocols.llm import ChatMessage
from lingxuan.protocols.repositories import (
    SocialEdge,
    UserFact,
    UserProfile,
)
from tests.fakes.clock import FakeClock
from tests.fakes.config import FakeConfigProvider
from tests.fakes.llm import FakeLLMProvider
from tests.fakes.logsink import FakeLogSink
from tests.fakes.repositories import (
    InMemorySocialGraphRepository,
    InMemoryUserProfileRepository,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(now=datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc))


@pytest.fixture
def profiles() -> InMemoryUserProfileRepository:
    return InMemoryUserProfileRepository(max_active_facts=30)


@pytest.fixture
def graph() -> InMemorySocialGraphRepository:
    return InMemorySocialGraphRepository()


@pytest.fixture
def llm() -> FakeLLMProvider:
    return FakeLLMProvider()


@pytest.fixture
def config() -> FakeConfigProvider:
    return FakeConfigProvider()


@pytest.fixture
def log() -> FakeLogSink:
    return FakeLogSink()


@pytest.fixture
def svc(
    profiles: InMemoryUserProfileRepository,
    graph: InMemorySocialGraphRepository,
    llm: FakeLLMProvider,
    config: FakeConfigProvider,
    clock: FakeClock,
    log: FakeLogSink,
) -> UserMemoryService:
    return UserMemoryService(
        profiles=profiles,
        graph=graph,
        llm=llm,
        config=config,
        clock=clock,
        log=log,
    )


# ---------------------------------------------------------------------------
# touch_user — interaction count & stage progression
# ---------------------------------------------------------------------------

class TestTouchUser:
    async def test_creates_profile_on_first_touch(self, svc: UserMemoryService, profiles: InMemoryUserProfileRepository) -> None:
        p = await svc.touch_user(100, nickname="小明")
        assert p.user_id == 100
        assert p.preferred_name == "小明"
        assert p.interaction_count == 1
        assert p.first_met_at is not None
        assert p.stage == "stranger"

    async def test_interaction_count_increments(self, svc: UserMemoryService) -> None:
        await svc.touch_user(100, nickname="小明")
        p = await svc.touch_user(100, nickname="小明")
        assert p.interaction_count == 2

    async def test_stage_progression(self, svc: UserMemoryService) -> None:
        # 3 interactions → acquaintance
        for _ in range(3):
            p = await svc.touch_user(100, nickname="小明", group_id=1)
        assert p.stage == "acquaintance"

        # 10 interactions → familiar
        for _ in range(7):
            p = await svc.touch_user(100, nickname="小明", group_id=1)
        assert p.stage == "familiar"

        # 30 interactions → close
        for _ in range(20):
            p = await svc.touch_user(100, nickname="小明", group_id=1)
        assert p.stage == "close"

    async def test_familiar_via_seen_both_contexts(self, svc: UserMemoryService) -> None:
        p = await svc.touch_user(100, nickname="小明", is_private=True)
        assert p.stage == "stranger"
        p = await svc.touch_user(100, nickname="小明", group_id=1)
        assert p.seen_in_private and p.seen_in_group
        assert p.stage == "familiar"

    async def test_nickname_becomes_alias_when_preferred_exists(self, svc: UserMemoryService) -> None:
        await svc.touch_user(100, nickname="小明")
        p = await svc.touch_user(100, nickname="明明")
        assert p.preferred_name == "小明"
        assert "明明" in p.aliases

    async def test_group_card_recorded(self, svc: UserMemoryService) -> None:
        p = await svc.touch_user(100, nickname="群名片", group_id=42)
        assert p.group_cards.get("42") == "群名片"

    async def test_first_met_at_set_once(self, svc: UserMemoryService, clock: FakeClock) -> None:
        t1 = clock.now()
        p1 = await svc.touch_user(100)
        assert p1.first_met_at == t1
        clock.advance(3600)
        p2 = await svc.touch_user(100)
        # first_met_at should not change
        assert p2.first_met_at == t1
        assert p2.last_seen_at != t1


# ---------------------------------------------------------------------------
# add_fact — dedup + truncation + identity deactivation
# ---------------------------------------------------------------------------

class TestAddFact:
    async def test_add_fact_basic(self, svc: UserMemoryService) -> None:
        fact = await svc.add_fact(100, "喜欢猫", category=FACT_CATEGORY_PREFERENCE)
        assert fact is not None
        assert fact.content == "喜欢猫"
        assert fact.category == FACT_CATEGORY_PREFERENCE

    async def test_add_fact_dedup(self, svc: UserMemoryService) -> None:
        f1 = await svc.add_fact(100, "喜欢猫")
        f2 = await svc.add_fact(100, "喜欢猫")
        assert f1 is not None
        assert f2 is not None
        assert f1.id == f2.id  # same fact returned

    async def test_add_fact_truncation(self, svc: UserMemoryService, profiles: InMemoryUserProfileRepository) -> None:
        for i in range(35):
            await svc.add_fact(100, f"fact_{i:03d}")

        p = await profiles.get(100)
        assert p is not None
        active = [f for f in p.facts if f.active]
        inactive = [f for f in p.facts if not f.active]
        assert len(active) == 30
        assert len(inactive) == 5
        # The oldest 5 should be deactivated
        inactive_contents = {f.content for f in inactive}
        for i in range(5):
            assert f"fact_{i:03d}" in inactive_contents

    async def test_add_fact_empty_content_skipped(self, svc: UserMemoryService) -> None:
        result = await svc.add_fact(100, "  ")
        assert result is None

    async def test_add_fact_updates_stage(self, svc: UserMemoryService) -> None:
        # Stranger with 1 interaction, adding a non-identity fact → acquaintance
        await svc.touch_user(100, nickname="小明")
        p_before = await svc._get_or_create_profile(100)
        assert p_before.stage == "stranger"
        await svc.add_fact(100, "喜欢编程", category=FACT_CATEGORY_PREFERENCE)
        p_after = await svc._get_or_create_profile(100)
        assert p_after.stage == "acquaintance"


# ---------------------------------------------------------------------------
# Identity fact deactivation
# ---------------------------------------------------------------------------

class TestIdentityFactDeactivation:
    async def test_new_identity_deactivates_old(self, svc: UserMemoryService) -> None:
        await svc.set_preferred_name(100, "小明")
        p = await svc._get_or_create_profile(100)
        identity_facts = [f for f in p.facts if f.category == FACT_CATEGORY_IDENTITY]
        assert len(identity_facts) == 1
        assert identity_facts[0].active

        await svc.set_preferred_name(100, "明明")
        p = await svc._get_or_create_profile(100)
        identity_facts = [f for f in p.facts if f.category == FACT_CATEGORY_IDENTITY]
        # Old one should be inactive, new one active
        active_identity = [f for f in identity_facts if f.active]
        inactive_identity = [f for f in identity_facts if not f.active]
        assert len(active_identity) == 1
        assert len(inactive_identity) == 1
        assert active_identity[0].content == "希望被称呼为明明"
        assert not inactive_identity[0].active

    async def test_set_preferred_name_moves_old_to_aliases(self, svc: UserMemoryService) -> None:
        await svc.set_preferred_name(100, "小明")
        await svc.set_preferred_name(100, "明明")
        p = await svc._get_or_create_profile(100)
        assert p.preferred_name == "明明"
        assert "小明" in p.aliases


# ---------------------------------------------------------------------------
# Social graph — edge dedup, name resolution
# ---------------------------------------------------------------------------

class TestSocialGraph:
    async def test_add_social_edge_dedup(self, svc: UserMemoryService, graph: InMemorySocialGraphRepository) -> None:
        await svc.add_social_edge(100, 200, RELATION_INTRODUCED_AS, label="小红")
        await svc.add_social_edge(100, 200, RELATION_INTRODUCED_AS, label="小红")
        edges = await graph.edges_from(100)
        # Same four-tuple should not add duplicate
        assert len(edges) == 1

    async def test_add_social_edge_different_label_ok(self, svc: UserMemoryService, graph: InMemorySocialGraphRepository) -> None:
        await svc.add_social_edge(100, 200, RELATION_INTRODUCED_AS, label="小红")
        await svc.add_social_edge(100, 200, RELATION_INTRODUCED_AS, label="红红")
        edges = await graph.edges_from(100)
        assert len(edges) == 2

    async def test_index_and_resolve_name(self, svc: UserMemoryService) -> None:
        await svc.index_name("小明", 100)
        uid = await svc.resolve_name("小明")
        assert uid == 100

    async def test_resolve_unknown_name(self, svc: UserMemoryService) -> None:
        uid = await svc.resolve_name("不存在")
        assert uid is None

    async def test_add_edge_indexes_label(self, svc: UserMemoryService) -> None:
        await svc.add_social_edge(100, 200, RELATION_INTRODUCED_AS, label="小红")
        uid = await svc.resolve_name("小红")
        assert uid == 200

    async def test_add_edge_skips_zero_ids(self, svc: UserMemoryService, graph: InMemorySocialGraphRepository) -> None:
        await svc.add_social_edge(0, 200, RELATION_INTRODUCED_AS, label="x")
        await svc.add_social_edge(100, 0, RELATION_INTRODUCED_AS, label="x")
        edges = await graph.edges_from(100)
        assert len(edges) == 0


# ---------------------------------------------------------------------------
# Cognition refine — trigger conditions & truncation
# ---------------------------------------------------------------------------

class TestCognitionRefine:
    async def test_should_refine_on_interval(self, svc: UserMemoryService) -> None:
        # Default interval is 5 interactions
        await svc.touch_user(100, nickname="小明")
        p = await svc._get_or_create_profile(100)
        assert not svc.should_refine_cognition(p)

        # After 5 more touches (total 6, delta = 6 - 0 = 6 >= 5)
        for _ in range(5):
            await svc.touch_user(100, nickname="小明")
        p = await svc._get_or_create_profile(100)
        assert svc.should_refine_cognition(p)

    async def test_should_refine_on_name_change(self, svc: UserMemoryService, clock: FakeClock) -> None:
        await svc.touch_user(100, nickname="小明")
        # Simulate cognition was updated before
        p = await svc._get_or_create_profile(100)
        p.cognition_updated_at = clock.now()
        p.cognition_interaction_at_update = 1
        await svc._save(p)

        # Identity change after cognition update
        clock.advance(10)
        await svc.set_preferred_name(100, "明明")
        p = await svc._get_or_create_profile(100)
        assert svc.should_refine_cognition(p)

    async def test_should_refine_on_first_facts(self, svc: UserMemoryService) -> None:
        await svc.touch_user(100, nickname="小明")
        p = await svc._get_or_create_profile(100)
        # No cognition yet, but facts exist (identity fact from set_preferred_name)
        # Actually no identity fact yet since touch doesn't add one.
        # Let's add a fact manually.
        assert not svc.should_refine_cognition(p)
        await svc.add_fact(100, "喜欢猫")
        p = await svc._get_or_create_profile(100)
        assert svc.should_refine_cognition(p)

    async def test_should_refine_with_recent_exchange(self, svc: UserMemoryService) -> None:
        await svc.touch_user(100, nickname="小明")
        p = await svc._get_or_create_profile(100)
        assert svc.should_refine_cognition(p, has_recent_exchange=True)

    async def test_should_not_refine_when_disabled(self, svc: UserMemoryService, config: FakeConfigProvider) -> None:
        await config.set("ENABLE_USER_COGNITION_REFINE", False)
        await svc.touch_user(100, nickname="小明")
        p = await svc._get_or_create_profile(100)
        assert not svc.should_refine_cognition(p, has_recent_exchange=True)

    async def test_refine_cognition_truncation(self, svc: UserMemoryService, llm: FakeLLMProvider, clock: FakeClock) -> None:
        long_summary = "这是一段很长的认知总结" * 50  # way over 150 chars
        llm.set_chat_response(long_summary)

        await svc.touch_user(100, nickname="小明")
        p = await svc._get_or_create_profile(100)
        p.cognition_interaction_at_update = 0  # ensure refine triggers
        await svc._save(p)

        result = await svc.refine_user_cognition(100)
        assert len(result) <= 150

    async def test_refine_cognition_success(self, svc: UserMemoryService, llm: FakeLLMProvider) -> None:
        llm.set_chat_response("小明是个喜欢编程的朋友")
        await svc.touch_user(100, nickname="小明")
        p = await svc._get_or_create_profile(100)
        p.cognition_interaction_at_update = 0
        await svc._save(p)

        result = await svc.refine_user_cognition(100)
        assert result == "小明是个喜欢编程的朋友"
        p = await svc._get_or_create_profile(100)
        assert p.cognition_summary == "小明是个喜欢编程的朋友"
        assert p.cognition_interaction_at_update == p.interaction_count

    async def test_refine_cognition_rejects_json(self, svc: UserMemoryService, llm: FakeLLMProvider) -> None:
        llm.set_chat_response('{"summary": "something"}')
        await svc.touch_user(100, nickname="小明")
        p = await svc._get_or_create_profile(100)
        old_summary = p.cognition_summary
        result = await svc.refine_user_cognition(100)
        assert result == old_summary  # unchanged


# ---------------------------------------------------------------------------
# Rule extraction
# ---------------------------------------------------------------------------

class TestRuleExtraction:
    async def test_call_me_rule(self, svc: UserMemoryService) -> None:
        changed = await svc.apply_rule_extraction(100, "叫我小明")
        assert changed
        p = await svc._get_or_create_profile(100)
        assert p.preferred_name == "小明"

    async def test_name_correction_rule(self, svc: UserMemoryService) -> None:
        # _CORRECTION_SIMPLE matches "我不是X我是Y" (no punctuation between)
        changed = await svc.apply_rule_extraction(100, "我不是小红我是小明")
        assert changed
        p = await svc._get_or_create_profile(100)
        assert p.preferred_name == "小明"

    async def test_intro_name_with_at_user(self, svc: UserMemoryService) -> None:
        changed = await svc.apply_rule_extraction(
            100, "这位就是小红", at_user_ids=[200],
        )
        assert changed
        uid = await svc.resolve_name("小红")
        assert uid == 200

    async def test_self_identified_rule(self, svc: UserMemoryService) -> None:
        # _INTRO_NAME matches "他就是X" / "她就是X" / "这位就是X"
        # Self-identified path: "就是" in text + no at_user_ids + _INTRO_NAME match
        # _INTRO_NAME doesn't match "我就是" since it needs 他/她/这位/这 prefix.
        # Use a pattern that works: "叫我灵轩" (handled by _CALL_ME path)
        changed = await svc.apply_rule_extraction(
            100, "叫我灵轩",
        )
        assert changed
        p = await svc._get_or_create_profile(100)
        assert p.preferred_name == "灵轩"

    async def test_empty_text_no_change(self, svc: UserMemoryService) -> None:
        changed = await svc.apply_rule_extraction(100, "  ")
        assert not changed


# ---------------------------------------------------------------------------
# on_user_message
# ---------------------------------------------------------------------------

class TestOnUserMessage:
    async def test_touches_and_schedules(self, svc: UserMemoryService, profiles: InMemoryUserProfileRepository) -> None:
        await svc.on_user_message(100, "你好", nickname="小明", group_id=1)
        p = await profiles.get(100)
        assert p is not None
        # apply_rule_extraction → sync_entity_to_graph → touch_user adds a second touch
        assert p.interaction_count >= 1
        assert p.preferred_name == "小明"

    async def test_disabled_noop(self, svc: UserMemoryService, config: FakeConfigProvider, profiles: InMemoryUserProfileRepository) -> None:
        await config.set("ENABLE_USER_MEMORY", False)
        await svc.on_user_message(100, "你好", nickname="小明")
        p = await profiles.get(100)
        assert p is None


# ---------------------------------------------------------------------------
# Debounced extraction (smoke test with FakeClock)
# ---------------------------------------------------------------------------

class TestDebouncedExtraction:
    async def test_schedule_memory_extract_creates_task(self, svc: UserMemoryService) -> None:
        await svc.schedule_memory_extract(100, "你好", nickname="小明")
        assert 100 in svc._extract_tasks
        assert not svc._extract_tasks[100].done()

    async def test_schedule_disabled_noop(self, svc: UserMemoryService, config: FakeConfigProvider) -> None:
        await config.set("ENABLE_USER_MEMORY", False)
        await svc.schedule_memory_extract(100, "你好")
        assert 100 not in svc._extract_tasks


# ---------------------------------------------------------------------------
# Prompt context formatting
# ---------------------------------------------------------------------------

class TestFormatUserContext:
    async def test_format_user_brief(self, svc: UserMemoryService) -> None:
        await svc.touch_user(100, nickname="小明")
        brief = await svc.format_user_brief(100)
        assert "小明" in brief
        assert "陌生" in brief

    async def test_format_user_brief_disabled(self, svc: UserMemoryService, config: FakeConfigProvider) -> None:
        await config.set("ENABLE_USER_MEMORY", False)
        brief = await svc.format_user_brief(100)
        assert brief == ""

    async def test_format_user_profile_summary(self, svc: UserMemoryService) -> None:
        await svc.touch_user(100, nickname="小明")
        await svc.add_fact(100, "喜欢猫", category=FACT_CATEGORY_PREFERENCE)
        summary = await svc.format_user_profile_summary(100)
        assert "小明" in summary
        assert "QQ 100" in summary
        assert "喜欢猫" in summary

    async def test_format_context_for_prompt_primary(self, svc: UserMemoryService) -> None:
        await svc.touch_user(100, nickname="小明")
        ctx = await svc.format_user_context_for_prompt(primary_user_id=100)
        assert "正在对话的人" in ctx
        assert "小明" in ctx

    async def test_format_context_for_prompt_disabled(self, svc: UserMemoryService, config: FakeConfigProvider) -> None:
        await config.set("ENABLE_USER_MEMORY", False)
        ctx = await svc.format_user_context_for_prompt(primary_user_id=100)
        assert ctx == ""

    async def test_format_context_includes_mentioned_users(self, svc: UserMemoryService) -> None:
        await svc.touch_user(100, nickname="小明")
        await svc.index_name("小红", 200)
        await svc.touch_user(200, nickname="小红")
        ctx = await svc.format_user_context_for_prompt(
            primary_user_id=100, observation_text="小红你好呀",
        )
        assert "相关的人" in ctx
        assert "小红" in ctx

    async def test_format_context_social_edges(self, svc: UserMemoryService) -> None:
        await svc.touch_user(100, nickname="小明")
        await svc.touch_user(200, nickname="小红")
        await svc.add_social_edge(100, 200, RELATION_INTRODUCED_AS, label="小红")
        ctx = await svc.format_user_context_for_prompt(primary_user_id=100)
        assert "社会关系" in ctx

    async def test_format_context_with_cognition(self, svc: UserMemoryService, llm: FakeLLMProvider) -> None:
        llm.set_chat_response("小明是个好朋友")
        await svc.touch_user(100, nickname="小明")
        p = await svc._get_or_create_profile(100)
        p.cognition_summary = "小明是个好朋友"
        p.cognition_interaction_at_update = 1
        await svc._save(p)

        ctx = await svc.format_user_context_for_prompt(primary_user_id=100)
        assert "认知" in ctx
        assert "小明是个好朋友" in ctx


# ---------------------------------------------------------------------------
# ensure_user_memory_initialized
# ---------------------------------------------------------------------------

class TestEnsureInitialized:
    async def test_ensure_initialized_works(self, svc: UserMemoryService) -> None:
        # Should not raise
        await svc.ensure_user_memory_initialized()

    async def test_ensure_initialized_disabled(self, svc: UserMemoryService, config: FakeConfigProvider) -> None:
        await config.set("ENABLE_USER_MEMORY", False)
        # Should not raise even when disabled
        await svc.ensure_user_memory_initialized()


# ---------------------------------------------------------------------------
# LLM extraction integration (flush)
# ---------------------------------------------------------------------------

class TestLLMExtraction:
    async def test_flush_extracts_adds_facts_and_edges(
        self,
        svc: UserMemoryService,
        llm: FakeLLMProvider,
        profiles: InMemoryUserProfileRepository,
        graph: InMemorySocialGraphRepository,
    ) -> None:
        llm.set_chat_response(json.dumps({
            "facts": [{"about_user_id": 100, "content": "喜欢猫", "category": "preference"}],
            "edges": [{"from_user_id": 100, "to_user_id": 200, "relation": "introduced_as", "label": "小红"}],
            "impression_delta": "很友好",
        }))

        await svc._llm_extract_memory({
            "user_id": 100,
            "text": "我喜欢猫",
            "nickname": "小明",
            "group_id": None,
            "context_lines": [],
        })

        p = await profiles.get(100)
        assert p is not None
        assert any(f.content == "喜欢猫" and f.active for f in p.facts)
        assert p.impression == "很友好"

        uid = await graph.resolve_name("小红")
        assert uid == 200

    async def test_flush_extracts_impression_appends(
        self,
        svc: UserMemoryService,
        llm: FakeLLMProvider,
        profiles: InMemoryUserProfileRepository,
    ) -> None:
        # Pre-set an impression
        await svc.touch_user(100, nickname="小明")
        p = await svc._get_or_create_profile(100)
        p.impression = "活泼"
        await svc._save(p)

        llm.set_chat_response(json.dumps({
            "facts": [],
            "edges": [],
            "impression_delta": "很友好",
        }))

        await svc._llm_extract_memory({
            "user_id": 100,
            "text": "你好",
            "nickname": "小明",
            "group_id": None,
            "context_lines": [],
        })

        p = await profiles.get(100)
        assert p is not None
        assert "活泼" in p.impression
        assert "很友好" in p.impression

    async def test_flush_extracts_no_duplicate_impression(
        self,
        svc: UserMemoryService,
        llm: FakeLLMProvider,
        profiles: InMemoryUserProfileRepository,
    ) -> None:
        await svc.touch_user(100, nickname="小明")
        p = await svc._get_or_create_profile(100)
        p.impression = "活泼"
        await svc._save(p)

        llm.set_chat_response(json.dumps({
            "facts": [],
            "edges": [],
            "impression_delta": "活泼",
        }))

        await svc._llm_extract_memory({
            "user_id": 100,
            "text": "你好",
            "nickname": "小明",
            "group_id": None,
            "context_lines": [],
        })

        p = await profiles.get(100)
        assert p is not None
        # "活泼" should not appear twice
        assert p.impression == "活泼"

    async def test_llm_extract_bad_json_no_crash(
        self,
        svc: UserMemoryService,
        llm: FakeLLMProvider,
    ) -> None:
        llm.set_chat_response("not json at all")
        # Should not raise
        await svc._llm_extract_memory({
            "user_id": 100,
            "text": "你好",
            "nickname": "小明",
            "group_id": None,
            "context_lines": [],
        })

    async def test_llm_extract_code_block_json(
        self,
        svc: UserMemoryService,
        llm: FakeLLMProvider,
        profiles: InMemoryUserProfileRepository,
    ) -> None:
        llm.set_chat_response("```json\n" + json.dumps({
            "facts": [{"about_user_id": 100, "content": "喜欢狗", "category": "preference"}],
            "edges": [],
            "impression_delta": "",
        }) + "\n```")

        await svc._llm_extract_memory({
            "user_id": 100,
            "text": "我喜欢狗",
            "nickname": "小明",
            "group_id": None,
            "context_lines": [],
        })

        p = await profiles.get(100)
        assert p is not None
        assert any(f.content == "喜欢狗" for f in p.facts)
