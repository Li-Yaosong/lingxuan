"""UserMemoryService: user profiles, facts, social graph, cognition refine.

Migrated from MVP user_memory.py — all IO now goes through injected
Repository interfaces; no file IO, no nonebot, no sqlalchemy.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from lingxuan.core.models import (
    FACT_CATEGORY_IDENTITY,
    RELATION_INTRODUCED_AS,
    RELATION_SELF_IDENTIFIED_AS,
    compute_stage,
    display_name,
    new_fact_id,
    stage_label,
)
from lingxuan.protocols.clock import Clock
from lingxuan.protocols.config import ConfigProvider
from lingxuan.protocols.llm import ChatMessage, LLMProvider
from lingxuan.protocols.logging import LogSink, LogRecord
from lingxuan.protocols.plugins import HookType, PluginContext, PluginHost
from lingxuan.protocols.repositories import (
    SocialEdge,
    SocialGraphRepository,
    UserFact,
    UserProfile,
    UserProfileRepository,
)

# ---------------------------------------------------------------------------
# Rule patterns (same regexes as MVP)
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# LLM prompt templates
# ---------------------------------------------------------------------------

_EXTRACT_PROMPT = """从以下消息中提取可长期记住的人际信息。若无新信息返回空 JSON：{{}}

输出严格 JSON，字段：
- facts: [{{"about_user_id": 数字, "content": "事实", "category": "identity|preference|skill|relation|general"}}]
- edges: [{{"from_user_id": 数字, "to_user_id": 数字, "relation": "introduced_as|also_known_as|friend_of", "label": "称呼"}}]
- impression_delta: "对说话者印象的补充（短句，无则空）"

