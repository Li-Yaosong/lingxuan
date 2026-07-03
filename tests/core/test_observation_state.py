"""Tests for core/observation_state.py — ObservationStore."""

from __future__ import annotations

import asyncio

import pytest

from lingxuan.core.observation_state import GroupObserveState, ObservationStore
from lingxuan.protocols.messaging import ObservationEntry
from tests.fakes.clock import FakeClock
from tests.fakes.config import FakeConfigProvider


def _make_store(**config_overrides) -> ObservationStore:
    return ObservationStore(
        config=FakeConfigProvider(config_overrides),
        clock=FakeClock(),
    )


def _entry(user_id: int = 1, nickname: str = "test", text: str = "hi", **kw) -> ObservationEntry:
    return ObservationEntry(user_id=user_id, nickname=nickname, text=text, **kw)


# ── buffer trimming ──────────────────────────────────────────────────────


class TestBufferTrimming:
    def test_append_within_window_keeps_all(self):
        store = _make_store(GROUP_OBSERVE_WINDOW=5)
        for i in range(5):
            store.append_entry(1, _entry(text=f"msg{i}"))
        assert store.buffer_len(1) == 5

    def test_append_exceeds_window_trims_to_window(self):
        store = _make_store(GROUP_OBSERVE_WINDOW=3)
        for i in range(7):
            store.append_entry(1, _entry(text=f"msg{i}"))
        buf = store.buffer(1)
        assert len(buf) == 3
        # Kept the most recent 3
        assert [e.text for e in buf] == ["msg4", "msg5", "msg6"]

    def test_bot_message_also_trims(self):
        store = _make_store(GROUP_OBSERVE_WINDOW=2)
        store.append_entry(1, _entry(text="user1"))
        store.append_entry(1, _entry(text="user2"))
        store.append_bot_message(1, "bot says hi")
        buf = store.buffer(1)
        assert len(buf) == 2
        assert buf[-1].text == "bot says hi"
        assert buf[-1].is_bot is True


# ── mark_observed / has_new_since_observe ────────────────────────────────


class TestObserveProgress:
    def test_no_new_after_mark(self):
        store = _make_store()
        store.append_entry(1, _entry(text="msg1"))
        store.mark_observed(1)
        assert store.has_new_since_observe(1) is False

    def test_new_after_append(self):
        store = _make_store()
        store.append_entry(1, _entry(text="msg1"))
        store.mark_observed(1)
        store.append_entry(1, _entry(text="msg2"))
        assert store.has_new_since_observe(1) is True

    def test_mark_observed_tracks_buffer_length(self):
        store = _make_store()
        store.append_entry(1, _entry(text="a"))
        store.append_entry(1, _entry(text="b"))
        store.mark_observed(1)
        # No new messages → False
        assert store.has_new_since_observe(1) is False
        # One more → True
        store.append_entry(1, _entry(text="c"))
        assert store.has_new_since_observe(1) is True

    def test_initial_has_new_is_true_when_buffer_nonempty(self):
        store = _make_store()
        store.append_entry(1, _entry(text="hello"))
        # Never marked observed → any buffer content counts as new
        assert store.has_new_since_observe(1) is True


# ── nickname cache ──────────────────────────────────────────────────────


class TestNicknameCache:
    def test_remember_and_read(self):
        store = _make_store()
        store.remember_nickname(1, 42, "小明")
        assert store.nickname_for(1, 42) == "小明"

    def test_unknown_user_returns_str_id(self):
        store = _make_store()
        assert store.nickname_for(1, 999) == "999"

    def test_nickname_per_group(self):
        store = _make_store()
        store.remember_nickname(1, 42, "群1小名")
        store.remember_nickname(2, 42, "群2大名")
        assert store.nickname_for(1, 42) == "群1小名"
        assert store.nickname_for(2, 42) == "群2大名"

    def test_append_entry_remembers_non_bot_nickname(self):
        store = _make_store()
        store.append_entry(1, _entry(user_id=10, nickname="小花", text="hi"))
        assert store.nickname_for(1, 10) == "小花"

    def test_bot_entry_does_not_overwrite_nickname(self):
        store = _make_store()
        store.remember_nickname(1, 10, "原昵称")
        store.append_bot_message(1, "bot text")  # bot entry, user_id=0
        # user_id=0 shouldn't overwrite user 10's nickname
        assert store.nickname_for(1, 10) == "原昵称"

    def test_empty_nickname_not_stored(self):
        store = _make_store()
        store.remember_nickname(1, 10, "")
        assert store.nickname_for(1, 10) == "10"

    def test_zero_user_id_not_stored(self):
        store = _make_store()
        store.remember_nickname(1, 0, "bot")
        assert store.nickname_for(1, 0) == "0"


