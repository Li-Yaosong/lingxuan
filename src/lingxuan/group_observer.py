from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import nonebot

from lingxuan._config import _cfg

logger = nonebot.logger

ObserveCallback = Callable[[int], Awaitable[None]]


@dataclass
class ObservationEntry:
    user_id: int
    nickname: str
    text: str
    at_bot: bool = False
    reply_to_bot: bool = False
    at_user_ids: list[int] = field(default_factory=list)
    is_bot: bool = False
    ts: float = field(default_factory=time.time)


@dataclass
class GroupObserveState:
    last_reply_at: float = 0.0
    cooldown_until: float = 0.0
    last_judge_result: str = ""
    last_reply_user_id: int = 0
    observe_in_flight: bool = False
    pending_observe: bool = False


_buffers: dict[int, list[ObservationEntry]] = {}
_debounce_tasks: dict[int, asyncio.Task[None]] = {}
_observe_callbacks: dict[int, ObserveCallback] = {}
_last_observe_len: dict[int, int] = {}
_group_states: dict[int, GroupObserveState] = {}
_group_locks: dict[int, asyncio.Lock] = {}
_user_nicknames: dict[int, dict[int, str]] = {}


def remember_user_nickname(group_id: int, user_id: int, nickname: str) -> None:
    if user_id and nickname:
        _user_nicknames.setdefault(group_id, {})[user_id] = nickname


def nickname_for(group_id: int, user_id: int) -> str:
    return _user_nicknames.get(group_id, {}).get(user_id) or str(user_id)


def _state(group_id: int) -> GroupObserveState:
    return _group_states.setdefault(group_id, GroupObserveState())


def get_group_lock(group_id: int) -> asyncio.Lock:
    return _group_locks.setdefault(group_id, asyncio.Lock())


def is_observe_in_flight(group_id: int) -> bool:
    return _state(group_id).observe_in_flight


def set_pending_observe(group_id: int) -> None:
    _state(group_id).pending_observe = True


def clear_pending_observe(group_id: int) -> None:
    _state(group_id).pending_observe = False


def has_pending_observe(group_id: int) -> bool:
    return _state(group_id).pending_observe


@asynccontextmanager
async def group_reply_session(group_id: int) -> AsyncIterator[None]:
    lock = get_group_lock(group_id)
    async with lock:
        yield


def _trim_buffer(group_id: int) -> None:
    buf = _buffers.get(group_id, [])
    window = _cfg().get_int("GROUP_OBSERVE_WINDOW")
    if len(buf) > window:
        _buffers[group_id] = buf[-window:]


def append_entry(group_id: int, entry: ObservationEntry) -> None:
    if not entry.is_bot:
        remember_user_nickname(group_id, entry.user_id, entry.nickname)
    _buffers.setdefault(group_id, []).append(entry)
    _trim_buffer(group_id)
    if not entry.is_bot and _state(group_id).observe_in_flight:
        set_pending_observe(group_id)


def append_bot_message(group_id: int, text: str) -> None:
    _buffers.setdefault(group_id, []).append(
        ObservationEntry(
            user_id=0,
            nickname=_cfg().get_str("BOT_NAME"),
            text=text,
            is_bot=True,
        )
    )
    _trim_buffer(group_id)


def _format_entry_line(
    name: str,
    text: str,
    *,
    at_bot: bool = False,
    at_other_names: list[str] | None = None,
) -> str:
    bot_name = _cfg().get_str("BOT_NAME")
    markers: list[str] = []
    if at_bot:
        markers.append(f"@{bot_name}")
    if at_other_names:
        markers.extend(f"@{n}" for n in at_other_names)
    suffix = f" -> {' '.join(markers)}" if markers else ""
    return f"[{name}{suffix}]: {text}"


def format_observation(group_id: int) -> str:
    entries = _buffers.get(group_id, [])
    bot_name = _cfg().get_str("BOT_NAME")
    burst_merge_window = _cfg().get_float("GROUP_BURST_MERGE_WINDOW")
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
            if j > i and cur.ts - entries[j - 1].ts > burst_merge_window:
                break
            if cur.text.strip():
                merged_texts.append(cur.text)
            has_at = has_at or cur.at_bot
            merged_at_ids.extend(cur.at_user_ids)
            j += 1

        name = entry.nickname or str(entry.user_id)
        text = " / ".join(merged_texts) if merged_texts else entry.text
        other_names = [
            nickname_for(group_id, uid)
            for uid in dict.fromkeys(merged_at_ids)
        ]
        lines.append(
            _format_entry_line(
                name,
                text,
                at_bot=has_at,
                at_other_names=other_names if other_names and not has_at else None,
            )
        )
        i = j
    return "\n".join(lines)