说话者 user_id={speaker_id}，昵称={nickname}
{context}
当前消息：[{nickname}]: {text}"""

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


class UserMemoryService:
    """User memory: profiles, facts, social graph, cognition integration.

    All persistence goes through injected Repository interfaces.
    Debounce state is per-instance, not module-global.
    """

    def __init__(
        self,
        *,
        profiles: UserProfileRepository,
        graph: SocialGraphRepository,
        llm: LLMProvider,
        config: ConfigProvider,
        clock: Clock,
        log: LogSink,
        plugin_host: PluginHost | None = None,
    ) -> None:
        self._profiles = profiles
        self._graph = graph
        self._llm = llm
        self._config = config
        self._clock = clock
        self._log = log
        self._plugin_host = plugin_host

        # Per-instance debounce state (replaces module-level globals)
        self._pending_extracts: dict[int, list[dict[str, Any]]] = {}
        self._extract_tasks: dict[int, asyncio.Task[None]] = {}
        self._pending_refines: dict[int, dict[str, Any]] = {}
        self._refine_tasks: dict[int, asyncio.Task[None]] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _now(self) -> datetime:
        return self._clock.now()

    def _emit(self, level: str, msg: str, **extra: Any) -> None:
        self._log.emit(LogRecord(
            ts=self._now(),
            level=level,
            logger="user_memory",
            msg=msg,
            extra=extra,
        ))

    def _enabled(self) -> bool:
        return self._config.get_bool("ENABLE_USER_MEMORY")

    async def _get_or_create_profile(self, user_id: int) -> UserProfile:
        p = await self._profiles.get(user_id)
        if p is None:
            p = UserProfile(user_id=user_id)
        return p

    async def _save(self, profile: UserProfile) -> None:
        # Fact truncation: soft-delete oldest active facts exceeding limit
        max_facts = self._config.get_int("USER_MEMORY_MAX_FACTS")
        active_facts = [f for f in profile.facts if f.active]
        if len(active_facts) > max_facts:
            active_facts.sort(key=lambda f: f.learned_at)
            keep_ids = {f.id for f in active_facts[-max_facts:]}
            deactivate_ids = [
                f.id for f in profile.facts if f.active and f.id not in keep_ids
            ]
            if deactivate_ids:
                await self._profiles.deactivate_facts(profile.user_id, deactivate_ids)
        await self._profiles.upsert(profile)

    # ------------------------------------------------------------------
    # Touch & identity
    # ------------------------------------------------------------------

    async def touch_user(
        self,
        user_id: int,
        *,
        nickname: str = "",
        group_id: int | None = None,
        is_private: bool = False,
    ) -> UserProfile:
        profile = await self._get_or_create_profile(user_id)
        now = self._now()
        profile.last_seen_at = now
        profile.interaction_count += 1
        if group_id is not None:
            profile.last_group_id = group_id
            profile.seen_in_group = True
            if nickname:
                profile.group_cards[str(group_id)] = nickname
        if is_private:
            profile.seen_in_private = True
        if nickname and not profile.preferred_name:
            profile.preferred_name = nickname
        elif nickname and nickname != profile.preferred_name:
            if nickname not in profile.aliases:
                profile.aliases.append(nickname)
        if profile.first_met_at is None:
            profile.first_met_at = now
        profile.stage = compute_stage(profile)
        await self._save(profile)
        return profile

    async def set_preferred_name(
        self, user_id: int, new_name: str, old_name: str = ""
    ) -> UserProfile:
        new_name = new_name.strip()
        old_name = old_name.strip()
        if not new_name:
            profile = await self._get_or_create_profile(user_id)
            return profile
        profile = await self._get_or_create_profile(user_id)
        prev = profile.preferred_name
        if prev and prev != new_name and prev not in profile.aliases:
            profile.aliases.append(prev)
        if old_name and old_name != new_name and old_name not in profile.aliases:
            profile.aliases.append(old_name)
        profile.preferred_name = new_name
        await self._add_identity_fact(
            profile,
            f"希望被称呼为{new_name}"
            + (f"，不要叫{old_name}" if old_name else ""),
            source_user_id=user_id,
        )
        # Reindex name in social graph
        if old_name:
            names = await self._graph.all_names()
            if old_name in names and names[old_name] == user_id:
                # Can't delete from the protocol, but we can overwrite
                pass
        if new_name:
            await self._graph.index_name(new_name, user_id)
        await self._save(profile)
        return profile

    async def _add_identity_fact(
        self, profile: UserProfile, content: str, source_user_id: int
    ) -> None:
        # Deactivate existing active identity facts
        old_ids = [f.id for f in profile.facts if f.active and f.category == FACT_CATEGORY_IDENTITY]
        if old_ids:
            await self._profiles.deactivate_facts(profile.user_id, old_ids)
            for f in profile.facts:
                if f.active and f.category == FACT_CATEGORY_IDENTITY:
                    f.active = False
        fact = UserFact(
            id=new_fact_id(),
            content=content,
            category=FACT_CATEGORY_IDENTITY,
            source_user_id=source_user_id,
            learned_at=self._now(),
        )
        profile.facts.append(fact)

    # ------------------------------------------------------------------
    # Facts
    # ------------------------------------------------------------------

    async def add_fact(
        self,
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
        profile = await self._get_or_create_profile(user_id)
        # Dedup: skip if active fact with same content
        for f in profile.facts:
            if f.active and f.content == content:
                return f
        fact = UserFact(
            id=new_fact_id(),
            content=content,
            category=category,
            source_user_id=source_user_id,
            confidence=confidence,
            learned_at=self._now(),
        )
        profile.facts.append(fact)
        profile.stage = compute_stage(profile)
        await self._save(profile)
        return fact

    # ------------------------------------------------------------------
    # Social graph
    # ------------------------------------------------------------------

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
        edge = SocialEdge(
            from_user_id=from_user_id,
            to_user_id=to_user_id,
            relation=relation,
            label=label,
            evidence=evidence,
            group_id=group_id,
            learned_at=self._now(),
        )
        added = await self._graph.add_edge(edge)
        if added and label:
            await self._graph.index_name(label, to_user_id)

    async def index_name(self, name: str, user_id: int) -> None:
        name = name.strip()
        if not name or not user_id:
            return
        await self._graph.index_name(name, user_id)

    async def resolve_name(self, name: str) -> int | None:
        name = name.strip()
        if not name:
            return None
        return await self._graph.resolve_name(name)

    async def sync_entity_to_graph(
        self, name: str, user_id: int, session_id: str = ""
    ) -> None:
        """Sync group session entity to global name index and user profile."""
        await self.index_name(name, user_id)
        profile = await self.touch_user(user_id, nickname=name)
        # Session entity merge is handled by caller (DialogueService)
        # using SessionRepository.merge_entity — not our concern here.

    # ------------------------------------------------------------------
    # Rule extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_name_correction(text: str) -> tuple[str, str] | None:
        m = _CORRECTION_SIMPLE.search(text)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        m = _CORRECTION.search(text)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        return None

    @staticmethod
    def _extract_call_me(text: str) -> str | None:
        m = _CALL_ME.search(text)
        if m:
            return m.group(1).strip()
        return None

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
        """Apply rule-based memory updates. Returns True if any rule matched."""
        if not text.strip():
            return False
        changed = False
        at_user_ids = at_user_ids or []

        correction = self._extract_name_correction(text)
        if correction:
            old_name, new_name = correction
            await self.set_preferred_name(user_id, new_name, old_name)
            self._emit("INFO", "name_correction", uid=user_id, old=old_name, new=new_name)
            changed = True
        else:
            call_me = self._extract_call_me(text)
            if call_me:
                await self.set_preferred_name(user_id, call_me, nickname)
                changed = True

        for uid in at_user_ids:
            if "小堞宝" in text:
                await self.index_name("小堞宝", uid)
                await self.add_social_edge(
                    user_id, uid, RELATION_INTRODUCED_AS,
                    label="小堞宝", evidence=text, group_id=group_id,
                )
                changed = True
            match = _INTRO_NAME.search(text)
            if match:
                name = match.group(1).strip().strip("的")
                if name and len(name) <= 12:
                    await self.index_name(name, uid)
                    await self.add_social_edge(
                        user_id, uid, RELATION_INTRODUCED_AS,
                        label=name, evidence=text, group_id=group_id,
                    )
                    changed = True

        if "就是" in text and not at_user_ids:
            match = _INTRO_NAME.search(text)
            if match:
                name = match.group(1).strip()
                if name and len(name) <= 12:
                    await self.index_name(name, user_id)
                    await self.add_social_edge(
                        user_id, user_id, RELATION_SELF_IDENTIFIED_AS,
                        label=name, evidence=text, group_id=group_id,
                    )
                    changed = True

        if nickname:
            await self.sync_entity_to_graph(nickname, user_id, session_id)

        if changed:
            await self.schedule_cognition_refine(user_id)

        return changed

    # ------------------------------------------------------------------
    # Message entry point
    # ------------------------------------------------------------------

    async def on_user_message(
        self,
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
        if not self._enabled():
            return
        await self.touch_user(
            user_id, nickname=nickname, group_id=group_id, is_private=is_private,
        )
        await self.apply_rule_extraction(
            user_id, text,
            nickname=nickname, group_id=group_id,
            at_user_ids=at_user_ids, session_id=session_id,
        )
        await self.schedule_memory_extract(
            user_id, text,
            nickname=nickname, group_id=group_id,
            context_lines=context_lines,
        )

    # ------------------------------------------------------------------
    # LLM memory extraction (debounced)
    # ------------------------------------------------------------------

    async def _llm_extract_memory(self, payload: dict[str, Any]) -> None:
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
        try:
            raw = await self._llm.chat(
                [ChatMessage(role="user", content=prompt)],
                max_tokens=256,
                temperature=0.0,
                timeout=5.0,
            )
        except Exception:
            self._emit("DEBUG", "llm_extract_failed", uid=speaker_id)
            return

        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self._emit("DEBUG", "llm_extract_parse_failed", uid=speaker_id)
            return

        # Plugin hook: on_memory_extract — allow plugins to modify candidates
        if self._plugin_host is not None:
            extract_ctx = PluginContext(
                hook=HookType.on_memory_extract,
                extra={
                    "facts": data.get("facts", []),
                    "edges": data.get("edges", []),
                    "impression_delta": data.get("impression_delta", ""),
                    "speaker_id": speaker_id,
                    "group_id": group_id,
                },
            )
            extract_ctx = await self._plugin_host.dispatch(extract_ctx)
            # Apply plugin modifications back
            data = {
                "facts": extract_ctx.extra.get("facts", []),
                "edges": extract_ctx.extra.get("edges", []),
                "impression_delta": extract_ctx.extra.get("impression_delta", ""),
            }

        for item in data.get("facts", []):
            if not isinstance(item, dict):
                continue
            about_uid = int(item.get("about_user_id", speaker_id))
            content = str(item.get("content", "")).strip()
            category = str(item.get("category", "general"))
            if content:
                await self.add_fact(about_uid, content, category=category, source_user_id=speaker_id)

        for item in data.get("edges", []):
            if not isinstance(item, dict):
                continue
            await self.add_social_edge(
                int(item.get("from_user_id", speaker_id)),
                int(item.get("to_user_id", 0)),
                str(item.get("relation", RELATION_INTRODUCED_AS)),
                label=str(item.get("label", "")),
                evidence=text,
                group_id=group_id,
            )

        delta = str(data.get("impression_delta", "")).strip()
        if delta:
            profile = await self._get_or_create_profile(speaker_id)
            if profile.impression:
                if delta not in profile.impression:
                    profile.impression = f"{profile.impression}；{delta}"
            else:
                profile.impression = delta
            await self._save(profile)

        profile = await self._get_or_create_profile(speaker_id)
        if self.should_refine_cognition(profile):
            await self.schedule_cognition_refine(speaker_id)

    async def _flush_extracts(self, user_id: int) -> None:
        batch = self._pending_extracts.pop(user_id, [])
        self._extract_tasks.pop(user_id, None)
        if not batch:
            return
        merged = batch[-1]
        if len(batch) > 1:
            texts = [b.get("text", "") for b in batch if b.get("text")]
            merged = {**merged, "text": " / ".join(texts)}
        await self._llm_extract_memory(merged)

    async def schedule_memory_extract(
        self,
        user_id: int,
        text: str,
        *,
        nickname: str = "",
        group_id: int | None = None,
        context_lines: list[str] | None = None,
    ) -> None:
        """Schedule debounced LLM memory extraction for a message."""
        if not self._enabled():
            return

        payload = {
            "user_id": user_id,
            "text": text,
            "nickname": nickname,
            "group_id": group_id,
            "context_lines": context_lines or [],
        }
        self._pending_extracts.setdefault(user_id, []).append(payload)

        if user_id in self._extract_tasks and not self._extract_tasks[user_id].done():
            return

        svc = self  # capture for closure

        async def _delayed() -> None:
            delay = svc._config.get_float("USER_MEMORY_BURST_MERGE")
            await svc._clock.sleep(delay)
            await svc._flush_extracts(user_id)

        self._extract_tasks[user_id] = asyncio.create_task(_delayed())

    # ------------------------------------------------------------------
    # Cognition refine
    # ------------------------------------------------------------------

    def should_refine_cognition(
        self, profile: UserProfile, *, has_recent_exchange: bool = False
    ) -> bool:
        if not self._config.get_bool("ENABLE_USER_COGNITION_REFINE"):
            return False
        if has_recent_exchange:
            return True
        delta = profile.interaction_count - profile.cognition_interaction_at_update
        if delta >= self._config.get_int("USER_COGNITION_REFINE_INTERVAL"):
            return True
        if profile.cognition_updated_at:
            for f in profile.facts:
                if (
                    f.active
                    and f.category == FACT_CATEGORY_IDENTITY
                    and f.learned_at > profile.cognition_updated_at
                ):
                    return True
        elif profile.facts or profile.impression:
            return True
        return False

    @staticmethod
    def _supplementary_facts(
        profile: UserProfile, summary: str, limit: int = 3
    ) -> list[str]:
        extras: list[str] = []
        for f in profile.facts:
            if not f.active or f.category == FACT_CATEGORY_IDENTITY:
                continue
            if f.content in summary:
                continue
            extras.append(f.content)
            if len(extras) >= limit:
                break
        return extras

    async def refine_user_cognition(
        self, user_id: int, *, recent_exchange: str = ""
    ) -> str:
        profile = await self._get_or_create_profile(user_id)
        name = display_name(profile)
        facts_lines = [f"- {f.content}" for f in profile.facts if f.active]
        facts_block = "\n".join(facts_lines) if facts_lines else "(暂无)"
        exchange_block = ""
        if recent_exchange.strip():
            exchange_block = f"最近对话：\n{recent_exchange.strip()}\n"

        prompt = _REFINE_PROMPT.format(
            max_chars=self._config.get_int("USER_COGNITION_MAX_CHARS"),
            name=name,
            stage=stage_label(profile.stage),
            old_summary=profile.cognition_summary or "(初次认识，尚无认知)",
            preferred_name=profile.preferred_name or name,
            impression=profile.impression or "(暂无)",
            facts=facts_block,
            exchange=exchange_block,
        )
        try:
            raw = await self._llm.chat(
                [ChatMessage(role="user", content=prompt)],
                max_tokens=200,
                temperature=0.3,
                timeout=10.0,
            )
        except Exception:
            self._emit("DEBUG", "cognition_refine_failed", uid=user_id)
            return profile.cognition_summary

        summary = raw.strip()
        if not summary or summary.startswith("{"):
            self._emit("DEBUG", "cognition_refine_skipped", uid=user_id)
            return profile.cognition_summary

        max_chars = self._config.get_int("USER_COGNITION_MAX_CHARS")
        if len(summary) > max_chars:
            summary = summary[:max_chars]

        profile.cognition_summary = summary
        profile.cognition_updated_at = self._now()
        profile.cognition_interaction_at_update = profile.interaction_count
        await self._save(profile)
        self._emit("INFO", "cognition_refined", uid=user_id, length=len(summary))
        return summary

    async def _flush_refine(self, user_id: int) -> None:
        payload = self._pending_refines.pop(user_id, {})
        self._refine_tasks.pop(user_id, None)
        recent_exchange = str(payload.get("recent_exchange", ""))
        profile = await self._get_or_create_profile(user_id)
        if not self.should_refine_cognition(
            profile, has_recent_exchange=bool(recent_exchange)
        ):
            return
        await self.refine_user_cognition(user_id, recent_exchange=recent_exchange)

    async def schedule_cognition_refine(
        self,
        user_id: int,
        *,
        recent_exchange: str = "",
    ) -> None:
        if not self._enabled() or not self._config.get_bool("ENABLE_USER_COGNITION_REFINE"):
            return

        profile = await self._get_or_create_profile(user_id)
        if not self.should_refine_cognition(
            profile, has_recent_exchange=bool(recent_exchange)
        ):
            return

        existing = self._pending_refines.get(user_id, {})
        if recent_exchange:
            existing["recent_exchange"] = recent_exchange
        self._pending_refines[user_id] = existing

        if user_id in self._refine_tasks and not self._refine_tasks[user_id].done():
            return

        svc = self

        async def _delayed() -> None:
            delay = svc._config.get_float("USER_COGNITION_REFINE_DELAY")
            await svc._clock.sleep(delay)
            await svc._flush_refine(user_id)

        self._refine_tasks[user_id] = asyncio.create_task(_delayed())

    # ------------------------------------------------------------------
    # Prompt context formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _active_facts(profile: UserProfile, limit: int = 5) -> list[str]:
        facts = [f.content for f in profile.facts if f.active and f.category != FACT_CATEGORY_IDENTITY]
        identity = [f.content for f in profile.facts if f.active and f.category == FACT_CATEGORY_IDENTITY]
        return identity + facts[-limit:]

    async def _find_mentioned_user_ids(self, text: str) -> list[int]:
        names = await self._graph.all_names()
        found: list[int] = []
        seen: set[int] = set()
        for name, uid in names.items():
            if name in text and uid not in seen:
                found.append(uid)
                seen.add(uid)
        return found

    async def format_user_context_for_prompt(
        self,
        primary_user_id: int | None = None,
        observation_text: str = "",
        *,
        is_private: bool = False,
    ) -> str:
        if not self._enabled():
            return ""

        blocks: list[str] = []
        related_ids: set[int] = set()

        if primary_user_id:
            profile = await self._get_or_create_profile(primary_user_id)
            name = display_name(profile)
            lines = [
                f"- {name} (QQ {primary_user_id})，关系：{stage_label(profile.stage)}",
            ]
            if profile.cognition_summary:
                lines.append(f"  认知：{profile.cognition_summary}")
            elif profile.impression:
                lines.append(f"  印象：{profile.impression}")
            extras = self._supplementary_facts(profile, profile.cognition_summary)
            if extras:
                lines.append("  补充：" + "；".join(extras))
            blocks.append("【正在对话的人】\n" + "\n".join(lines))
            related_ids.add(primary_user_id)

        mentioned = await self._find_mentioned_user_ids(observation_text)
        related_lines: list[str] = []
        for uid in mentioned:
            if uid in related_ids:
                continue
            related_ids.add(uid)
            profile = await self._get_or_create_profile(uid)
            name = display_name(profile)
            aliases = [a for a in profile.aliases if a != name]
            alias_str = f"/{'/'.join(aliases)}" if aliases else ""
            line = f"- {name}{alias_str} (QQ {uid})"
            if profile.cognition_summary:
                line += "：" + profile.cognition_summary
            else:
                facts = self._active_facts(profile, limit=3)
                if facts:
                    line += "：" + "；".join(facts)
            related_lines.append(line)
        if related_lines:
            blocks.append("【相关的人（本轮提及）】\n" + "\n".join(related_lines))

        # Social edges
        all_names = await self._graph.all_names()
        social_lines: list[str] = []
        uid_set = related_ids or ({primary_user_id} if primary_user_id else set())

        # Collect edges for all related users
        edges_to_check: list[SocialEdge] = []
        for uid in uid_set:
            edges_to_check.extend(await self._graph.edges_from(uid))

        for edge in edges_to_check:
            from_p = await self._get_or_create_profile(edge.from_user_id)
            to_p = await self._get_or_create_profile(edge.to_user_id)
            from_n = display_name(from_p)
            to_n = display_name(to_p)
            if edge.relation == RELATION_INTRODUCED_AS and edge.label:
                social_lines.append(f"- {from_n} 介绍 {edge.label} 就是 {to_n}")
            elif edge.relation == "also_known_as" and edge.label:
                social_lines.append(f"- {to_n} 又名 {edge.label}")
            elif edge.from_user_id == edge.to_user_id and edge.label:
                social_lines.append(f"- {from_n} 自称 {edge.label}")

        for name, uid in all_names.items():
            profile = await self._get_or_create_profile(uid)
            pref = profile.preferred_name
            if pref and pref != name and name in observation_text:
                social_lines.append(f"- {name} 就是 {pref} (QQ {uid})")

        if social_lines:
            blocks.append("【社会关系】\n" + "\n".join(dict.fromkeys(social_lines)))

        return "\n\n".join(blocks)

    async def format_user_brief(self, user_id: int) -> str:
        if not self._enabled():
            return ""
        profile = await self._get_or_create_profile(user_id)
        name = display_name(profile)
        return f"{name}(关系:{stage_label(profile.stage)})"

    async def format_user_profile_summary(self, user_id: int) -> str:
        profile = await self._get_or_create_profile(user_id)
        lines = [
            f"用户: {display_name(profile)} (QQ {user_id})",
            f"关系: {stage_label(profile.stage)}",
            f"互动次数: {profile.interaction_count}",
            f"首选称呼: {profile.preferred_name or '(未设置)'}",
        ]
        if profile.aliases:
            lines.append(f"别名: {', '.join(profile.aliases)}")
        if profile.cognition_summary:
            lines.append(f"认知总结: {profile.cognition_summary}")
            if profile.cognition_updated_at:
                lines.append(f"认知更新: {profile.cognition_updated_at.isoformat()}")
        elif profile.impression:
            lines.append(f"印象: {profile.impression}")
        facts = self._active_facts(profile, limit=10)
        if facts:
            lines.append("事实:")
            for f in facts:
                lines.append(f"  - {f}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Initialization helper
    # ------------------------------------------------------------------

    async def ensure_user_memory_initialized(self) -> None:
        """Phase 2: DB-ready check. JSON→DB migration is in Phase 3."""
        # In Phase 2, this is essentially a no-op since the DB repos
        # handle their own initialization. The real migration happens
        # in Phase 3's migrate-memory command.
        if self._enabled():
            # Verify repos are functional by listing user IDs
            await self._profiles.list_user_ids()
