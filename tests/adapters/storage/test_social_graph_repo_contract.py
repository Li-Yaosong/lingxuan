"""Contract tests for SocialGraphRepository — parameterized over InMemory and SQLite.

Both ``InMemorySocialGraphRepository`` and ``SqlSocialGraphRepository`` must
satisfy the same behavioural contract defined by the ``SocialGraphRepository``
Protocol.  This module runs an identical suite of assertions against both
implementations.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pytest

from lingxuan.adapters.storage.db import Database
from lingxuan.adapters.storage.repositories import SqlSocialGraphRepository
from lingxuan.protocols.repositories import SocialEdge
from tests.fakes.repositories import InMemorySocialGraphRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _edge(
    from_uid: int = 1,
    to_uid: int = 2,
    relation: str = "friend_of",
    label: str = "小明",
    *,
    evidence: str = "",
    group_id: int | None = None,
) -> SocialEdge:
    return SocialEdge(
        from_user_id=from_uid,
        to_user_id=to_uid,
        relation=relation,
        label=label,
        evidence=evidence,
        group_id=group_id,
    )


# ---------------------------------------------------------------------------
# Protocol for the factory — lets us parameterize over both impls
# ---------------------------------------------------------------------------


@runtime_checkable
class RepoFactory(Protocol):
    def __call__(self) -> InMemorySocialGraphRepository | SqlSocialGraphRepository: ...


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def inmemory_repo() -> InMemorySocialGraphRepository:
    return InMemorySocialGraphRepository()


@pytest.fixture
async def sql_repo() -> SqlSocialGraphRepository:
    """In-memory SQLite avoids Windows path issues in _ensure_db_dir."""
    db = Database("sqlite+aiosqlite://")
    await db.create_all()
    yield SqlSocialGraphRepository(db)
    await db.dispose()


# ===========================================================================
# Contract: add_edge dedup
# ===========================================================================


async def test_add_edge_returns_true_on_new(inmemory_repo: InMemorySocialGraphRepository) -> None:
    assert await inmemory_repo.add_edge(_edge()) is True


async def test_add_edge_returns_false_on_duplicate(inmemory_repo: InMemorySocialGraphRepository) -> None:
    await inmemory_repo.add_edge(_edge())
    assert await inmemory_repo.add_edge(_edge()) is False


async def test_add_edge_different_label_not_deduped(inmemory_repo: InMemorySocialGraphRepository) -> None:
    await inmemory_repo.add_edge(_edge(label="小明"))
    assert await inmemory_repo.add_edge(_edge(label="小红")) is True


async def test_add_edge_different_relation_not_deduped(inmemory_repo: InMemorySocialGraphRepository) -> None:
    await inmemory_repo.add_edge(_edge(relation="friend_of"))
    assert await inmemory_repo.add_edge(_edge(relation="introduced_as")) is True


async def test_add_edge_different_to_user_not_deduped(inmemory_repo: InMemorySocialGraphRepository) -> None:
    await inmemory_repo.add_edge(_edge(to_uid=2))
    assert await inmemory_repo.add_edge(_edge(to_uid=3)) is True


async def test_add_edge_no_extra_rows_on_duplicate(inmemory_repo: InMemorySocialGraphRepository) -> None:
    await inmemory_repo.add_edge(_edge())
    await inmemory_repo.add_edge(_edge())
    edges = await inmemory_repo.edges_from(1)
    assert len(edges) == 1


# --- SQL mirrors ---


async def test_sql_add_edge_returns_true_on_new(sql_repo: SqlSocialGraphRepository) -> None:
    assert await sql_repo.add_edge(_edge()) is True


async def test_sql_add_edge_returns_false_on_duplicate(sql_repo: SqlSocialGraphRepository) -> None:
    await sql_repo.add_edge(_edge())
    assert await sql_repo.add_edge(_edge()) is False


async def test_sql_add_edge_different_label_not_deduped(sql_repo: SqlSocialGraphRepository) -> None:
    await sql_repo.add_edge(_edge(label="小明"))
    assert await sql_repo.add_edge(_edge(label="小红")) is True


async def test_sql_add_edge_different_relation_not_deduped(sql_repo: SqlSocialGraphRepository) -> None:
    await sql_repo.add_edge(_edge(relation="friend_of"))
    assert await sql_repo.add_edge(_edge(relation="introduced_as")) is True


async def test_sql_add_edge_different_to_user_not_deduped(sql_repo: SqlSocialGraphRepository) -> None:
    await sql_repo.add_edge(_edge(to_uid=2))
    assert await sql_repo.add_edge(_edge(to_uid=3)) is True


async def test_sql_add_edge_no_extra_rows_on_duplicate(sql_repo: SqlSocialGraphRepository) -> None:
    await sql_repo.add_edge(_edge())
    await sql_repo.add_edge(_edge())
    edges = await sql_repo.edges_from(1)
    assert len(edges) == 1


# ===========================================================================
# Contract: index_name + resolve_name
# ===========================================================================


async def test_resolve_name_returns_none_for_missing(inmemory_repo: InMemorySocialGraphRepository) -> None:
    assert await inmemory_repo.resolve_name("小明") is None


async def test_index_and_resolve(inmemory_repo: InMemorySocialGraphRepository) -> None:
    await inmemory_repo.index_name("小明", 42)
    assert await inmemory_repo.resolve_name("小明") == 42


async def test_index_name_upsert_overwrites(inmemory_repo: InMemorySocialGraphRepository) -> None:
    await inmemory_repo.index_name("小明", 42)
    await inmemory_repo.index_name("小明", 99)
    assert await inmemory_repo.resolve_name("小明") == 99


async def test_index_multiple_names(inmemory_repo: InMemorySocialGraphRepository) -> None:
    await inmemory_repo.index_name("小明", 1)
    await inmemory_repo.index_name("小红", 2)
    assert await inmemory_repo.resolve_name("小明") == 1
    assert await inmemory_repo.resolve_name("小红") == 2


# --- SQL mirrors ---


async def test_sql_resolve_name_returns_none_for_missing(sql_repo: SqlSocialGraphRepository) -> None:
    assert await sql_repo.resolve_name("小明") is None


async def test_sql_index_and_resolve(sql_repo: SqlSocialGraphRepository) -> None:
    await sql_repo.index_name("小明", 42)
    assert await sql_repo.resolve_name("小明") == 42


async def test_sql_index_name_upsert_overwrites(sql_repo: SqlSocialGraphRepository) -> None:
    await sql_repo.index_name("小明", 42)
    await sql_repo.index_name("小明", 99)
    assert await sql_repo.resolve_name("小明") == 99


async def test_sql_index_multiple_names(sql_repo: SqlSocialGraphRepository) -> None:
    await sql_repo.index_name("小明", 1)
    await sql_repo.index_name("小红", 2)
    assert await sql_repo.resolve_name("小明") == 1
    assert await sql_repo.resolve_name("小红") == 2


# ===========================================================================
# Contract: edges_from
# ===========================================================================


async def test_edges_from_empty(inmemory_repo: InMemorySocialGraphRepository) -> None:
    assert await inmemory_repo.edges_from(1) == []


async def test_edges_from_filters_by_from_user_id(inmemory_repo: InMemorySocialGraphRepository) -> None:
    await inmemory_repo.add_edge(_edge(from_uid=1, to_uid=2, label="A"))
    await inmemory_repo.add_edge(_edge(from_uid=1, to_uid=3, label="B"))
    await inmemory_repo.add_edge(_edge(from_uid=2, to_uid=1, label="C"))

    edges = await inmemory_repo.edges_from(1)
    assert len(edges) == 2
    assert all(e.from_user_id == 1 for e in edges)


async def test_edges_from_preserves_edge_data(inmemory_repo: InMemorySocialGraphRepository) -> None:
    await inmemory_repo.add_edge(
        _edge(from_uid=1, to_uid=2, relation="introduced_as", label="小明",
              evidence="chat", group_id=99)
    )
    edges = await inmemory_repo.edges_from(1)
    assert len(edges) == 1
    e = edges[0]
    assert e.from_user_id == 1
    assert e.to_user_id == 2
    assert e.relation == "introduced_as"
    assert e.label == "小明"
    assert e.evidence == "chat"
    assert e.group_id == 99


# --- SQL mirrors ---


async def test_sql_edges_from_empty(sql_repo: SqlSocialGraphRepository) -> None:
    assert await sql_repo.edges_from(1) == []


async def test_sql_edges_from_filters_by_from_user_id(sql_repo: SqlSocialGraphRepository) -> None:
    await sql_repo.add_edge(_edge(from_uid=1, to_uid=2, label="A"))
    await sql_repo.add_edge(_edge(from_uid=1, to_uid=3, label="B"))
    await sql_repo.add_edge(_edge(from_uid=2, to_uid=1, label="C"))

    edges = await sql_repo.edges_from(1)
    assert len(edges) == 2
    assert all(e.from_user_id == 1 for e in edges)


async def test_sql_edges_from_preserves_edge_data(sql_repo: SqlSocialGraphRepository) -> None:
    await sql_repo.add_edge(
        _edge(from_uid=1, to_uid=2, relation="introduced_as", label="小明",
              evidence="chat", group_id=99)
    )
    edges = await sql_repo.edges_from(1)
    assert len(edges) == 1
    e = edges[0]
    assert e.from_user_id == 1
    assert e.to_user_id == 2
    assert e.relation == "introduced_as"
    assert e.label == "小明"
    assert e.evidence == "chat"
    assert e.group_id == 99


# ===========================================================================
# Contract: all_names
# ===========================================================================


async def test_all_names_empty(inmemory_repo: InMemorySocialGraphRepository) -> None:
    assert await inmemory_repo.all_names() == {}


async def test_all_names_returns_all(inmemory_repo: InMemorySocialGraphRepository) -> None:
    await inmemory_repo.index_name("小明", 1)
    await inmemory_repo.index_name("小红", 2)
    names = await inmemory_repo.all_names()
    assert names == {"小明": 1, "小红": 2}


# --- SQL mirrors ---


async def test_sql_all_names_empty(sql_repo: SqlSocialGraphRepository) -> None:
    assert await sql_repo.all_names() == {}


async def test_sql_all_names_returns_all(sql_repo: SqlSocialGraphRepository) -> None:
    await sql_repo.index_name("小明", 1)
    await sql_repo.index_name("小红", 2)
    names = await sql_repo.all_names()
    assert names == {"小明": 1, "小红": 2}


# ===========================================================================
# Contract: clear
# ===========================================================================


async def test_clear_empties_edges_and_names(inmemory_repo: InMemorySocialGraphRepository) -> None:
    await inmemory_repo.add_edge(_edge())
    await inmemory_repo.index_name("小明", 42)
    await inmemory_repo.clear()
    assert await inmemory_repo.edges_from(1) == []
    assert await inmemory_repo.resolve_name("小明") is None
    assert await inmemory_repo.all_names() == {}


async def test_clear_idempotent(inmemory_repo: InMemorySocialGraphRepository) -> None:
    await inmemory_repo.clear()  # no error on empty


# --- SQL mirrors ---


async def test_sql_clear_empties_edges_and_names(sql_repo: SqlSocialGraphRepository) -> None:
    await sql_repo.add_edge(_edge())
    await sql_repo.index_name("小明", 42)
    await sql_repo.clear()
    assert await sql_repo.edges_from(1) == []
    assert await sql_repo.resolve_name("小明") is None
    assert await sql_repo.all_names() == {}


async def test_sql_clear_idempotent(sql_repo: SqlSocialGraphRepository) -> None:
    await sql_repo.clear()  # no error on empty
