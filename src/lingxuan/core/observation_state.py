"""ObservationStore: state container replacing MVP's 7 module-level global dicts.

Encapsulates per-group buffers, observe progress, cooldown state, locks,
nickname cache, and debounce task handles into a single object with explicit
lifecycle.  All time-dependent logic uses the injected Clock; config values
are read via ConfigProvider so hot-reload works.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from lingxuan.protocols.clock import Clock
from lingxuan.protocols.config import ConfigProvider
from lingxuan.protocols.messaging import ObservationEntry


@dataclass
class GroupObserveState:
    """Per-group observe state — aligns with MVP GroupObserveState."""

    last_reply_at: float = 0.0
    cooldown_until: float = 0.0
    last_judge_result: str = ""
    last_reply_user_id: int = 0
    observe_in_flight: bool = False
    pending_observe: bool = False


class ObservationStore:
    """Replaces MVP's _buffers / _debounce_tasks / _observe_callbacks /
    _last_observe_len / _group_states / _group_locks / _user_nicknames.

    All state lives on the instance — no module-level mutable globals.
    """

    def __init__(self, config: ConfigProvider, clock: Clock) -> None:
        self._config = config
        self._clock = clock

        # Per-group containers (lazily populated on first access)
        self._buffers: dict[int, list[ObservationEntry]] = {}
        self._last_observe_len: dict[int, int] = {}
        self._group_states: dict[int, GroupObserveState] = {}
        self._group_locks: dict[int, asyncio.Lock] = {}
        self._user_nicknames: dict[int, dict[int, str]] = {}
        self._debounce_tasks: dict[int, asyncio.Task[None] | None] = {}

    # ── helpers ──────────────────────────────────────────────────────────

    def _observe_window(self) -> int:
        return self._config.get_int("GROUP_OBSERVE_WINDOW")

    # ── buffer ───────────────────────────────────────────────────────────

    def append_entry(self, group_id: int, entry: ObservationEntry) -> None:
        """Append a non-bot entry; remember nickname; trim to window."""
        if not entry.is_bot:
            self.remember_nickname(group_id, entry.user_id, entry.nickname)
        buf = self._buffers.setdefault(group_id, [])
        buf.append(entry)
        window = self._observe_window()
        if len(buf) > window:
            self._buffers[group_id] = buf[-window:]

    def append_bot_message(self, group_id: int, text: str) -> None:
        """Append a bot-originated entry using BOT_NAME from config."""
        bot_name = self._config.get_str("BOT_NAME")
        entry = ObservationEntry(
            user_id=0,
            nickname=bot_name,
            text=text,
            is_bot=True,
            ts=self._clock.monotonic(),
        )
        buf = self._buffers.setdefault(group_id, [])
        buf.append(entry)
        window = self._observe_window()
        if len(buf) > window:
            self._buffers[group_id] = buf[-window:]

    def buffer(self, group_id: int) -> list[ObservationEntry]:
        return self._buffers.get(group_id, [])

    def recent(self, group_id: int, limit: int = 5) -> list[ObservationEntry]:
        return list(self._buffers.get(group_id, [])[-limit:])

    def buffer_len(self, group_id: int) -> int:
        return len(self._buffers.get(group_id, []))

    # ── public enumeration ────────────────────────────────────────────────

    def active_group_ids(self) -> list[int]:
        """Return IDs of groups that have buffered entries."""
        return [gid for gid, buf in self._buffers.items() if buf]

    # ── observe progress ─────────────────────────────────────────────────

    def mark_observed(self, group_id: int) -> None:
        """Record current buffer length so has_new_since_observe can diff."""
        self._last_observe_len[group_id] = len(self._buffers.get(group_id, []))

    def has_new_since_observe(self, group_id: int) -> bool:
        buf_len = len(self._buffers.get(group_id, []))
        return buf_len > self._last_observe_len.get(group_id, 0)

    # ── state ────────────────────────────────────────────────────────────

    def state(self, group_id: int) -> GroupObserveState:
        return self._group_states.setdefault(group_id, GroupObserveState())

    def lock(self, group_id: int) -> asyncio.Lock:
        return self._group_locks.setdefault(group_id, asyncio.Lock())

    # ── nickname cache ───────────────────────────────────────────────────

    def remember_nickname(self, group_id: int, user_id: int, nickname: str) -> None:
        if user_id and nickname:
            self._user_nicknames.setdefault(group_id, {})[user_id] = nickname

    def nickname_for(self, group_id: int, user_id: int) -> str:
        return self._user_nicknames.get(group_id, {}).get(user_id) or str(user_id)

    # ── debounce task handle ─────────────────────────────────────────────

    def set_debounce_task(self, group_id: int, task: asyncio.Task[None] | None) -> None:
        self._debounce_tasks[group_id] = task

    def get_debounce_task(self, group_id: int) -> asyncio.Task[None] | None:
        return self._debounce_tasks.get(group_id)

    # ── reset (for testing & lifecycle) ──────────────────────────────────

    def reset(self) -> None:
        """Clear all state across every group."""
        self._buffers.clear()
        self._last_observe_len.clear()
        self._group_states.clear()
        self._group_locks.clear()
        self._user_nicknames.clear()
        self._debounce_tasks.clear()

    def reset_group(self, group_id: int) -> None:
        """Clear state for a single group."""
        self._buffers.pop(group_id, None)
        self._last_observe_len.pop(group_id, None)
        self._group_states.pop(group_id, None)
        self._group_locks.pop(group_id, None)
        self._user_nicknames.pop(group_id, None)
        self._debounce_tasks.pop(group_id, None)
