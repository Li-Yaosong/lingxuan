"""ObservationService: group observation + judge orchestration.

Converges the decision logic that was scattered across MVP ``group_observer.py``
(rules) and ``handlers/group.py::_observe_group`` (short-circuit + LLM judge)
into a single Core service.  Includes debounce, cooldown, rule short-circuit,
LLM judge, and reply-trigger orchestration.

All time-dependent logic uses the injected ``Clock``; config via
``ConfigProvider``.  No framework/IO imports.
"""

from __future__ import annotations

import asyncio

from lingxuan.core.group_reply_executor import GroupReplyExecutor
from lingxuan.core.observation_state import ObservationStore
from lingxuan.core.prompting import PromptBuilder, build_judge_prompt, should_skip_reply_locally
from lingxuan.protocols.clock import Clock
from lingxuan.protocols.config import ConfigProvider
from lingxuan.protocols.llm import LLMProvider
from lingxuan.protocols.memory import MemoryService, UserMemoryService
from lingxuan.protocols.messaging import (
    InboundMessage,
    ObservationEntry,
    SessionId,
)
from lingxuan.protocols.repositories import SessionRepository, StoredMessage


# ---------------------------------------------------------------------------
# Pure rule functions (migrated from MVP group_observer.py)
# ---------------------------------------------------------------------------

def is_knowledge_question(text: str) -> bool:
    if not text.strip():
        return False
    hints = (
        "你知道吗", "你知道", "是谁", "叫什么", "谁叫",
        "谁是小", "谁是", "之前说过", "不是给你说过",
        "还记得", "认识吗", "有没有说过",
    )
    return any(h in text for h in hints)


def is_introducing_other(entry: ObservationEntry, bot_name: str) -> bool:
    if entry.at_bot or not entry.at_user_ids:
        return False
    hints = ("这位", "这就是", "他是", "她是", "就是", "大名鼎鼎", "介绍")
    return any(h in entry.text for h in hints)


def is_directed_at_bot(text: str, bot_name: str) -> bool:
    if not text.strip():
        return False
    if bot_name in text:
        return True
    hints = ("叫你", "问你", "回她", "回他", "回复一下", "回答", "出来答", "回一下")
    return any(h in text for h in hints) and "你" in text


def is_seeking_engagement(text: str) -> bool:
    """Emotional distress or seeking help — suitable for rule short-circuit reply."""
    if not text.strip():
        return False
    emotional = (
        "晚安", "孤独", "寂寞", "难过", "伤心",
        "委屈", "好可怜", "陪陪我", "说说话",
    )
    asking = ("有人能", "谁能", "帮帮", "怎么办", "有人吗", "在吗")
    if any(k in text for k in emotional):
        return True
    if any(k in text for k in asking) and (
        "吗" in text or "呢" in text or "?" in text or "？" in text
    ):
        return True
    return False


# ---------------------------------------------------------------------------
# ObservationService
# ---------------------------------------------------------------------------