# ── per-group state ─────────────────────────────────────────────────────


class TestGroupState:
    def test_state_returns_default(self):
        store = _make_store()
        s = store.state(1)
        assert isinstance(s, GroupObserveState)
        assert s.observe_in_flight is False
        assert s.cooldown_until == 0.0

    def test_state_is_same_object_on_repeated_access(self):
        store = _make_store()
        s1 = store.state(1)
        s2 = store.state(1)
        assert s1 is s2

    def test_lock_returns_asyncio_lock(self):
        store = _make_store()
        lock = store.lock(1)
        assert isinstance(lock, asyncio.Lock)

    def test_lock_is_same_object_on_repeated_access(self):
        store = _make_store()
        assert store.lock(1) is store.lock(1)


# ── recent / buffer_len ─────────────────────────────────────────────────


class TestRecentAndBufferLen:
    def test_recent_returns_last_n(self):
        store = _make_store()
        for i in range(10):
            store.append_entry(1, _entry(text=f"m{i}"))
        recent = store.recent(1, limit=3)
        assert [e.text for e in recent] == ["m7", "m8", "m9"]

    def test_buffer_len_matches(self):
        store = _make_store()
        store.append_entry(1, _entry(text="a"))
        store.append_entry(1, _entry(text="b"))
        assert store.buffer_len(1) == 2

    def test_empty_group_returns_empty(self):
        store = _make_store()
        assert store.buffer(99) == []
        assert store.buffer_len(99) == 0
        assert store.recent(99) == []


# ── reset ───────────────────────────────────────────────────────────────


class TestReset:
    def test_reset_clears_all(self):
        store = _make_store()
        store.append_entry(1, _entry(text="a"))
        store.append_entry(2, _entry(text="b"))
        store.mark_observed(1)
        store.remember_nickname(1, 10, "昵称")
        store.reset()
        assert store.buffer_len(1) == 0
        assert store.buffer_len(2) == 0
        assert store.has_new_since_observe(1) is False
        assert store.nickname_for(1, 10) == "10"

    def test_reset_group_clears_only_target(self):
        store = _make_store()
        store.append_entry(1, _entry(text="a"))
        store.append_entry(2, _entry(text="b"))
        store.remember_nickname(1, 10, "群1")
        store.remember_nickname(2, 20, "群2")
        store.reset_group(1)
        assert store.buffer_len(1) == 0
        assert store.buffer_len(2) == 1
        assert store.nickname_for(1, 10) == "10"
        assert store.nickname_for(2, 20) == "群2"


# ── debounce task handle ────────────────────────────────────────────────


class TestDebounceTask:
    @pytest.mark.asyncio
    async def test_set_and_get(self):
        store = _make_store()
        assert store.get_debounce_task(1) is None

        async def _noop() -> None:
            pass

        loop = asyncio.get_running_loop()
        task = loop.create_task(_noop())
        store.set_debounce_task(1, task)
        assert store.get_debounce_task(1) is task
        task.cancel()


# ── bot message uses config BOT_NAME ────────────────────────────────────


class TestBotMessageConfig:
    def test_bot_message_uses_bot_name(self):
        store = _make_store(BOT_NAME="测试轩")
        store.append_bot_message(1, "你好呀")
        entry = store.buffer(1)[-1]
        assert entry.nickname == "测试轩"
        assert entry.is_bot is True

    def test_bot_message_uses_clock_monotonic(self):
        clock = FakeClock(monotonic_start=100.0)
        store = ObservationStore(
            config=FakeConfigProvider(),
            clock=clock,
        )
        store.append_bot_message(1, "hello")
        assert store.buffer(1)[-1].ts == 100.0
