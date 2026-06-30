from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import nonebot

from lingxuan.config import BOT_NAME, GROUP_OBSERVE_DELAY, GROUP_OBSERVE_WINDOW

logger = nonebot.logger

ObserveCallback = Callable[[int], Awaitable[None]]


@dataclass
class ObservationEntry:
    user_id: int
    nickname: str
    text: str
    at_bot: bool = False
    reply_to_bot: bool = False
    is_bot: bool = False
    ts: float = field(default_factory=time.time)


_buffers: dict[int, list[ObservationEntry]] = {}
_debounce_tasks: dict[int, asyncio.Task[None]] = {}
_observe_callbacks: dict[int, ObserveCallback] = {}
_last_observe_len: dict[int, int] = {}


def _trim_buffer(group_id: int) -> None:
    buf = _buffers.get(group_id, [])
    if len(buf) > GROUP_OBSERVE_WINDOW:
        _buffers[group_id] = buf[-GROUP_OBSERVE_WINDOW:]


def append_entry(group_id: int, entry: ObservationEntry) -> None:
    _buffers.setdefault(group_id, []).append(entry)
    _trim_buffer(group_id)


def append_bot_message(group_id: int, text: str) -> None:
    append_entry(
        group_id,
        ObservationEntry(
            user_id=0,
            nickname=BOT_NAME,
            text=text,
            is_bot=True,
        ),
    )


def format_observation(group_id: int) -> str:
    lines: list[str] = []
    for entry in _buffers.get(group_id, []):
        name = entry.nickname or str(entry.user_id)
        lines.append(f"[{name}]: {entry.text}")
    return "\n".join(lines)


def get_last_user_text(group_id: int) -> str:
    for entry in reversed(_buffers.get(group_id, [])):
        if not entry.is_bot and entry.text.strip():
            return entry.text
    return ""


def get_reply_target(group_id: int) -> tuple[int, str] | None:
    for entry in reversed(_buffers.get(group_id, [])):
        if entry.is_bot:
            continue
        if entry.text.strip():
            return entry.user_id, entry.nickname or str(entry.user_id)
    return None


def has_new_messages_since_observe(group_id: int) -> bool:
    buf_len = len(_buffers.get(group_id, []))
    return buf_len > _last_observe_len.get(group_id, 0)


def mark_observed(group_id: int) -> None:
    _last_observe_len[group_id] = len(_buffers.get(group_id, []))


def register_observe_callback(group_id: int, callback: ObserveCallback) -> None:
    _observe_callbacks[group_id] = callback


async def _run_debounced_observe(group_id: int) -> None:
    try:
        await asyncio.sleep(GROUP_OBSERVE_DELAY)
    except asyncio.CancelledError:
        return
    _debounce_tasks.pop(group_id, None)
    if not has_new_messages_since_observe(group_id):
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
    task = _debounce_tasks.pop(group_id, None)
    if task is not None:
        task.cancel()
    _debounce_tasks[group_id] = asyncio.create_task(_run_debounced_observe(group_id))
    logger.info("observe scheduled group={} delay={}s", group_id, GROUP_OBSERVE_DELAY)