class ObservationService:
    """Group observation orchestration: debounce, rules, judge, reply.

    Replaces MVP's ``group_observer`` + ``handlers/group._observe_group``.
    All time goes through ``clock``; all config via ``config``.
    """

    def __init__(
        self,
        store: ObservationStore,
        executor: GroupReplyExecutor,
        llm: LLMProvider,
        sessions: SessionRepository,
        memory: MemoryService,
        user_memory: UserMemoryService,
        config: ConfigProvider,
        clock: Clock,
    ) -> None:
        self._store = store
        self._executor = executor
        self._llm = llm
        self._sessions = sessions
        self._memory = memory
        self._user_memory = user_memory
        self._config = config
        self._clock = clock

    # ── config helpers ────────────────────────────────────────────────────

    @property
    def _bot_name(self) -> str:
        return self._config.get_str("BOT_NAME")

    @property
    def _observe_delay(self) -> float:
        return self._config.get_float("GROUP_OBSERVE_DELAY")

    @property
    def _cooldown_seconds(self) -> float:
        return self._config.get_float("GROUP_OBSERVE_COOLDOWN")

    @property
    def _burst_merge_window(self) -> float:
        return self._config.get_float("GROUP_BURST_MERGE_WINDOW")

    @property
    def _followup_window(self) -> float:
        return self._config.get_float("GROUP_FOLLOWUP_WINDOW")

    @property
    def _group_chat_context(self) -> int:
        return self._config.get_int("GROUP_CHAT_CONTEXT")

    @property
    def _observe_enabled(self) -> bool:
        return self._config.get_bool("ENABLE_GROUP_OBSERVE")

    # ── buffer accessors (delegated to store) ─────────────────────────────

    def _buffer(self, group_id: int) -> list[ObservationEntry]:
        return self._store.buffer(group_id)

    def _get_latest_user_entry(self, group_id: int) -> ObservationEntry | None:
        for entry in reversed(self._buffer(group_id)):
            if not entry.is_bot and entry.text.strip():
                return entry
        return None

    def _get_last_user_text(self, group_id: int) -> str:
        for entry in reversed(self._buffer(group_id)):
            if not entry.is_bot and entry.text.strip():
                return entry.text
        return ""

    def _latest_user_at_bot(self, group_id: int) -> bool:
        for entry in reversed(self._buffer(group_id)):
            if entry.is_bot:
                continue
            return entry.at_bot
        return False

    def _latest_user_replies_to_bot(self, group_id: int) -> bool:
        for entry in reversed(self._buffer(group_id)):
            if entry.is_bot:
                continue
            return entry.reply_to_bot
        return False

    # ── rule predicates ───────────────────────────────────────────────────

    def should_skip_observe(self, group_id: int) -> bool:
        """@ others only (not @bot, not introducing) → don't interrupt."""
        entry = self._get_latest_user_entry(group_id)
        if not entry:
            return False
        if entry.at_bot:
            return False
        if not entry.at_user_ids:
            return False
        return not is_introducing_other(entry, self._bot_name)

    def is_followup_after_bot(self, group_id: int) -> bool:
        state = self._store.state(group_id)
        entries = self._buffer(group_id)
        last_bot_ts = 0.0
        for entry in reversed(entries):
            if entry.is_bot:
                last_bot_ts = entry.ts
                break
        if last_bot_ts <= 0:
            return False

        for entry in reversed(entries):
            if entry.is_bot:
                continue
            if not entry.text.strip():
                continue
            if entry.ts - last_bot_ts > self._followup_window:
                return False
            if is_knowledge_question(entry.text):
                return False
            if entry.at_user_ids and not entry.at_bot:
                return False
            if entry.at_bot or entry.reply_to_bot or self._bot_name in entry.text:
                return True
            if state.last_reply_user_id and entry.user_id == state.last_reply_user_id:
                if len(entry.text) <= 20 and not is_knowledge_question(entry.text):
                    return True
            return False
        return False

    def should_bypass_cooldown(self, group_id: int) -> bool:
        if self._latest_user_at_bot(group_id) or self._latest_user_replies_to_bot(group_id):
            return True
        last_text = self._get_last_user_text(group_id)
        if is_knowledge_question(last_text):
            return True
        if is_directed_at_bot(last_text, self._bot_name) or self._bot_name in last_text:
            return True
        if is_seeking_engagement(last_text):
            return True
        return self.is_followup_after_bot(group_id)

    def is_in_cooldown(self, group_id: int) -> bool:
        return self._clock.monotonic() < self._store.state(group_id).cooldown_until

    def mark_last_trigger(self, group_id: int, reply_user_id: int = 0) -> None:
        now = self._clock.monotonic()
        state = self._store.state(group_id)
        state.last_reply_at = now
        state.cooldown_until = now + self._cooldown_seconds
        if reply_user_id:
            state.last_reply_user_id = reply_user_id

    def get_reply_target(self, group_id: int) -> tuple[int, str] | None:
        entry = self._get_latest_user_entry(group_id)
        if not entry:
            return None
        if entry.at_bot:
            return entry.user_id, entry.nickname or str(entry.user_id)
        if entry.at_user_ids and not entry.at_bot:
            target_uid = entry.at_user_ids[0]
            return target_uid, self._store.nickname_for(group_id, target_uid)
        return entry.user_id, entry.nickname or str(entry.user_id)

    # ── short-circuit (migrated from handlers/group._should_shortcircuit_judge)

    def _should_shortcircuit_judge(self, group_id: int) -> tuple[bool, str]:
        if self._latest_user_at_bot(group_id):
            return True, "at_bot"
        if self._latest_user_replies_to_bot(group_id):
            return True, "reply_to_bot"
        last_text = self._get_last_user_text(group_id)
        if self._bot_name in last_text:
            return True, "name_mention"
        if is_directed_at_bot(last_text, self._bot_name):
            return True, "directed_request"
        if is_seeking_engagement(last_text):
            return True, "engagement"
        entry = self._get_latest_user_entry(group_id)
        if entry and is_introducing_other(entry, self._bot_name):
            return True, "intro_other"
        if self.is_followup_after_bot(group_id):
            return True, "followup"
        return False, ""

    # ── format_observation ────────────────────────────────────────────────

    def format_observation(self, group_id: int) -> str:
        """Format buffered entries for judge / context, merging bursts."""
        entries = self._buffer(group_id)
        bot_name = self._bot_name
        lines: list[str] = []
        i = 0
        while i < len(entries):
            entry = entries[i]
            if entry.is_bot:
                name = entry.nickname or bot_name
                lines.append(f"[{name}]: {entry.text}")
                i += 1
                continue

            merged_texts: list[str] = []
            has_at = False
            merged_at_ids: list[int] = []
            j = i
            while j < len(entries):
                cur = entries[j]
                if cur.is_bot:
                    break
                if cur.user_id != entry.user_id:
                    break
                if j > i and cur.ts - entries[j - 1].ts > self._burst_merge_window:
                    break
                if cur.text.strip():
                    merged_texts.append(cur.text)
                has_at = has_at or cur.at_bot
                merged_at_ids.extend(cur.at_user_ids)
                j += 1

            name = entry.nickname or str(entry.user_id)
            text = " / ".join(merged_texts) if merged_texts else entry.text
            # Build @ markers
            markers: list[str] = []
            if has_at:
                markers.append(f"@{bot_name}")
            if not has_at and merged_at_ids:
                for uid in dict.fromkeys(merged_at_ids):
                    markers.append(f"@{self._store.nickname_for(group_id, uid)}")
            suffix = f" -> {' '.join(markers)}" if markers else ""
            lines.append(f"[{name}{suffix}]: {text}")
            i = j
        return "\n".join(lines)

    # ── core orchestration ────────────────────────────────────────────────

    async def on_group_message(self, inbound: InboundMessage) -> None:
        """Entry point: buffer the message and schedule observation.

        Direct-@ messages are NOT handled here; they belong to DialogueService
        (P1-08).  This service only handles passive observation.
        """
        group_id = inbound.group_id
        if group_id is None:
            return

        obs_entry = ObservationEntry(
            user_id=inbound.actor.user_id,
            nickname=inbound.actor.nickname,
            text=inbound.text,
            at_bot=inbound.at_bot,
            reply_to_bot=inbound.reply_to_bot,
            at_user_ids=list(inbound.at_user_ids),
            ts=self._clock.monotonic(),
        )
        self._store.append_entry(group_id, obs_entry)

        # Write user message to session memory
        session_id = SessionId(kind="group", peer_id=group_id)
        nickname = inbound.actor.nickname or str(inbound.actor.user_id)
        await self._sessions.append_message(
            session_id,
            StoredMessage(
                role="user",
                content=f"[{nickname}]: {inbound.text}",
                user_id=inbound.actor.user_id,
            ),
        )

        if self._observe_enabled:
            self._schedule_observe(group_id)

    def _schedule_observe(self, group_id: int) -> None:
        """Debounce: cancel old task, sleep delay, then run _observe."""
        state = self._store.state(group_id)
        if state.observe_in_flight:
            state.pending_observe = True
            return

        old_task = self._store.get_debounce_task(group_id)
        if old_task is not None:
            old_task.cancel()

        task = asyncio.create_task(self._run_debounced_observe(group_id))
        self._store.set_debounce_task(group_id, task)

    async def _run_debounced_observe(self, group_id: int) -> None:
        try:
            await self._clock.sleep(self._observe_delay)
        except asyncio.CancelledError:
            return
        self._store.set_debounce_task(group_id, None)
        if not self._store.has_new_since_observe(group_id):
            return
        state = self._store.state(group_id)
        if state.observe_in_flight:
            state.pending_observe = True
            return
        await self._run_observe_loop(group_id)

    async def _run_observe_loop(self, group_id: int) -> None:
        """Aligns with MVP run_observe_loop: serialize, merge pending."""
        state = self._store.state(group_id)
        if state.observe_in_flight:
            state.pending_observe = True
            return
        state.observe_in_flight = True
        try:
            while True:
                async with self._store.lock(group_id):
                    await self._observe(group_id)
                if not state.pending_observe or not self._store.has_new_since_observe(group_id):
                    state.pending_observe = False
                    break
                state.pending_observe = False
        except Exception:
            # Swallow to avoid crashing the loop; in production a LogSink
            # would record this — kept minimal for Core layer.
            pass
        finally:
            state.observe_in_flight = False
            if state.pending_observe and self._store.has_new_since_observe(group_id):
                state.pending_observe = False
                self._schedule_observe(group_id)

    async def _observe(self, group_id: int) -> None:
        """Core observation logic — aligns with MVP _observe_group."""
        if not self._observe_enabled:
            self._store.mark_observed(group_id)
            return

        observation = self.format_observation(group_id)
        if not observation.strip():
            self._store.mark_observed(group_id)
            return

        if self.should_skip_observe(group_id):
            self._store.mark_observed(group_id)
            return

        shortcircuit, reason = self._should_shortcircuit_judge(group_id)
        bypass = self.should_bypass_cooldown(group_id)

        if not shortcircuit and not bypass and self.is_in_cooldown(group_id):
            self._store.mark_observed(group_id)
            return

        if shortcircuit:
            should_reply = True
            self._store.state(group_id).last_judge_result = f"yes:{reason}"
        else:
            last_text = self._get_last_user_text(group_id)
            if should_skip_reply_locally(last_text):
                self._store.state(group_id).last_judge_result = "no:local_skip"
                self._store.mark_observed(group_id)
                return
            should_reply = await self._llm.judge(
                build_judge_prompt(
                    observation,
                    bot_name=self._bot_name,
                )
            )
            self._store.state(group_id).last_judge_result = "yes" if should_reply else "no"
            if not should_reply:
                self._store.mark_observed(group_id)
                return

        target = self.get_reply_target(group_id)
        if not target:
            self._store.mark_observed(group_id)
            return

        await self._send_group_reply(group_id, target, observation)
        self._store.mark_observed(group_id)

    # ── group reply execution ─────────────────────────────────────────────

    async def _send_group_reply(
        self,
        group_id: int,
        target: tuple[int, str],
        observation: str,
    ) -> None:
        """Generate and send a group reply via GroupReplyExecutor."""
        user_id, nickname = target
        session_id = SessionId(kind="group", peer_id=group_id)

        reply_text = await self._executor.execute(
            session_id=session_id,
            observation_text=observation,
            at_user_id=user_id,
        )

        if reply_text:
            # Write assistant message to session memory
            await self._sessions.append_message(
                session_id,
                StoredMessage(role="assistant", content=reply_text),
            )

            # Record bot message in observation buffer
            self._store.append_bot_message(group_id, reply_text)

            # Mark observation state so scheduler won't re-trigger
            self.mark_last_trigger(group_id, reply_user_id=user_id)

            # Schedule summarize (aligns with MVP direct-@ path)
            self._memory.schedule_summarize(session_id)

            # Schedule cognition refine (aligns with MVP direct-@ path)
            await self._user_memory.schedule_cognition_refine(
                user_id,
                recent_exchange=_format_exchange(
                    self._bot_name, nickname, observation, reply_text
                ),
            )

    def _format_entities_text(self, group_id: int, session_id: SessionId) -> str:
        """Format group entities as context text.

        # TODO(Phase 2): implement with SessionRepository.get_entities
        """
        return ""


def _format_exchange(
    bot_name: str, nickname: str, user_text: str, bot_reply: str
) -> str:
    """Format a user-bot exchange for cognition refine — aligns with MVP."""
    return f"用户[{nickname}]: {user_text}\n{bot_name}: {bot_reply}"
