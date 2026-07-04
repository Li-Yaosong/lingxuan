"""Contract tests for SessionRepository — parameterized over InMemory and SQLite.

Both ``InMemorySessionRepository`` and ``SqlSessionRepository`` must satisfy
the same behavioural contract defined by the ``SessionRepository`` Protocol.
This module runs an identical suite of assertions against both implementations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

import pytest

from lingxuan.adapters.storage.db import Database
from lingxuan.adapters.storage.repositories import SqlSessionRepository
from lingxuan.protocols.messaging import SessionId
from lingxuan.protocols.repositories import Session, StoredMessage
from tests.fakes.repositories import InMemorySessionRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PRIVATE_SID = SessionId(kind="private", peer_id=42)
GROUP_SID = SessionId(kind="group", peer_id=99)


def _msg(role: str, content: str, user_id: int | None = None) -> StoredMessage:
    return StoredMessage(role=role, content=content, user_id=user_id)


# ---------------------------------------------------------------------------
# Protocol for the factory — lets us parameterize over both impls
# ---------------------------------------------------------------------------


@runtime_checkable
class RepoFactory(Protocol):
    def __call__(self) -> InMemorySessionRepository | SqlSessionRepository: ...


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def inmemory_repo() -> InMemorySessionRepository:
    return InMemorySessionRepository()


@pytest.fixture
async def sql_repo() -> SqlSessionRepository:
    """In-memory SQLite avoids Windows path issues in _ensure_db_dir."""
    db = Database("sqlite+aiosqlite://")
    await db.create_all()
    yield SqlSessionRepository(db)
    await db.dispose()


# ---------------------------------------------------------------------------
# Contract: ensure + get
# ---------------------------------------------------------------------------


async def test_ensure_creates_new_session(inmemory_repo: InMemorySessionRepository) -> None:
    s = await inmemory_repo.ensure(PRIVATE_SID, group_id=7, nickname="alice")
    assert s.session_id == PRIVATE_SID
    assert s.kind == "private"
    assert s.group_id == 7
    assert s.nickname == "alice"


async def test_ensure_idempotent(inmemory_repo: InMemorySessionRepository) -> None:
    s1 = await inmemory_repo.ensure(PRIVATE_SID, nickname="alice")
    s2 = await inmemory_repo.ensure(PRIVATE_SID, nickname="bob")
    # second call returns existing, nickname unchanged
    assert s1.session_id == s2.session_id
    assert s2.nickname == "alice"


async def test_get_returns_none_for_missing(inmemory_repo: InMemorySessionRepository) -> None:
    assert await inmemory_repo.get(PRIVATE_SID) is None


async def test_get_returns_ensured(inmemory_repo: InMemorySessionRepository) -> None:
    await inmemory_repo.ensure(PRIVATE_SID)
    s = await inmemory_repo.get(PRIVATE_SID)
    assert s is not None
    assert s.session_id == PRIVATE_SID


# --- SQL mirrors ---


async def test_sql_ensure_creates_new_session(sql_repo: SqlSessionRepository) -> None:
    s = await sql_repo.ensure(PRIVATE_SID, group_id=7, nickname="alice")
    assert s.session_id == PRIVATE_SID
    assert s.kind == "private"
    assert s.group_id == 7
    assert s.nickname == "alice"


async def test_sql_ensure_idempotent(sql_repo: SqlSessionRepository) -> None:
    s1 = await sql_repo.ensure(PRIVATE_SID, nickname="alice")
    s2 = await sql_repo.ensure(PRIVATE_SID, nickname="bob")
    assert s1.session_id == s2.session_id
    assert s2.nickname == "alice"


async def test_sql_get_returns_none_for_missing(sql_repo: SqlSessionRepository) -> None:
    assert await sql_repo.get(PRIVATE_SID) is None


async def test_sql_get_returns_ensured(sql_repo: SqlSessionRepository) -> None:
    await sql_repo.ensure(PRIVATE_SID)
    s = await sql_repo.get(PRIVATE_SID)
    assert s is not None
    assert s.session_id == PRIVATE_SID


# ---------------------------------------------------------------------------
# Contract: append_message + load_history + count
# ---------------------------------------------------------------------------


async def test_append_and_load_order(inmemory_repo: InMemorySessionRepository) -> None:
    await inmemory_repo.append_message(PRIVATE_SID, _msg("user", "hello", 1))
    await inmemory_repo.append_message(PRIVATE_SID, _msg("assistant", "hi"))
    history = await inmemory_repo.load_history(PRIVATE_SID)
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[0].content == "hello"
    assert history[1].role == "assistant"
    assert history[1].content == "hi"


async def test_load_history_with_limit(inmemory_repo: InMemorySessionRepository) -> None:
    for i in range(5):
        await inmemory_repo.append_message(PRIVATE_SID, _msg("user", f"m{i}"))
    history = await inmemory_repo.load_history(PRIVATE_SID, limit=2)
    assert len(history) == 2
    assert history[0].content == "m3"
    assert history[1].content == "m4"


async def test_load_history_no_limit(inmemory_repo: InMemorySessionRepository) -> None:
    for i in range(5):
        await inmemory_repo.append_message(PRIVATE_SID, _msg("user", f"m{i}"))
    history = await inmemory_repo.load_history(PRIVATE_SID)
    assert len(history) == 5


async def test_count_messages(inmemory_repo: InMemorySessionRepository) -> None:
    assert await inmemory_repo.count_messages(PRIVATE_SID) == 0
    await inmemory_repo.append_message(PRIVATE_SID, _msg("user", "a"))
    await inmemory_repo.append_message(PRIVATE_SID, _msg("assistant", "b"))
    assert await inmemory_repo.count_messages(PRIVATE_SID) == 2


async def test_count_messages_empty_session(inmemory_repo: InMemorySessionRepository) -> None:
    await inmemory_repo.ensure(PRIVATE_SID)
    assert await inmemory_repo.count_messages(PRIVATE_SID) == 0


# --- SQL mirrors ---


async def test_sql_append_and_load_order(sql_repo: SqlSessionRepository) -> None:
    await sql_repo.append_message(PRIVATE_SID, _msg("user", "hello", 1))
    await sql_repo.append_message(PRIVATE_SID, _msg("assistant", "hi"))
    history = await sql_repo.load_history(PRIVATE_SID)
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[0].content == "hello"
    assert history[1].role == "assistant"
    assert history[1].content == "hi"


async def test_sql_load_history_with_limit(sql_repo: SqlSessionRepository) -> None:
    for i in range(5):
        await sql_repo.append_message(PRIVATE_SID, _msg("user", f"m{i}"))
    history = await sql_repo.load_history(PRIVATE_SID, limit=2)
    assert len(history) == 2
    assert history[0].content == "m3"
    assert history[1].content == "m4"


async def test_sql_load_history_no_limit(sql_repo: SqlSessionRepository) -> None:
    for i in range(5):
        await sql_repo.append_message(PRIVATE_SID, _msg("user", f"m{i}"))
    history = await sql_repo.load_history(PRIVATE_SID)
    assert len(history) == 5


async def test_sql_count_messages(sql_repo: SqlSessionRepository) -> None:
    assert await sql_repo.count_messages(PRIVATE_SID) == 0
    await sql_repo.append_message(PRIVATE_SID, _msg("user", "a"))
    await sql_repo.append_message(PRIVATE_SID, _msg("assistant", "b"))
    assert await sql_repo.count_messages(PRIVATE_SID) == 2


async def test_sql_count_messages_empty_session(sql_repo: SqlSessionRepository) -> None:
    await sql_repo.ensure(PRIVATE_SID)
    assert await sql_repo.count_messages(PRIVATE_SID) == 0


# ---------------------------------------------------------------------------
# Contract: trim_to_last
# ---------------------------------------------------------------------------


async def test_trim_removes_oldest(inmemory_repo: InMemorySessionRepository) -> None:
    for i in range(10):
        await inmemory_repo.append_message(PRIVATE_SID, _msg("user", f"m{i}"))
    removed = await inmemory_repo.trim_to_last(PRIVATE_SID, keep_last=3)
    assert removed == 7
    history = await inmemory_repo.load_history(PRIVATE_SID)
    assert len(history) == 3
    assert history[0].content == "m7"
    assert history[1].content == "m8"
    assert history[2].content == "m9"


async def test_trim_noop_when_under_limit(inmemory_repo: InMemorySessionRepository) -> None:
    for i in range(3):
        await inmemory_repo.append_message(PRIVATE_SID, _msg("user", f"m{i}"))
    removed = await inmemory_repo.trim_to_last(PRIVATE_SID, keep_last=5)
    assert removed == 0
    assert await inmemory_repo.count_messages(PRIVATE_SID) == 3


async def test_trim_exact_keep(inmemory_repo: InMemorySessionRepository) -> None:
    for i in range(5):
        await inmemory_repo.append_message(PRIVATE_SID, _msg("user", f"m{i}"))
    removed = await inmemory_repo.trim_to_last(PRIVATE_SID, keep_last=5)
    assert removed == 0


# --- SQL mirrors ---


async def test_sql_trim_removes_oldest(sql_repo: SqlSessionRepository) -> None:
    for i in range(10):
        await sql_repo.append_message(PRIVATE_SID, _msg("user", f"m{i}"))
    removed = await sql_repo.trim_to_last(PRIVATE_SID, keep_last=3)
    assert removed == 7
    history = await sql_repo.load_history(PRIVATE_SID)
    assert len(history) == 3
    assert history[0].content == "m7"
    assert history[1].content == "m8"
    assert history[2].content == "m9"


async def test_sql_trim_noop_when_under_limit(sql_repo: SqlSessionRepository) -> None:
    for i in range(3):
        await sql_repo.append_message(PRIVATE_SID, _msg("user", f"m{i}"))
    removed = await sql_repo.trim_to_last(PRIVATE_SID, keep_last=5)
    assert removed == 0
    assert await sql_repo.count_messages(PRIVATE_SID) == 3


async def test_sql_trim_exact_keep(sql_repo: SqlSessionRepository) -> None:
    for i in range(5):
        await sql_repo.append_message(PRIVATE_SID, _msg("user", f"m{i}"))
    removed = await sql_repo.trim_to_last(PRIVATE_SID, keep_last=5)
    assert removed == 0


# ---------------------------------------------------------------------------
# Contract: summary
# ---------------------------------------------------------------------------


async def test_summary_default_empty(inmemory_repo: InMemorySessionRepository) -> None:
    await inmemory_repo.ensure(PRIVATE_SID)
    assert await inmemory_repo.get_summary(PRIVATE_SID) == ""


async def test_set_and_get_summary(inmemory_repo: InMemorySessionRepository) -> None:
    await inmemory_repo.ensure(PRIVATE_SID)
    await inmemory_repo.set_summary(PRIVATE_SID, "A summary")
    assert await inmemory_repo.get_summary(PRIVATE_SID) == "A summary"


# --- SQL mirrors ---


async def test_sql_summary_default_empty(sql_repo: SqlSessionRepository) -> None:
    await sql_repo.ensure(PRIVATE_SID)
    assert await sql_repo.get_summary(PRIVATE_SID) == ""


async def test_sql_set_and_get_summary(sql_repo: SqlSessionRepository) -> None:
    await sql_repo.ensure(PRIVATE_SID)
    await sql_repo.set_summary(PRIVATE_SID, "A summary")
    assert await sql_repo.get_summary(PRIVATE_SID) == "A summary"


# ---------------------------------------------------------------------------
# Contract: update_meta
# ---------------------------------------------------------------------------


async def test_update_meta_nickname(inmemory_repo: InMemorySessionRepository) -> None:
    await inmemory_repo.ensure(PRIVATE_SID, nickname="old")
    await inmemory_repo.update_meta(PRIVATE_SID, nickname="new")
    s = await inmemory_repo.get(PRIVATE_SID)
    assert s is not None
    assert s.nickname == "new"


async def test_update_meta_group_id(inmemory_repo: InMemorySessionRepository) -> None:
    await inmemory_repo.ensure(PRIVATE_SID)
    await inmemory_repo.update_meta(PRIVATE_SID, group_id=55)
    s = await inmemory_repo.get(PRIVATE_SID)
    assert s is not None
    assert s.group_id == 55


async def test_update_meta_last_active_at(inmemory_repo: InMemorySessionRepository) -> None:
    await inmemory_repo.ensure(PRIVATE_SID)
    now = datetime.now(timezone.utc)
    await inmemory_repo.update_meta(PRIVATE_SID, last_active_at=now)
    s = await inmemory_repo.get(PRIVATE_SID)
    assert s is not None
    assert s.last_active_at is not None


# --- SQL mirrors ---


async def test_sql_update_meta_nickname(sql_repo: SqlSessionRepository) -> None:
    await sql_repo.ensure(PRIVATE_SID, nickname="old")
    await sql_repo.update_meta(PRIVATE_SID, nickname="new")
    s = await sql_repo.get(PRIVATE_SID)
    assert s is not None
    assert s.nickname == "new"


async def test_sql_update_meta_group_id(sql_repo: SqlSessionRepository) -> None:
    await sql_repo.ensure(PRIVATE_SID)
    await sql_repo.update_meta(PRIVATE_SID, group_id=55)
    s = await sql_repo.get(PRIVATE_SID)
    assert s is not None
    assert s.group_id == 55


async def test_sql_update_meta_last_active_at(sql_repo: SqlSessionRepository) -> None:
    await sql_repo.ensure(PRIVATE_SID)
    now = datetime.now(timezone.utc)
    await sql_repo.update_meta(PRIVATE_SID, last_active_at=now)
    s = await sql_repo.get(PRIVATE_SID)
    assert s is not None
    assert s.last_active_at is not None


# ---------------------------------------------------------------------------
# Contract: entities
# ---------------------------------------------------------------------------


async def test_merge_and_get_entities(inmemory_repo: InMemorySessionRepository) -> None:
    await inmemory_repo.ensure(GROUP_SID)
    await inmemory_repo.merge_entity(GROUP_SID, "alice", 111)
    await inmemory_repo.merge_entity(GROUP_SID, "bob", 222)
    entities = await inmemory_repo.get_entities(GROUP_SID)
    assert entities == {"alice": 111, "bob": 222}


async def test_merge_entity_upsert(inmemory_repo: InMemorySessionRepository) -> None:
    await inmemory_repo.ensure(GROUP_SID)
    await inmemory_repo.merge_entity(GROUP_SID, "alice", 111)
    await inmemory_repo.merge_entity(GROUP_SID, "alice", 999)
    entities = await inmemory_repo.get_entities(GROUP_SID)
    assert entities == {"alice": 999}


async def test_get_entities_empty(inmemory_repo: InMemorySessionRepository) -> None:
    await inmemory_repo.ensure(GROUP_SID)
    assert await inmemory_repo.get_entities(GROUP_SID) == {}


# --- SQL mirrors ---


async def test_sql_merge_and_get_entities(sql_repo: SqlSessionRepository) -> None:
    await sql_repo.ensure(GROUP_SID)
    await sql_repo.merge_entity(GROUP_SID, "alice", 111)
    await sql_repo.merge_entity(GROUP_SID, "bob", 222)
    entities = await sql_repo.get_entities(GROUP_SID)
    assert entities == {"alice": 111, "bob": 222}


async def test_sql_merge_entity_upsert(sql_repo: SqlSessionRepository) -> None:
    await sql_repo.ensure(GROUP_SID)
    await sql_repo.merge_entity(GROUP_SID, "alice", 111)
    await sql_repo.merge_entity(GROUP_SID, "alice", 999)
    entities = await sql_repo.get_entities(GROUP_SID)
    assert entities == {"alice": 999}


async def test_sql_get_entities_empty(sql_repo: SqlSessionRepository) -> None:
    await sql_repo.ensure(GROUP_SID)
    assert await sql_repo.get_entities(GROUP_SID) == {}


# ---------------------------------------------------------------------------
# Contract: clear (cascade)
# ---------------------------------------------------------------------------


async def test_clear_removes_session_and_messages(inmemory_repo: InMemorySessionRepository) -> None:
    await inmemory_repo.append_message(PRIVATE_SID, _msg("user", "hi"))
    await inmemory_repo.clear(PRIVATE_SID)
    assert await inmemory_repo.get(PRIVATE_SID) is None
    assert await inmemory_repo.count_messages(PRIVATE_SID) == 0


async def test_clear_removes_entities(inmemory_repo: InMemorySessionRepository) -> None:
    await inmemory_repo.merge_entity(GROUP_SID, "alice", 111)
    await inmemory_repo.clear(GROUP_SID)
    assert await inmemory_repo.get(GROUP_SID) is None
    assert await inmemory_repo.get_entities(GROUP_SID) == {}


async def test_clear_idempotent(inmemory_repo: InMemorySessionRepository) -> None:
    await inmemory_repo.clear(PRIVATE_SID)  # no error on missing


# --- SQL mirrors ---


async def test_sql_clear_removes_session_and_messages(sql_repo: SqlSessionRepository) -> None:
    await sql_repo.append_message(PRIVATE_SID, _msg("user", "hi"))
    await sql_repo.clear(PRIVATE_SID)
    assert await sql_repo.get(PRIVATE_SID) is None
    assert await sql_repo.count_messages(PRIVATE_SID) == 0


async def test_sql_clear_removes_entities(sql_repo: SqlSessionRepository) -> None:
    await sql_repo.merge_entity(GROUP_SID, "alice", 111)
    await sql_repo.clear(GROUP_SID)
    assert await sql_repo.get(GROUP_SID) is None
    assert await sql_repo.get_entities(GROUP_SID) == {}


async def test_sql_clear_idempotent(sql_repo: SqlSessionRepository) -> None:
    await sql_repo.clear(PRIVATE_SID)  # no error on missing


# ---------------------------------------------------------------------------
# Contract: list_sessions
# ---------------------------------------------------------------------------


async def test_list_sessions(inmemory_repo: InMemorySessionRepository) -> None:
    await inmemory_repo.ensure(PRIVATE_SID)
    await inmemory_repo.ensure(GROUP_SID)
    sessions = await inmemory_repo.list_sessions()
    assert len(sessions) == 2


async def test_list_sessions_limit(inmemory_repo: InMemorySessionRepository) -> None:
    await inmemory_repo.ensure(PRIVATE_SID)
    await inmemory_repo.ensure(GROUP_SID)
    sessions = await inmemory_repo.list_sessions(limit=1)
    assert len(sessions) == 1


# --- SQL mirrors ---


async def test_sql_list_sessions(sql_repo: SqlSessionRepository) -> None:
    await sql_repo.ensure(PRIVATE_SID)
    await sql_repo.ensure(GROUP_SID)
    sessions = await sql_repo.list_sessions()
    assert len(sessions) == 2


async def test_sql_list_sessions_limit(sql_repo: SqlSessionRepository) -> None:
    await sql_repo.ensure(PRIVATE_SID)
    await sql_repo.ensure(GROUP_SID)
    sessions = await sql_repo.list_sessions(limit=1)
    assert len(sessions) == 1


# ---------------------------------------------------------------------------
# Contract: seq auto-increment
# ---------------------------------------------------------------------------


async def test_seq_auto_increment(inmemory_repo: InMemorySessionRepository) -> None:
    await inmemory_repo.append_message(PRIVATE_SID, _msg("user", "a"))
    await inmemory_repo.append_message(PRIVATE_SID, _msg("assistant", "b"))
    history = await inmemory_repo.load_history(PRIVATE_SID)
    assert history[0].seq == 0
    assert history[1].seq == 1


async def test_sql_seq_auto_increment(sql_repo: SqlSessionRepository) -> None:
    await sql_repo.append_message(PRIVATE_SID, _msg("user", "a"))
    await sql_repo.append_message(PRIVATE_SID, _msg("assistant", "b"))
    history = await sql_repo.load_history(PRIVATE_SID)
    assert history[0].seq == 0
    assert history[1].seq == 1


# ---------------------------------------------------------------------------
# Contract: append auto-ensures session
# ---------------------------------------------------------------------------


async def test_append_auto_ensures(inmemory_repo: InMemorySessionRepository) -> None:
    await inmemory_repo.append_message(PRIVATE_SID, _msg("user", "hi"))
    s = await inmemory_repo.get(PRIVATE_SID)
    assert s is not None


async def test_sql_append_auto_ensures(sql_repo: SqlSessionRepository) -> None:
    await sql_repo.append_message(PRIVATE_SID, _msg("user", "hi"))
    s = await sql_repo.get(PRIVATE_SID)
    assert s is not None


# ---------------------------------------------------------------------------
# Contract: append updates last_active_at
# ---------------------------------------------------------------------------


async def test_sql_append_updates_last_active(sql_repo: SqlSessionRepository) -> None:
    await sql_repo.ensure(PRIVATE_SID)
    s1 = await sql_repo.get(PRIVATE_SID)
    assert s1 is not None
    assert s1.last_active_at is None

    await sql_repo.append_message(PRIVATE_SID, _msg("user", "hi"))
    s2 = await sql_repo.get(PRIVATE_SID)
    assert s2 is not None
    assert s2.last_active_at is not None