def get_recent_entries(group_id: int, limit: int = 5) -> list[ObservationEntry]:
    return list(_buffers.get(group_id, [])[-limit:])


def get_buffer_len(group_id: int) -> int:
    return len(_buffers.get(group_id, []))


def _iter_user_entries(group_id: int) -> list[ObservationEntry]:
    return [e for e in _buffers.get(group_id, []) if not e.is_bot]


def get_last_user_text(group_id: int) -> str:
    for entry in reversed(_buffers.get(group_id, [])):
        if not entry.is_bot and entry.text.strip():
            return entry.text
    return ""


def latest_user_replies_to_bot(group_id: int) -> bool:
    for entry in reversed(_buffers.get(group_id, [])):
        if entry.is_bot:
            continue
        return entry.reply_to_bot
    return False


def latest_user_at_bot(group_id: int) -> bool:
    for entry in reversed(_buffers.get(group_id, [])):
        if entry.is_bot:
            continue
        return entry.at_bot
    return False


def get_latest_user_entry(group_id: int) -> ObservationEntry | None:
    for entry in reversed(_buffers.get(group_id, [])):
        if not entry.is_bot and entry.text.strip():
            return entry
    return None


def is_knowledge_question(text: str) -> bool:
    if not text.strip():
        return False
    hints = (
        "你知道吗",
        "你知道",
        "是谁",
        "叫什么",
        "谁叫",
        "谁是小",
        "谁是",
        "之前说过",
        "不是给你说过",
        "还记得",
        "认识吗",
        "有没有说过",
    )
    return any(h in text for h in hints)


def is_introducing_other(entry: ObservationEntry) -> bool:
    if entry.at_bot or not entry.at_user_ids:
        return False
    text = entry.text
    hints = ("这位", "这就是", "他是", "她是", "就是", "大名鼎鼎", "介绍")
    return any(h in text for h in hints)


def latest_message_ats_others_only(group_id: int) -> bool:
    entry = get_latest_user_entry(group_id)
    if not entry or entry.at_bot or not entry.at_user_ids:
        return False
    return True


def should_skip_observe(group_id: int) -> bool:
    """@ 了别人且未 @ 机器人、亦非介绍场景 → 不抢话。"""
    entry = get_latest_user_entry(group_id)
    if not entry:
        return False
    if entry.at_bot:
        return False
    if not entry.at_user_ids:
        return False
    return not is_introducing_other(entry)


def is_followup_after_bot(group_id: int) -> bool:
    state = _state(group_id)
    entries = _buffers.get(group_id, [])
    bot_name = _cfg().get_str("BOT_NAME")
    followup_window = _cfg().get_float("GROUP_FOLLOWUP_WINDOW")
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
        if entry.ts - last_bot_ts > followup_window:
            return False
        if is_knowledge_question(entry.text):
            return False
        if entry.at_user_ids and not entry.at_bot:
            return False
        if entry.at_bot or entry.reply_to_bot or bot_name in entry.text:
            return True
        if state.last_reply_user_id and entry.user_id == state.last_reply_user_id:
            if len(entry.text) <= 20 and not is_knowledge_question(entry.text):
                return True
        return False
    return False


def is_directed_at_bot(text: str) -> bool:
    bot_name = _cfg().get_str("BOT_NAME")
    if not text.strip():
        return False
    if bot_name in text:
        return True
    hints = ("叫你", "问你", "回她", "回他", "回复一下", "回答", "出来答", "回一下")
    return any(h in text for h in hints) and "你" in text


def is_seeking_engagement(text: str) -> bool:
    """群里诉苦、求助、等人接话——适合规则短路后流式回复。"""
    if not text.strip():
        return False
    emotional = (
        "晚安",
        "孤独",
        "寂寞",
        "难过",
        "伤心",
        "委屈",
        "好可怜",
        "陪陪我",
        "说说话",
    )
    asking = (
        "有人能",
        "谁能",
        "帮帮",
        "怎么办",
        "有人吗",
        "在吗",
    )
    if any(k in text for k in emotional):
        return True
    if any(k in text for k in asking) and (
        "吗" in text or "呢" in text or "?" in text or "？" in text
    ):
        return True
    return False


