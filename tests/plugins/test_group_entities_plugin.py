"""Tests for group_entities built-in plugin.

Uses fakes / in-memory implementations to verify that:
- Group messages trigger entity learning (entities, name_index, social_edges, user_profiles)
- Private messages are ignored
- Bot's own messages are ignored
- Disable → no learning
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytest

from lingxuan.plugins.builtin.group_entities import GroupEntitiesPlugin
from lingxuan.plugins.host import DefaultPluginHost
from lingxuan.protocols.messaging import Actor, InboundMessage, SessionId
from lingxuan.protocols.plugins import HookType, PluginContext


# ---------------------------------------------------------------------------
# Fake / in-memory repositories & services
# ---------------------------------------------------------------------------


@dataclass
class FakeSession:
    session_id: SessionId
    kind: str
    group_id: int | None = None
    summary: str = ""
    nickname: str = ""
    last_active_at: datetime | None = None
    entities: dict[str, int] = field(default_factory=dict)


class InMemorySessionRepo:
    def __init__(self) -> None:
        self.sessions: dict[str, FakeSession] = {}

    async def get(self, sid: SessionId) -> FakeSession | None:
        return self.sessions.get(sid.as_str())

    async def ensure(self, sid: SessionId, **kw: Any) -> FakeSession:
        key = sid.as_str()
        if key not in self.sessions:
            self.sessions[key] = FakeSession(session_id=sid, kind=sid.kind)
        return self.sessions[key]

    async def merge_entity(self, sid: SessionId, name: str, user_id: int) -> None:
        s = await self.ensure(sid)
        s.entities[name] = user_id

    async def get_entities(self, sid: SessionId) -> dict[str, int]:
        s = await self.ensure(sid)
        return dict(s.entities)

    async def append_message(self, sid: SessionId, msg: Any) -> None:
        pass

    async def load_history(self, sid: SessionId, **kw: Any) -> list[Any]:
        return []

    async def count_messages(self, sid: SessionId) -> int:
        return 0

    async def count_sessions(self) -> int:
        return 0

    async def count_total_messages(self) -> int:
        return 0

    async def trim_to_last(self, sid: SessionId, **kw: Any) -> int:
        return 0

    async def get_summary(self, sid: SessionId) -> str:
        return ""

    async def set_summary(self, sid: SessionId, summary: str) -> None:
        pass

    async def clear(self, sid: SessionId) -> None:
        pass

    async def update_meta(self, sid: SessionId, **kw: Any) -> None:
        pass

    async def list_sessions(self, **kw: Any) -> list[Any]:
        return []


@dataclass
class FakeEdge:
    from_user_id: int
    to_user_id: int
    relation: str
    label: str = ""
    evidence: str = ""
    group_id: int | None = None
    learned_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class InMemorySocialGraphRepo:
    def __init__(self) -> None:
        self.edges: list[FakeEdge] = []
        self.name_index: dict[str, int] = {}

    async def add_edge(self, edge: FakeEdge) -> bool:
        key = (edge.from_user_id, edge.to_user_id, edge.relation, edge.label)
        for e in self.edges:
            if (e.from_user_id, e.to_user_id, e.relation, e.label) == key:
                return False
        self.edges.append(edge)
        return True

    async def index_name(self, name: str, user_id: int) -> None:
        self.name_index[name] = user_id

    async def resolve_name(self, name: str) -> int | None:
        return self.name_index.get(name)

    async def edges_from(self, user_id: int) -> list[FakeEdge]:
        return [e for e in self.edges if e.from_user_id == user_id]

    async def all_names(self) -> dict[str, int]:
        return dict(self.name_index)

    async def count_edges(self) -> int:
        return len(self.edges)

    async def clear(self) -> None:
        self.edges.clear()
        self.name_index.clear()


@dataclass
class FakeFact:
    id: str
    content: str
    category: str = "general"
    source_user_id: int = 0
    learned_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    confidence: float = 1.0
    active: bool = True
    supersedes: str | None = None


@dataclass
class FakeProfile:
    user_id: int
    preferred_name: str = ""
    aliases: list[str] = field(default_factory=list)
    group_cards: dict[str, str] = field(default_factory=dict)
    stage: str = "stranger"
    first_met_at: datetime | None = None
    last_seen_at: datetime | None = None
    interaction_count: int = 0
    last_group_id: int | None = None
    seen_in_private: bool = False
    seen_in_group: bool = False
    impression: str = ""
    cognition_summary: str = ""
    cognition_updated_at: datetime | None = None
    cognition_interaction_at_update: int = 0
    facts: list[FakeFact] = field(default_factory=list)


class InMemoryUserProfileRepo:
    def __init__(self) -> None:
        self.profiles: dict[int, FakeProfile] = {}

    async def get(self, user_id: int) -> FakeProfile | None:
        return self.profiles.get(user_id)

    async def upsert(self, profile: FakeProfile) -> None:
        self.profiles[profile.user_id] = profile

    async def add_fact(self, user_id: int, fact: FakeFact) -> None:
        p = self.profiles.setdefault(user_id, FakeProfile(user_id=user_id))
        p.facts.append(fact)

    async def list_active_facts(self, user_id: int, **kw: Any) -> list[FakeFact]:
        p = self.profiles.get(user_id)
        if not p:
            return []
        return [f for f in p.facts if f.active]

    async def deactivate_facts(self, user_id: int, fact_ids: list[str]) -> None:
        p = self.profiles.get(user_id)
        if not p:
            return
        for f in p.facts:
            if f.id in fact_ids:
                f.active = False

    async def list_user_ids(self) -> list[int]:
        return list(self.profiles.keys())

    async def count_users(self) -> int:
        return len(self.profiles)

    async def count_active_facts(self) -> int:
        return sum(
            len([f for f in p.facts if f.active]) for p in self.profiles.values()
        )

    async def delete(self, user_id: int) -> bool:
        return self.profiles.pop(user_id, None) is not None

    async def delete_all(self) -> int:
        n = len(self.profiles)
        self.profiles.clear()
        return n


class FakeClock:
    def __init__(self) -> None:
        self._now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        self._mono = 1000.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    async def sleep(self, seconds: float) -> None:
        self._mono += seconds


class FakeConfig:
    def __init__(self, data: dict | None = None) -> None:
        self._data = data or {}

    def get_bool(self, key: str) -> bool:
        return self._data.get(key, True)

    def get_str(self, key: str) -> str:
        return self._data.get(key, "")

    def get_int(self, key: str) -> int:
        return self._data.get(key, 0)

    def get_float(self, key: str) -> float:
        return self._data.get(key, 0.0)


class FakeLogSink:
    def __init__(self) -> None:
        self.records: list[Any] = []

    def emit(self, record: Any) -> None:
        self.records.append(record)


class FakeLLM:
    async def chat(self, messages: Any, **kw: Any) -> str:
        return ""

    async def chat_stream(self, messages: Any, **kw: Any) -> Any:
        yield ""  # type: ignore[misc]

    async def judge(self, prompt: str, **kw: Any) -> bool:
        return False


# ---------------------------------------------------------------------------
# Fake UserMemoryService — delegates to in-memory repos
# ---------------------------------------------------------------------------


class FakeUserMemoryService:
    """Minimal UserMemoryService that uses in-memory repos for testing."""

    def __init__(
        self,
        sessions: InMemorySessionRepo,
        profiles: InMemoryUserProfileRepo,
        graph: InMemorySocialGraphRepo,
    ) -> None:
        self._sessions = sessions
        self._profiles = profiles
        self._graph = graph
        self.cognition_refine_calls: list[int] = []
        self.memory_extract_calls: list[dict] = []

    async def touch_user(
        self, user_id: int, *, nickname: str = "", group_id: int | None = None,
        is_private: bool = False,
    ) -> FakeProfile:
        p = self._profiles.profiles.get(user_id)
        if p is None:
            p = FakeProfile(user_id=user_id)
            self._profiles.profiles[user_id] = p
        p.interaction_count += 1
        if group_id is not None:
            p.seen_in_group = True
            p.last_group_id = group_id
            if nickname:
                p.group_cards[str(group_id)] = nickname
        if is_private:
            p.seen_in_private = True
        if nickname and not p.preferred_name:
            p.preferred_name = nickname
        elif nickname and nickname != p.preferred_name:
            if nickname not in p.aliases:
                p.aliases.append(nickname)
        return p

    async def index_name(self, name: str, user_id: int) -> None:
        await self._graph.index_name(name, user_id)

    async def resolve_name(self, name: str) -> int | None:
        return await self._graph.resolve_name(name)

    async def sync_entity_to_graph(
        self, name: str, user_id: int, session_id: str = ""
    ) -> None:
        await self.index_name(name, user_id)
        await self.touch_user(user_id, nickname=name)
        if session_id:
            sid = SessionId.parse(session_id)
            await self._sessions.merge_entity(sid, name, user_id)

    async def merge_entity(
        self, session_id: Any, name: str, user_id: int
    ) -> None:
        await self._sessions.merge_entity(session_id, name, user_id)

    async def add_social_edge(
        self,
        from_user_id: int,
        to_user_id: int,
        relation: str,
        *,
        label: str = "",
        evidence: str = "",
        group_id: int | None = None,
    ) -> None:
        if not from_user_id or not to_user_id:
            return
        edge = FakeEdge(
            from_user_id=from_user_id,
            to_user_id=to_user_id,
            relation=relation,
            label=label,
            evidence=evidence,
            group_id=group_id,
        )
        added = await self._graph.add_edge(edge)
        if added and label:
            await self._graph.index_name(label, to_user_id)

    async def set_preferred_name(
        self, user_id: int, new_name: str, old_name: str = ""
    ) -> FakeProfile:
        p = await self.touch_user(user_id)
        if p.preferred_name and p.preferred_name != new_name:
            if p.preferred_name not in p.aliases:
                p.aliases.append(p.preferred_name)
        p.preferred_name = new_name
        await self._graph.index_name(new_name, user_id)
        return p

    async def apply_rule_extraction(
        self,
        user_id: int,
        text: str,
        *,
        nickname: str = "",
        group_id: int | None = None,
        at_user_ids: list[int] | None = None,
        session_id: str = "",
    ) -> bool:
        """Simplified rule extraction for testing: handles name correction, call-me, intro."""
        import re
        at_user_ids = at_user_ids or []
        changed = False

        # Name correction: "我不叫X我叫Y"
        correction = re.search(r"我不叫([^，。！？\s]{1,12})我叫([^，。！？\s]{1,12})", text)
        if correction:
            old_name, new_name = correction.group(1).strip(), correction.group(2).strip()
            await self.set_preferred_name(user_id, new_name, old_name)
            changed = True
        else:
            # Call me: "叫我X"
            call_me = re.search(r"叫我([^，。！？\s]{1,12})", text)
            if call_me:
                name = call_me.group(1).strip()
                await self.set_preferred_name(user_id, name, nickname)
                changed = True

        # @'d user introductions with "小堞宝"
        for uid in at_user_ids:
            if "小堞宝" in text:
                await self.index_name("小堞宝", uid)
                await self.add_social_edge(
                    user_id, uid, "introduced_as",
                    label="小堞宝", evidence=text, group_id=group_id,
                )
                changed = True
            # Introduction: "这位就是X"
            intro = re.search(
                r"(?:这位就是|这就是|他(?:就)?是|她(?:就)?是|叫)([^，。！？\s]{1,12})", text
            )
            if intro:
                name = intro.group(1).strip().strip("的")
                if name and len(name) <= 12:
                    await self.index_name(name, uid)
                    await self.add_social_edge(
                        user_id, uid, "introduced_as",
                        label=name, evidence=text, group_id=group_id,
                    )
                    changed = True

        # Self-identification: "就是X" without @
        if "就是" in text and not at_user_ids:
            intro = re.search(
                r"(?:这位就是|这就是|他(?:就)?是|她(?:就)?是|叫)([^，。！？\s]{1,12})", text
            )
            if intro:
                name = intro.group(1).strip()
                if name and len(name) <= 12:
                    await self.index_name(name, user_id)
                    await self.add_social_edge(
                        user_id, user_id, "self_identified_as",
                        label=name, evidence=text, group_id=group_id,
                    )
                    changed = True

        if nickname:
            await self.sync_entity_to_graph(nickname, user_id, session_id)

        return changed

    async def on_user_message(self, *a: Any, **kw: Any) -> None:
        pass

    async def schedule_cognition_refine(self, user_id: int, **kw: Any) -> None:
        self.cognition_refine_calls.append(user_id)

    async def schedule_memory_extract(self, *a: Any, **kw: Any) -> None:
        self.memory_extract_calls.append({"args": a, "kwargs": kw})


# ---------------------------------------------------------------------------
# Minimal PluginServices for testing
# ---------------------------------------------------------------------------


class FakePluginServices:
    def __init__(
        self,
        sessions: InMemorySessionRepo,
        profiles: InMemoryUserProfileRepo,
        graph: InMemorySocialGraphRepo,
        user_memory: FakeUserMemoryService,
    ) -> None:
        self._sessions = sessions
        self._profiles = profiles
        self._graph = graph
        self._user_memory = user_memory

    @property
    def sessions(self) -> InMemorySessionRepo:
        return self._sessions

    @property
    def user_profiles(self) -> InMemoryUserProfileRepo:
        return self._profiles

    @property
    def social_graph(self) -> InMemorySocialGraphRepo:
        return self._graph

    @property
    def user_memory(self) -> FakeUserMemoryService:
        return self._user_memory

    @property
    def config(self) -> FakeConfig:
        return FakeConfig()

    @property
    def log(self) -> FakeLogSink:
        return FakeLogSink()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def env() -> dict:
    """Set up a full in-memory test environment."""
    sessions = InMemorySessionRepo()
    profiles = InMemoryUserProfileRepo()
    graph = InMemorySocialGraphRepo()
    um = FakeUserMemoryService(sessions, profiles, graph)
    services = FakePluginServices(sessions, profiles, graph, um)
    host = DefaultPluginHost(services=services)
    return {
        "sessions": sessions,
        "profiles": profiles,
        "graph": graph,
        "um": um,
        "services": services,
        "host": host,
    }


def _group_inbound(
    user_id: int = 100,
    nickname: str = "测试者",
    text: str = "你好",
    at_user_ids: list[int] | None = None,
    group_id: int = 999,
    is_self: bool = False,
) -> InboundMessage:
    """Create a group InboundMessage for testing."""
    return InboundMessage(
        session_id=SessionId(kind="group", peer_id=group_id),
        actor=Actor(user_id=user_id, nickname=nickname, is_self=is_self),
        text=text,
        at_user_ids=at_user_ids or [],
        group_id=group_id,
    )


def _private_inbound(
    user_id: int = 100,
    nickname: str = "测试者",
    text: str = "你好",
) -> InboundMessage:
    """Create a private InboundMessage for testing."""
    return InboundMessage(
        session_id=SessionId(kind="private", peer_id=user_id),
        actor=Actor(user_id=user_id, nickname=nickname),
        text=text,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGroupEntitiesPlugin:
    """Test group_entities plugin entity learning."""

    @pytest.mark.asyncio
    async def test_setup_subscribes_to_inbound(self, env: dict) -> None:
        """Plugin subscribes to on_inbound_message during setup."""
        p = GroupEntitiesPlugin()
        env["host"].register(p, config={})
        subs = env["host"]._subscriptions[HookType.on_inbound_message]
        assert any(name == "group_entities" for name, _ in subs)

    @pytest.mark.asyncio
    async def test_private_message_ignored(self, env: dict) -> None:
        """Private messages do not trigger entity learning."""
        p = GroupEntitiesPlugin()
        env["host"].register(p, config={})
        env["host"].enable("group_entities")

        inbound = _private_inbound(text="这位就是小明")
        ctx = PluginContext(hook=HookType.on_inbound_message, inbound=inbound)
        await env["host"].dispatch(ctx)

        # No entities written
        entities = await env["sessions"].get_entities(
            SessionId(kind="private", peer_id=100)
        )
        assert not entities

    @pytest.mark.asyncio
    async def test_bot_message_ignored(self, env: dict) -> None:
        """Bot's own messages do not trigger entity learning."""
        p = GroupEntitiesPlugin()
        env["host"].register(p, config={})
        env["host"].enable("group_entities")

        inbound = _group_inbound(text="这位就是小明", is_self=True)
        ctx = PluginContext(hook=HookType.on_inbound_message, inbound=inbound)
        await env["host"].dispatch(ctx)

        entities = await env["sessions"].get_entities(
            SessionId(kind="group", peer_id=999)
        )
        assert not entities

    @pytest.mark.asyncio
    async def test_speaker_nickname_synced(self, env: dict) -> None:
        """Speaker nickname is synced to name_index and profile."""
        p = GroupEntitiesPlugin()
        env["host"].register(p, config={})
        env["host"].enable("group_entities")

        inbound = _group_inbound(user_id=100, nickname="小明", text="大家好")
        ctx = PluginContext(hook=HookType.on_inbound_message, inbound=inbound)
        await env["host"].dispatch(ctx)

        # Name should be indexed
        assert await env["graph"].resolve_name("小明") == 100
        # Profile should exist with preferred_name
        profile = await env["profiles"].get(100)
        assert profile is not None
        assert profile.preferred_name == "小明"
        # Session entity should be written
        entities = await env["sessions"].get_entities(
            SessionId(kind="group", peer_id=999)
        )
        assert entities.get("小明") == 100

    @pytest.mark.asyncio
    async def test_at_intro_name_extraction(self, env: dict) -> None:
        """Introduction with @: "这位就是小红" → entity + name_index + social_edge."""
        p = GroupEntitiesPlugin()
        env["host"].register(p, config={})
        env["host"].enable("group_entities")

        inbound = _group_inbound(
            user_id=100, nickname="小明",
            text="这位就是小红",
            at_user_ids=[200],
        )
        ctx = PluginContext(hook=HookType.on_inbound_message, inbound=inbound)
        await env["host"].dispatch(ctx)

        # Name indexed
        assert await env["graph"].resolve_name("小红") == 200
        # Session entity
        entities = await env["sessions"].get_entities(
            SessionId(kind="group", peer_id=999)
        )
        assert entities.get("小红") == 200
        # Social edge created by apply_rule_extraction
        edges = await env["graph"].edges_from(100)
        intro_edges = [e for e in edges if e.relation == "introduced_as" and e.label == "小红"]
        assert len(intro_edges) >= 1

    @pytest.mark.asyncio
    async def test_bot_name_keyword_detection(self, env: dict) -> None:
        """@ with '小堞宝' → entity + name_index for the @'d user."""
        p = GroupEntitiesPlugin()
        env["host"].register(p, config={})
        env["host"].enable("group_entities")

        inbound = _group_inbound(
            user_id=100, nickname="小明",
            text="这位就是小堞宝",
            at_user_ids=[200],
        )
        ctx = PluginContext(hook=HookType.on_inbound_message, inbound=inbound)
        await env["host"].dispatch(ctx)

        # 小堞宝 indexed
        assert await env["graph"].resolve_name("小堞宝") == 200
        # Session entity
        entities = await env["sessions"].get_entities(
            SessionId(kind="group", peer_id=999)
        )
        assert entities.get("小堞宝") == 200

    @pytest.mark.asyncio
    async def test_self_introduction_fallback(self, env: dict) -> None:
        """'就是' without @: "这就是小红" → self-identified entity."""
        p = GroupEntitiesPlugin()
        env["host"].register(p, config={})
        env["host"].enable("group_entities")

        inbound = _group_inbound(
            user_id=100, nickname="小明",
            text="这就是小红",
        )
        ctx = PluginContext(hook=HookType.on_inbound_message, inbound=inbound)
        await env["host"].dispatch(ctx)

        # Name indexed for self
        assert await env["graph"].resolve_name("小红") == 100
        # Session entity
        entities = await env["sessions"].get_entities(
            SessionId(kind="group", peer_id=999)
        )
        assert entities.get("小红") == 100
        # Social edge: self_identified_as
        edges = await env["graph"].edges_from(100)
        self_edges = [e for e in edges if e.relation == "self_identified_as" and e.label == "小红"]
        assert len(self_edges) >= 1

    @pytest.mark.asyncio
    async def test_name_correction_rule(self, env: dict) -> None:
        """Rule extraction: "我不叫小红我叫小蓝" → preferred_name updated."""
        p = GroupEntitiesPlugin()
        env["host"].register(p, config={})
        env["host"].enable("group_entities")

        # First set a name via touch
        await env["um"].touch_user(100, nickname="小红")

        inbound = _group_inbound(
            user_id=100, nickname="小红",
            text="我不叫小红我叫小蓝",
        )
        ctx = PluginContext(hook=HookType.on_inbound_message, inbound=inbound)
        await env["host"].dispatch(ctx)

        # Preferred name should be updated
        profile = await env["profiles"].get(100)
        assert profile is not None
        assert profile.preferred_name == "小蓝"

    @pytest.mark.asyncio
    async def test_call_me_rule(self, env: dict) -> None:
        """Rule extraction: "叫我小蓝" → preferred_name updated."""
        p = GroupEntitiesPlugin()
        env["host"].register(p, config={})
        env["host"].enable("group_entities")

        inbound = _group_inbound(
            user_id=100, nickname="小明",
            text="叫我小蓝",
        )
        ctx = PluginContext(hook=HookType.on_inbound_message, inbound=inbound)
        await env["host"].dispatch(ctx)

        profile = await env["profiles"].get(100)
        assert profile is not None
        assert profile.preferred_name == "小蓝"

    @pytest.mark.asyncio
    async def test_disabled_plugin_no_learning(self, env: dict) -> None:
        """When plugin is disabled, no entity learning occurs."""
        p = GroupEntitiesPlugin()
        env["host"].register(p, config={})
        env["host"].disable("group_entities")

        inbound = _group_inbound(
            user_id=100, nickname="小明",
            text="这位就是小红",
            at_user_ids=[200],
        )
        ctx = PluginContext(hook=HookType.on_inbound_message, inbound=inbound)
        await env["host"].dispatch(ctx)

        # No name index
        assert await env["graph"].resolve_name("小红") is None
        # No entities
        entities = await env["sessions"].get_entities(
            SessionId(kind="group", peer_id=999)
        )
        assert not entities
        # No profile
        assert await env["profiles"].get(100) is None

    @pytest.mark.asyncio
    async def test_empty_text_ignored(self, env: dict) -> None:
        """Empty text messages do not trigger entity learning."""
        p = GroupEntitiesPlugin()
        env["host"].register(p, config={})
        env["host"].enable("group_entities")

        inbound = _group_inbound(text="   ")
        ctx = PluginContext(hook=HookType.on_inbound_message, inbound=inbound)
        await env["host"].dispatch(ctx)

        entities = await env["sessions"].get_entities(
            SessionId(kind="group", peer_id=999)
        )
        assert not entities

    @pytest.mark.asyncio
    async def test_no_group_id_ignored(self, env: dict) -> None:
        """Inbound without group_id is ignored — no entity learning."""
        p = GroupEntitiesPlugin()
        env["host"].register(p, config={})
        env["host"].enable("group_entities")

        inbound = InboundMessage(
            session_id=SessionId(kind="group", peer_id=999),
            actor=Actor(user_id=100, nickname="小明"),
            text="这位就是小红",
            at_user_ids=[200],
            group_id=None,
        )
        ctx = PluginContext(hook=HookType.on_inbound_message, inbound=inbound)
        await env["host"].dispatch(ctx)

        # No entities written (group_id is None → early return)
        entities = await env["sessions"].get_entities(
            SessionId(kind="group", peer_id=999)
        )
        assert not entities

    @pytest.mark.asyncio
    async def test_configurable_bot_keywords(self, env: dict) -> None:
        """Plugin config can override bot_name_keywords."""
        p = GroupEntitiesPlugin()
        env["host"].register(p, config={"bot_name_keywords": ["小灵轩"]})
        env["host"].enable("group_entities")

        inbound = _group_inbound(
            user_id=100, nickname="小明",
            text="这位就是小灵轩",
            at_user_ids=[200],
        )
        ctx = PluginContext(hook=HookType.on_inbound_message, inbound=inbound)
        await env["host"].dispatch(ctx)

        # Custom keyword should be indexed
        assert await env["graph"].resolve_name("小灵轩") == 200
