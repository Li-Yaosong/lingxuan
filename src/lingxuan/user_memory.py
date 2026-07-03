from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import nonebot

from lingxuan._config import _cfg

logger = nonebot.logger


def _memory_dir() -> Path:
    return Path(_cfg().get_str("DATA_ROOT")) / "memory"


def _user_dir() -> Path:
    return _memory_dir() / "users"


def _graph_path() -> Path:
    return _memory_dir() / "social_graph.json"


def _migrated_flag() -> Path:
    return _memory_dir() / ".user_memory_migrated"

_STAGES = ("stranger", "acquaintance", "familiar", "close")
_STAGE_LABELS = {
    "stranger": "陌生",
    "acquaintance": "认识",
    "familiar": "熟悉",
    "close": "亲近",
}

# --- rule patterns ---
_CORRECTION = re.compile(
    r"(?:我(?:不)?是(?:叫)?|叫我|请叫我|称呼我(?:为|做)?)"
    r"(?:[^，。！？\s]{1,12})?"
    r"(?:不是|不叫)([^，。！？\s]{1,12})"
    r".{0,8}"
    r"(?:是|叫)([^，。！？\s]{1,12})"
)
_CORRECTION_SIMPLE = re.compile(
    r"我(?:不)?是([^，。！？\s]{1,12})我(?:是|叫)([^，。！？\s]{1,12})"
)
_INTRO_NAME = re.compile(
    r"(?:这位就是|这就是|他(?:就)?是|她(?:就)?是|叫)([^，。！？\s]{1,12})"
)
_CALL_ME = re.compile(r"(?:叫我|请叫我|称呼我(?:为|做)?)([^，。！？\s]{1,12})")