def should_bypass_cooldown(group_id: int) -> bool:
    if latest_user_at_bot(group_id) or latest_user_replies_to_bot(group_id):
        return True
    last_text = get_last_user_text(group_id)
    if is_knowledge_question(last_text):
        return True
    if is_directed_at_bot(last_text) or _cfg().get_str("BOT_NAME") in last_text:
        return True
    if is_seeking_engagement(last_text):
        return True
    return is_followup_after_bot(group_id)


def get_reply_target(group_id: int) -> tuple[int, str] | None:
    entry = get_latest_user_entry(group_id)
    if not entry:
        return None

    if entry.at_bot:
        return entry.user_id, entry.nickname or str(entry.user_id)

    if entry.at_user_ids and not entry.at_bot:
        target_uid = entry.at_user_ids[0]
        return target_uid, nickname_for(group_id, target_uid)

    return entry.user_id, entry.nickname or str(entry.user_id)


def has_new_messages_since_observe(group_id: int) -> bool:
    buf_len = len(_buffers.get(group_id, []))
    return buf_len > _last_observe_len.get(group_id, 0)


def mark_observed(group_id: int) -> None:
    _last_observe_len[group_id] = len(_buffers.get(group_id, []))


def mark_last_trigger(group_id: int, reply_user_id: int = 0) -> None:
    now = time.time()
    state = _state(group_id)
    state.last_reply_at = now
    state.cooldown_until = now + _cfg().get_float("GROUP_OBSERVE_COOLDOWN")
    if reply_user_id:
        state.last_reply_user_id = reply_user_id


def is_in_cooldown(group_id: int) -> bool:
    return time.time() < _state(group_id).cooldown_until


def record_judge_result(group_id: int, result: str) -> None:
    _state(group_id).last_judge_result = result


def get_observe_state(group_id: int) -> dict[str, Any]:
    state = _state(group_id)
    return {
        "buffer_len": get_buffer_len(group_id),
        "last_judge_result": state.last_judge_result,
        "in_cooldown": is_in_cooldown(group_id),
        "cooldown_remaining": max(0.0, state.cooldown_until - time.time()),
        "observe_in_flight": state.observe_in_flight,
        "pending_observe": state.pending_observe,
    }


def register_observe_callback(group_id: int, callback: ObserveCallback) -> None:
    _observe_callbacks[group_id] = callback


async def _run_debounced_observe(group_id: int) -> None:
    try:
        await asyncio.sleep(_cfg().get_float("GROUP_OBSERVE_DELAY"))
    except asyncio.CancelledError:
        return
    _debounce_tasks.pop(group_id, None)
    if not has_new_messages_since_observe(group_id):
        logger.info(
            "observe debounce skipped (no new msgs) group={} buf={} last={}",
            group_id,
            get_buffer_len(group_id),
            _last_observe_len.get(group_id, 0),
        )
        return
    if is_observe_in_flight(group_id):
        set_pending_observe(group_id)
        logger.info("observe debounce deferred (in flight) group={}", group_id)
        return
    callback = _observe_callbacks.get(group_id)
    if callback is None:
        logger.warning("observe callback missing group={}", group_id)
        return
    logger.info("observe debounce triggered group={}", group_id)
    try:
        await callback(group_id)
    except Exception:
        logger.exception("observe callback error group={}", group_id)


def schedule_observe(group_id: int) -> None:
    if is_observe_in_flight(group_id):
        set_pending_observe(group_id)
        logger.info("observe pending (in flight) group={}", group_id)
        return
    task = _debounce_tasks.pop(group_id, None)
    if task is not None:
        task.cancel()
    _debounce_tasks[group_id] = asyncio.create_task(_run_debounced_observe(group_id))
    logger.info("observe scheduled group={} delay={}s", group_id, _cfg().get_float("GROUP_OBSERVE_DELAY"))


async def run_observe_loop(group_id: int, callback: ObserveCallback) -> None:
    state = _state(group_id)
    if state.observe_in_flight:
        set_pending_observe(group_id)
        return
    state.observe_in_flight = True
    try:
        while True:
            await callback(group_id)
            if not state.pending_observe or not has_new_messages_since_observe(group_id):
                clear_pending_observe(group_id)
                break
            clear_pending_observe(group_id)
            logger.info("observe rerun (pending merged) group={}", group_id)
    except Exception:
        logger.exception("observe loop error group={}", group_id)
    finally:
        state.observe_in_flight = False
        if state.pending_observe and has_new_messages_since_observe(group_id):
            clear_pending_observe(group_id)
            schedule_observe(group_id)