# burst merge state
_pending_extracts: dict[int, list[dict[str, Any]]] = {}
_extract_tasks: dict[int, asyncio.Task[None]] = {}
_pending_refines: dict[int, dict[str, Any]] = {}
_refine_tasks: dict[int, asyncio.Task[None]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_fact_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class UserFact:
    id: str
    content: str
    category: str = "general"
    source_user_id: int = 0
    learned_at: str = ""
    confidence: float = 1.0
    active: bool = True
    supersedes: str | None = None

    def __post_init__(self) -> None:
        if not self.learned_at:
            self.learned_at = _now_iso()


@dataclass
class UserRelationship:
    stage: str = "stranger"
    first_met_at: str = ""
    last_seen_at: str = ""
    interaction_count: int = 0
    last_group_id: int | None = None
    seen_in_private: bool = False
    seen_in_group: bool = False

    def __post_init__(self) -> None:
        now = _now_iso()
        if not self.first_met_at:
            self.first_met_at = now
        if not self.last_seen_at:
            self.last_seen_at = now


@dataclass
class UserIdentity:
    preferred_name: str = ""
    aliases: list[str] = field(default_factory=list)
    group_cards: dict[str, str] = field(default_factory=dict)


@dataclass
class UserCognition:
    summary: str = ""
    updated_at: str = ""
    interaction_at_update: int = 0


@dataclass
class UserProfile:
    version: int = 2
    user_id: int = 0
    identity: UserIdentity = field(default_factory=UserIdentity)
    relationship: UserRelationship = field(default_factory=UserRelationship)
    facts: list[UserFact] = field(default_factory=list)
    impression: str = ""
    cognition: UserCognition = field(default_factory=UserCognition)


@dataclass
class SocialEdge:
    from_user_id: int
    to_user_id: int
    relation: str
    label: str = ""
    evidence: str = ""
    group_id: int | None = None
    learned_at: str = ""

    def __post_init__(self) -> None:
        if not self.learned_at:
            self.learned_at = _now_iso()


@dataclass
class SocialGraph:
    version: int = 1
    edges: list[SocialEdge] = field(default_factory=list)
    name_index: dict[str, int] = field(default_factory=dict)


# --- serialization ---

def _fact_from_dict(d: dict[str, Any]) -> UserFact:
    return UserFact(
        id=str(d.get("id", _new_fact_id())),
        content=str(d.get("content", "")),
        category=str(d.get("category", "general")),
        source_user_id=int(d.get("source_user_id", 0)),
        learned_at=str(d.get("learned_at", "")),
        confidence=float(d.get("confidence", 1.0)),
        active=bool(d.get("active", True)),
        supersedes=d.get("supersedes"),
    )


def _profile_from_dict(data: dict[str, Any]) -> UserProfile:
    identity_raw = data.get("identity", {})
    rel_raw = data.get("relationship", {})
    cog_raw = data.get("cognition", {})
    facts = [_fact_from_dict(f) for f in data.get("facts", []) if isinstance(f, dict)]
    return UserProfile(
        version=int(data.get("version", 2)),
        user_id=int(data.get("user_id", 0)),
        identity=UserIdentity(
            preferred_name=str(identity_raw.get("preferred_name", "")),
            aliases=list(identity_raw.get("aliases", [])),
            group_cards={
                str(k): str(v) for k, v in identity_raw.get("group_cards", {}).items()
            },
        ),
        relationship=UserRelationship(
            stage=str(rel_raw.get("stage", "stranger")),
            first_met_at=str(rel_raw.get("first_met_at", "")),
            last_seen_at=str(rel_raw.get("last_seen_at", "")),
            interaction_count=int(rel_raw.get("interaction_count", 0)),
            last_group_id=rel_raw.get("last_group_id"),
            seen_in_private=bool(rel_raw.get("seen_in_private", False)),
            seen_in_group=bool(rel_raw.get("seen_in_group", False)),
        ),
        facts=facts,
        impression=str(data.get("impression", "")),
        cognition=UserCognition(
            summary=str(cog_raw.get("summary", "")),
            updated_at=str(cog_raw.get("updated_at", "")),
            interaction_at_update=int(cog_raw.get("interaction_at_update", 0)),
        ),
    )


def _profile_to_dict(profile: UserProfile) -> dict[str, Any]:
    return {
        "version": profile.version,
        "user_id": profile.user_id,
        "identity": asdict(profile.identity),
        "relationship": asdict(profile.relationship),
        "facts": [asdict(f) for f in profile.facts],
        "impression": profile.impression,
        "cognition": asdict(profile.cognition),
    }


def _edge_from_dict(d: dict[str, Any]) -> SocialEdge:
    return SocialEdge(
        from_user_id=int(d.get("from_user_id", 0)),
        to_user_id=int(d.get("to_user_id", 0)),
        relation=str(d.get("relation", "")),
        label=str(d.get("label", "")),
        evidence=str(d.get("evidence", "")),
        group_id=d.get("group_id"),
        learned_at=str(d.get("learned_at", "")),
    )


def _graph_from_dict(data: dict[str, Any]) -> SocialGraph:
    edges = [_edge_from_dict(e) for e in data.get("edges", []) if isinstance(e, dict)]
    name_index: dict[str, int] = {}
    for name, uid in data.get("name_index", {}).items():
        try:
            name_index[str(name)] = int(uid)
        except (TypeError, ValueError):
            continue
    return SocialGraph(version=int(data.get("version", 1)), edges=edges, name_index=name_index)


def _graph_to_dict(graph: SocialGraph) -> dict[str, Any]:
    return {
        "version": graph.version,
        "edges": [asdict(e) for e in graph.edges],
        "name_index": graph.name_index,
    }


def _ensure_user_dir() -> None:
    _user_dir().mkdir(parents=True, exist_ok=True)
    _memory_dir().mkdir(parents=True, exist_ok=True)


def _user_path(user_id: int) -> Path:
    return _user_dir() / f"{user_id}.json"


# --- CRUD ---

def load_user_profile(user_id: int) -> UserProfile:
    path = _user_path(user_id)
    if not path.exists():
        return UserProfile(user_id=user_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        profile = _profile_from_dict(data)
        profile.user_id = user_id
        return profile
    except (json.JSONDecodeError, OSError):
        return UserProfile(user_id=user_id)


def save_user_profile(profile: UserProfile) -> None:
    _ensure_user_dir()
    max_facts = _cfg().get_int("USER_MEMORY_MAX_FACTS")
    active_facts = [f for f in profile.facts if f.active]
    if len(active_facts) > max_facts:
        active_facts.sort(key=lambda f: f.learned_at)
        keep_ids = {f.id for f in active_facts[-max_facts:]}
        for f in profile.facts:
            if f.active and f.id not in keep_ids:
                f.active = False
    path = _user_path(profile.user_id)
    path.write_text(
        json.dumps(_profile_to_dict(profile), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_social_graph() -> SocialGraph:
    gp = _graph_path()
    if not gp.exists():
        return SocialGraph()
    try:
        data = json.loads(gp.read_text(encoding="utf-8"))
        return _graph_from_dict(data)
    except (json.JSONDecodeError, OSError):
        return SocialGraph()


def save_social_graph(graph: SocialGraph) -> None:
    _ensure_user_dir()
    _graph_path().write_text(
        json.dumps(_graph_to_dict(graph), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear_user_profile(user_id: int) -> bool:
    path = _user_path(user_id)
    if path.exists():
        path.unlink()
        return True
    return False


def clear_social_graph() -> None:
    gp = _graph_path()
    if gp.exists():
        gp.unlink()


def clear_all_user_memory() -> int:
    count = 0
    for uid in list_user_profiles():
        if clear_user_profile(uid):
            count += 1
    clear_social_graph()
    mf = _migrated_flag()
    if mf.exists():
        mf.unlink()
    return count


def list_user_profiles() -> list[int]:
    ud = _user_dir()
    if not ud.exists():
        return []
    return sorted(int(p.stem) for p in ud.glob("*.json") if p.stem.isdigit())


# --- relationship stage ---

def _compute_stage(profile: UserProfile) -> str:
    rel = profile.relationship
    active_facts = [f for f in profile.facts if f.active]
    non_identity = [f for f in active_facts if f.category != "identity"]
    count = rel.interaction_count

    if count >= 30:
        return "close"
    if rel.seen_in_private and rel.seen_in_group:
        return "familiar"
    if count >= 10:
        return "familiar"
    if count >= 3 or non_identity:
        return "acquaintance"
    return "stranger"


def _update_stage(profile: UserProfile) -> None:
    profile.relationship.stage = _compute_stage(profile)


def stage_label(stage: str) -> str:
    return _STAGE_LABELS.get(stage, stage)


def display_name(profile: UserProfile) -> str:
    if profile.identity.preferred_name:
        return profile.identity.preferred_name
    if profile.identity.aliases:
        return profile.identity.aliases[0]
    return str(profile.user_id)


# --- touch & identity ---

def touch_user(
    user_id: int,
    *,
    nickname: str = "",
    group_id: int | None = None,
    is_private: bool = False,
) -> UserProfile:
    profile = load_user_profile(user_id)
    now = _now_iso()
    rel = profile.relationship
    rel.last_seen_at = now
    rel.interaction_count += 1
    if group_id is not None:
        rel.last_group_id = group_id
        rel.seen_in_group = True
        if nickname:
            profile.identity.group_cards[str(group_id)] = nickname
    if is_private:
        rel.seen_in_private = True
    if nickname and not profile.identity.preferred_name:
        profile.identity.preferred_name = nickname
    elif nickname and nickname != profile.identity.preferred_name:
        if nickname not in profile.identity.aliases:
            profile.identity.aliases.append(nickname)
    _update_stage(profile)
    save_user_profile(profile)
    return profile


def set_preferred_name(user_id: int, new_name: str, old_name: str = "") -> UserProfile:
    profile = load_user_profile(user_id)
    new_name = new_name.strip()
    old_name = old_name.strip()
    if not new_name:
        return profile
    prev = profile.identity.preferred_name
    if prev and prev != new_name and prev not in profile.identity.aliases:
        profile.identity.aliases.append(prev)
    if old_name and old_name != new_name and old_name not in profile.identity.aliases:
        profile.identity.aliases.append(old_name)
    profile.identity.preferred_name = new_name
    _add_identity_fact(
        profile,
        f"希望被称呼为{new_name}"
        + (f"，不要叫{old_name}" if old_name else ""),
        source_user_id=user_id,
    )
    graph = load_social_graph()
    _reindex_name(graph, old_name, new_name, user_id)
    save_social_graph(graph)
    save_user_profile(profile)
    return profile


def _reindex_name(graph: SocialGraph, old_name: str, new_name: str, user_id: int) -> None:
    if old_name and old_name in graph.name_index and graph.name_index[old_name] == user_id:
        del graph.name_index[old_name]
    if new_name:
        graph.name_index[new_name] = user_id


def _add_identity_fact(profile: UserProfile, content: str, source_user_id: int) -> None:
    for f in profile.facts:
        if f.active and f.category == "identity":
            f.active = False
    fact = UserFact(
        id=_new_fact_id(),
        content=content,
        category="identity",
        source_user_id=source_user_id,
    )
    profile.facts.append(fact)


def add_fact(
    user_id: int,
    content: str,
    *,
    category: str = "general",
    source_user_id: int = 0,
    confidence: float = 1.0,
) -> UserFact | None:
    content = content.strip()
    if not content:
        return None
    profile = load_user_profile(user_id)
    for f in profile.facts:
        if f.active and f.content == content:
            return f
    fact = UserFact(
        id=_new_fact_id(),
        content=content,
        category=category,
        source_user_id=source_user_id,
        confidence=confidence,
    )
    profile.facts.append(fact)
    _update_stage(profile)
    save_user_profile(profile)
    return fact


def add_social_edge(
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
    graph = load_social_graph()
    for edge in graph.edges:
        if (
            edge.from_user_id == from_user_id
            and edge.to_user_id == to_user_id
            and edge.relation == relation
            and edge.label == label
        ):
            return
    graph.edges.append(
        SocialEdge(
            from_user_id=from_user_id,
            to_user_id=to_user_id,
            relation=relation,
            label=label,
            evidence=evidence,
            group_id=group_id,
        )
    )
    if label:
        graph.name_index[label] = to_user_id
    save_social_graph(graph)


def index_name(name: str, user_id: int) -> None:
    name = name.strip()
    if not name or not user_id:
        return
    graph = load_social_graph()
    graph.name_index[name] = user_id
    save_social_graph(graph)


def resolve_name(name: str) -> int | None:
    name = name.strip()
    if not name:
        return None
    graph = load_social_graph()
    return graph.name_index.get(name)


def sync_entity_to_graph(name: str, user_id: int, session_id: str = "") -> None:
    """Sync group session entity to global name index and user profile."""
    index_name(name, user_id)
    profile = touch_user(user_id, nickname=name)
    from lingxuan.memory import merge_entity

    if session_id:
        merge_entity(session_id, name, user_id)
    save_user_profile(profile)


# --- rule extraction ---

def _extract_name_correction(text: str) -> tuple[str, str] | None:
    m = _CORRECTION_SIMPLE.search(text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = _CORRECTION.search(text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None


def _extract_call_me(text: str) -> str | None:
    m = _CALL_ME.search(text)
    if m:
        return m.group(1).strip()
    return None


def apply_rule_extraction(
    user_id: int,
    text: str,
    *,
    nickname: str = "",
    group_id: int | None = None,
    at_user_ids: list[int] | None = None,
    session_id: str = "",
) -> bool:
    """Apply rule-based memory updates. Returns True if any rule matched."""
    if not text.strip():
        return False
    changed = False
    at_user_ids = at_user_ids or []

    correction = _extract_name_correction(text)
    if correction:
        old_name, new_name = correction
        set_preferred_name(user_id, new_name, old_name)
        if session_id:
            from lingxuan.memory import merge_entity

            merge_entity(session_id, new_name, user_id)
        logger.info("user_memory name_correction uid={} {} -> {}", user_id, old_name, new_name)
        changed = True
    else:
        call_me = _extract_call_me(text)
        if call_me:
            set_preferred_name(user_id, call_me, nickname)
            if session_id:
                from lingxuan.memory import merge_entity

                merge_entity(session_id, call_me, user_id)
            changed = True

    for uid in at_user_ids:
        if "小堞宝" in text:
            index_name("小堞宝", uid)
            add_social_edge(user_id, uid, "introduced_as", label="小堞宝", evidence=text, group_id=group_id)
            changed = True
        match = _INTRO_NAME.search(text)
        if match:
            name = match.group(1).strip().strip("的")
            if name and len(name) <= 12:
                index_name(name, uid)
                add_social_edge(
                    user_id, uid, "introduced_as", label=name, evidence=text, group_id=group_id
                )
                if session_id:
                    from lingxuan.memory import merge_entity

                    merge_entity(session_id, name, uid)
                changed = True

    if "就是" in text and not at_user_ids:
        match = _INTRO_NAME.search(text)
        if match:
            name = match.group(1).strip()
            if name and len(name) <= 12:
                index_name(name, user_id)
                add_social_edge(user_id, user_id, "self_identified_as", label=name, evidence=text, group_id=group_id)
                if session_id:
                    from lingxuan.memory import merge_entity

                    merge_entity(session_id, name, user_id)
                changed = True

    if nickname:
        sync_entity_to_graph(nickname, user_id, session_id)

    if changed:
        schedule_cognition_refine(user_id)

    return changed


# --- LLM extraction ---

_EXTRACT_PROMPT = """从以下消息中提取可长期记住的人际信息。若无新信息返回空 JSON：{{}}

输出严格 JSON，字段：
- facts: [{{"about_user_id": 数字, "content": "事实", "category": "identity|preference|skill|relation|general"}}]
- edges: [{{"from_user_id": 数字, "to_user_id": 数字, "relation": "introduced_as|also_known_as|friend_of", "label": "称呼"}}]
- impression_delta: "对说话者印象的补充（短句，无则空）"

说话者 user_id={speaker_id}，昵称={nickname}
{context}
当前消息：[{nickname}]: {text}"""


async def _llm_extract_memory(payload: dict[str, Any]) -> None:
    from lingxuan.llm import call_llm_raw

    speaker_id = int(payload["user_id"])
    text = payload.get("text", "")
    nickname = payload.get("nickname", "")
    group_id = payload.get("group_id")
    context_lines = payload.get("context_lines", [])

    context = ""
    if context_lines:
        context = "近期上下文：\n" + "\n".join(context_lines) + "\n"

    prompt = _EXTRACT_PROMPT.format(
        speaker_id=speaker_id,
        nickname=nickname,
        context=context,
        text=text,
    )
    raw = await call_llm_raw(
        [{"role": "user", "content": prompt}],
        max_tokens=256,
        temperature=0.0,
        timeout=5.0,
        fallback="{}",
    )
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("user_memory llm extract parse failed uid={}", speaker_id)
        return

    profile = load_user_profile(speaker_id)
    for item in data.get("facts", []):
        if not isinstance(item, dict):
            continue
        about_uid = int(item.get("about_user_id", speaker_id))
        content = str(item.get("content", "")).strip()
        category = str(item.get("category", "general"))
        if content:
            add_fact(about_uid, content, category=category, source_user_id=speaker_id)

    for item in data.get("edges", []):
        if not isinstance(item, dict):
            continue
        add_social_edge(
            int(item.get("from_user_id", speaker_id)),
            int(item.get("to_user_id", 0)),
            str(item.get("relation", "introduced_as")),
            label=str(item.get("label", "")),
            evidence=text,
            group_id=group_id,
        )

    delta = str(data.get("impression_delta", "")).strip()
    if delta:
        profile = load_user_profile(speaker_id)
        if profile.impression:
            if delta not in profile.impression:
                profile.impression = f"{profile.impression}；{delta}"
        else:
            profile.impression = delta
        save_user_profile(profile)

    profile = load_user_profile(speaker_id)
    if should_refine_cognition(profile):
        schedule_cognition_refine(speaker_id)


async def _flush_extracts(user_id: int) -> None:
    batch = _pending_extracts.pop(user_id, [])
    _extract_tasks.pop(user_id, None)
    if not batch:
        return
    merged = batch[-1]
    if len(batch) > 1:
        texts = [b.get("text", "") for b in batch if b.get("text")]
        merged = {**merged, "text": " / ".join(texts)}
    await _llm_extract_memory(merged)


def schedule_memory_extract(
    user_id: int,
    text: str,
    *,
    nickname: str = "",
    group_id: int | None = None,
    context_lines: list[str] | None = None,
) -> None:
    """Schedule debounced LLM memory extraction for a message."""
    if not _cfg().get_bool("ENABLE_USER_MEMORY"):
        return

    payload = {
        "user_id": user_id,
        "text": text,
        "nickname": nickname,
        "group_id": group_id,
        "context_lines": context_lines or [],
    }
    _pending_extracts.setdefault(user_id, []).append(payload)

    if user_id in _extract_tasks and not _extract_tasks[user_id].done():
        return

    async def _delayed() -> None:
        await asyncio.sleep(_cfg().get_float("USER_MEMORY_BURST_MERGE"))
        await _flush_extracts(user_id)

    _extract_tasks[user_id] = asyncio.create_task(_delayed())


# --- cognition refine ---

_REFINE_PROMPT = """你是灵轩的记忆整理模块。根据以下信息，写一段 {max_chars} 字以内对「{name}」的整体认知。

要求：
- 在旧认知基础上更新，不要简单拼接
- 纠正已被推翻的信息（如称呼变更）
- 体现当前关系阶段（{stage}）
- 语气像灵轩内心的笔记，口语化
- 只输出总结正文，不要 JSON 或标题

旧认知：{old_summary}
称呼：{preferred_name}
印象标签：{impression}
已知事实：
{facts}
{exchange}"""


def should_refine_cognition(profile: UserProfile, *, has_recent_exchange: bool = False) -> bool:
    if not _cfg().get_bool("ENABLE_USER_COGNITION_REFINE"):
        return False
    if has_recent_exchange:
        return True
    rel = profile.relationship
    cog = profile.cognition
    delta = rel.interaction_count - cog.interaction_at_update
    if delta >= _cfg().get_int("USER_COGNITION_REFINE_INTERVAL"):
        return True
    if cog.updated_at:
        for f in profile.facts:
            if (
                f.active
                and f.category == "identity"
                and f.learned_at > cog.updated_at
            ):
                return True
    elif profile.facts or profile.impression:
        return True
    return False


def _supplementary_facts(profile: UserProfile, summary: str, limit: int = 3) -> list[str]:
    extras: list[str] = []
    for f in profile.facts:
        if not f.active or f.category == "identity":
            continue
        if f.content in summary:
            continue
        extras.append(f.content)
        if len(extras) >= limit:
            break
    return extras


async def refine_user_cognition(user_id: int, *, recent_exchange: str = "") -> str:
    from lingxuan.llm import call_llm_raw

    profile = load_user_profile(user_id)
    name = display_name(profile)
    facts_lines = [f"- {f.content}" for f in profile.facts if f.active]
    facts_block = "\n".join(facts_lines) if facts_lines else "(暂无)"
    exchange_block = ""
    if recent_exchange.strip():
        exchange_block = f"最近对话：\n{recent_exchange.strip()}\n"

    prompt = _REFINE_PROMPT.format(
        max_chars=_cfg().get_int("USER_COGNITION_MAX_CHARS"),
        name=name,
        stage=stage_label(profile.relationship.stage),
        old_summary=profile.cognition.summary or "(初次认识，尚无认知)",
        preferred_name=profile.identity.preferred_name or name,
        impression=profile.impression or "(暂无)",
        facts=facts_block,
        exchange=exchange_block,
    )
    raw = await call_llm_raw(
        [{"role": "user", "content": prompt}],
        max_tokens=200,
        temperature=0.3,
        timeout=10.0,
        fallback="",
    )
    summary = raw.strip()
    if not summary or summary.startswith("{"):
        logger.debug("cognition refine skipped uid={}", user_id)
        return profile.cognition.summary

    max_chars = _cfg().get_int("USER_COGNITION_MAX_CHARS")
    if len(summary) > max_chars:
        summary = summary[:max_chars]

    profile.cognition.summary = summary
    profile.cognition.updated_at = _now_iso()
    profile.cognition.interaction_at_update = profile.relationship.interaction_count
    profile.version = 2
    save_user_profile(profile)
    logger.info("cognition refined uid={} len={}", user_id, len(summary))
    return summary


async def _flush_refine(user_id: int) -> None:
    payload = _pending_refines.pop(user_id, {})
    _refine_tasks.pop(user_id, None)
    recent_exchange = str(payload.get("recent_exchange", ""))
    profile = load_user_profile(user_id)
    if not should_refine_cognition(profile, has_recent_exchange=bool(recent_exchange)):
        return
    await refine_user_cognition(user_id, recent_exchange=recent_exchange)


def schedule_cognition_refine(
    user_id: int,
    *,
    recent_exchange: str = "",
) -> None:
    if not _cfg().get_bool("ENABLE_USER_MEMORY") or not _cfg().get_bool("ENABLE_USER_COGNITION_REFINE"):
        return

    profile = load_user_profile(user_id)
    if not should_refine_cognition(profile, has_recent_exchange=bool(recent_exchange)):
        return

    existing = _pending_refines.get(user_id, {})
    if recent_exchange:
        existing["recent_exchange"] = recent_exchange
    _pending_refines[user_id] = existing

    if user_id in _refine_tasks and not _refine_tasks[user_id].done():
        return

    async def _delayed() -> None:
        await asyncio.sleep(_cfg().get_float("USER_COGNITION_REFINE_DELAY"))
        await _flush_refine(user_id)

    _refine_tasks[user_id] = asyncio.create_task(_delayed())


def on_user_message(
    user_id: int,
    text: str,
    *,
    nickname: str = "",
    group_id: int | None = None,
    is_private: bool = False,
    session_id: str = "",
    at_user_ids: list[int] | None = None,
    context_lines: list[str] | None = None,
) -> None:
    """Handle per-message user memory: touch, rules, and LLM extraction."""
    if not _cfg().get_bool("ENABLE_USER_MEMORY"):
        return
    touch_user(
        user_id,
        nickname=nickname,
        group_id=group_id,
        is_private=is_private,
    )
    apply_rule_extraction(
        user_id,
        text,
        nickname=nickname,
        group_id=group_id,
        at_user_ids=at_user_ids,
        session_id=session_id,
    )
    schedule_memory_extract(
        user_id,
        text,
        nickname=nickname,
        group_id=group_id,
        context_lines=context_lines,
    )


# --- context formatting ---

def _active_facts(profile: UserProfile, limit: int = 5) -> list[str]:
    facts = [f.content for f in profile.facts if f.active and f.category != "identity"]
    identity = [f.content for f in profile.facts if f.active and f.category == "identity"]
    return identity + facts[-limit:]


def _find_mentioned_user_ids(text: str) -> list[int]:
    graph = load_social_graph()
    found: list[int] = []
    seen: set[int] = set()
    for name, uid in graph.name_index.items():
        if name in text and uid not in seen:
            found.append(uid)
            seen.add(uid)
    return found


def format_user_context_for_prompt(
    primary_user_id: int | None = None,
    observation_text: str = "",
    *,
    is_private: bool = False,
) -> str:
    if not _cfg().get_bool("ENABLE_USER_MEMORY"):
        return ""

    blocks: list[str] = []
    related_ids: set[int] = set()

    if primary_user_id:
        profile = load_user_profile(primary_user_id)
        name = display_name(profile)
        lines = [
            f"- {name} (QQ {primary_user_id})，关系：{stage_label(profile.relationship.stage)}",
        ]
        if profile.cognition.summary:
            lines.append(f"  认知：{profile.cognition.summary}")
        elif profile.impression:
            lines.append(f"  印象：{profile.impression}")
        extras = _supplementary_facts(profile, profile.cognition.summary)
        if extras:
            lines.append("  补充：" + "；".join(extras))
        blocks.append("【正在对话的人】\n" + "\n".join(lines))
        related_ids.add(primary_user_id)

    mentioned = _find_mentioned_user_ids(observation_text)
    related_lines: list[str] = []
    for uid in mentioned:
        if uid in related_ids:
            continue
        related_ids.add(uid)
        profile = load_user_profile(uid)
        name = display_name(profile)
        aliases = [a for a in profile.identity.aliases if a != name]
        alias_str = f"/{'/'.join(aliases)}" if aliases else ""
        line = f"- {name}{alias_str} (QQ {uid})"
        if profile.cognition.summary:
            line += "：" + profile.cognition.summary
        else:
            facts = _active_facts(profile, limit=3)
            if facts:
                line += "：" + "；".join(facts)
        related_lines.append(line)
    if related_lines:
        blocks.append("【相关的人（本轮提及）】\n" + "\n".join(related_lines))

    graph = load_social_graph()
    social_lines: list[str] = []
    uid_set = related_ids or ({primary_user_id} if primary_user_id else set())
    for edge in graph.edges:
        if edge.from_user_id in uid_set or edge.to_user_id in uid_set:
            from_p = load_user_profile(edge.from_user_id)
            to_p = load_user_profile(edge.to_user_id)
            from_n = display_name(from_p)
            to_n = display_name(to_p)
            if edge.relation == "introduced_as" and edge.label:
                social_lines.append(f"- {from_n} 介绍 {edge.label} 就是 {to_n}")
            elif edge.relation == "also_known_as" and edge.label:
                social_lines.append(f"- {to_n} 又名 {edge.label}")
            elif edge.from_user_id == edge.to_user_id and edge.label:
                social_lines.append(f"- {from_n} 自称 {edge.label}")

    for name, uid in graph.name_index.items():
        profile = load_user_profile(uid)
        pref = profile.identity.preferred_name
        if pref and pref != name and name in observation_text:
            social_lines.append(f"- {name} 就是 {pref} (QQ {uid})")

    if social_lines:
        blocks.append("【社会关系】\n" + "\n".join(dict.fromkeys(social_lines)))

    return "\n\n".join(blocks)


def format_user_brief(user_id: int) -> str:
    if not _cfg().get_bool("ENABLE_USER_MEMORY"):
        return ""
    profile = load_user_profile(user_id)
    name = display_name(profile)
    return f"{name}(关系:{stage_label(profile.relationship.stage)})"


def format_user_profile_summary(user_id: int) -> str:
    profile = load_user_profile(user_id)
    lines = [
        f"用户: {display_name(profile)} (QQ {user_id})",
        f"关系: {stage_label(profile.relationship.stage)}",
        f"互动次数: {profile.relationship.interaction_count}",
        f"首选称呼: {profile.identity.preferred_name or '(未设置)'}",
    ]
    if profile.identity.aliases:
        lines.append(f"别名: {', '.join(profile.identity.aliases)}")
    if profile.cognition.summary:
        lines.append(f"认知总结: {profile.cognition.summary}")
        if profile.cognition.updated_at:
            lines.append(f"认知更新: {profile.cognition.updated_at}")
    elif profile.impression:
        lines.append(f"印象: {profile.impression}")
    facts = _active_facts(profile, limit=10)
    if facts:
        lines.append("事实:")
        for f in facts:
            lines.append(f"  - {f}")
    return "\n".join(lines)


# --- migration ---

def migrate_from_session_entities() -> int:
    """Scan existing group_*.json entities and bootstrap user profiles."""
    mf = _migrated_flag()
    if mf.exists():
        return 0
    count = 0
    memory_dir = _memory_dir()
    if not memory_dir.exists():
        _migrated_flag().touch()
        return 0
    for path in memory_dir.glob("group_*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        entities = data.get("meta", {}).get("entities", {})
        if not isinstance(entities, dict):
            continue
        session_id = path.stem
        for name, uid in entities.items():
            try:
                user_id = int(uid)
            except (TypeError, ValueError):
                continue
            sync_entity_to_graph(str(name), user_id, session_id)
            count += 1
    _migrated_flag().touch()
    logger.info("user_memory migrated {} entities from session files", count)
    return count


def ensure_user_memory_initialized() -> None:
    if _cfg().get_bool("ENABLE_USER_MEMORY"):
        migrate_from_session_entities()
