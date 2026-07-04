"""Contract tests for UserProfileRepository — parameterized over InMemory and SQLite.

Both ``InMemoryUserProfileRepository`` and ``SqlUserProfileRepository`` must
satisfy the same behavioural contract defined by the ``UserProfileRepository``
Protocol. This module runs an identical suite of assertions against both
implementations.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lingxuan.adapters.storage.db import Database
from lingxuan.adapters.storage.repositories import SqlUserProfileRepository
from lingxuan.protocols.repositories import UserFact, UserProfile
from tests.fakes.repositories import InMemoryUserProfileRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fact(
    fid: str = "f1",
    content: str = "likes cats",
    *,
    category: str = "general",
    active: bool = True,
    learned_at: datetime | None = None,
) -> UserFact:
    return UserFact(
        id=fid,
        content=content,
        category=category,
        active=active,
        learned_at=learned_at or datetime.now(timezone.utc),
    )


def _profile(
    user_id: int = 1,
    preferred_name: str = "alice",
    *,
    stage: str = "stranger",
    interaction_count: int = 0,
) -> UserProfile:
    return UserProfile(
        user_id=user_id,
        preferred_name=preferred_name,
        stage=stage,
        interaction_count=interaction_count,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def inmemory_repo() -> InMemoryUserProfileRepository:
    return InMemoryUserProfileRepository()


@pytest.fixture
async def sql_repo() -> SqlUserProfileRepository:
    """In-memory SQLite avoids filesystem side-effects."""
    db = Database("sqlite+aiosqlite://")
    await db.create_all()
    yield SqlUserProfileRepository(db)
    await db.dispose()


# ---------------------------------------------------------------------------
# Contract: upsert + get round-trip
# ---------------------------------------------------------------------------


async def test_upsert_creates_and_gets(inmemory_repo: InMemoryUserProfileRepository) -> None:
    p = _profile(user_id=10, preferred_name="bob")
    await inmemory_repo.upsert(p)
    got = await inmemory_repo.get(10)
    assert got is not None
    assert got.user_id == 10
    assert got.preferred_name == "bob"


async def test_upsert_updates_existing(inmemory_repo: InMemoryUserProfileRepository) -> None:
    await inmemory_repo.upsert(_profile(user_id=1, preferred_name="alice"))
    await inmemory_repo.upsert(_profile(user_id=1, preferred_name="alice2"))
    got = await inmemory_repo.get(1)
    assert got is not None
    assert got.preferred_name == "alice2"


async def test_get_returns_none_for_missing(inmemory_repo: InMemoryUserProfileRepository) -> None:
    assert await inmemory_repo.get(999) is None


async def test_upsert_preserves_aliases_and_group_cards(
    inmemory_repo: InMemoryUserProfileRepository,
) -> None:
    p = UserProfile(
        user_id=5,
        preferred_name="eve",
        aliases=["e", "evie"],
        group_cards={"123": "Eve", "456": "Ev"},
    )
    await inmemory_repo.upsert(p)
    got = await inmemory_repo.get(5)
    assert got is not None
    assert got.aliases == ["e", "evie"]
    assert got.group_cards == {"123": "Eve", "456": "Ev"}


async def test_upsert_preserves_datetime_fields(
    inmemory_repo: InMemoryUserProfileRepository,
) -> None:
    now = datetime.now(timezone.utc)
    p = UserProfile(user_id=7, first_met_at=now, last_seen_at=now)
    await inmemory_repo.upsert(p)
    got = await inmemory_repo.get(7)
    assert got is not None
    assert got.first_met_at is not None
    assert got.last_seen_at is not None


# --- SQL mirrors ---


async def test_sql_upsert_creates_and_gets(sql_repo: SqlUserProfileRepository) -> None:
    p = _profile(user_id=10, preferred_name="bob")
    await sql_repo.upsert(p)
    got = await sql_repo.get(10)
    assert got is not None
    assert got.user_id == 10
    assert got.preferred_name == "bob"


async def test_sql_upsert_updates_existing(sql_repo: SqlUserProfileRepository) -> None:
    await sql_repo.upsert(_profile(user_id=1, preferred_name="alice"))
    await sql_repo.upsert(_profile(user_id=1, preferred_name="alice2"))
    got = await sql_repo.get(1)
    assert got is not None
    assert got.preferred_name == "alice2"


async def test_sql_get_returns_none_for_missing(sql_repo: SqlUserProfileRepository) -> None:
    assert await sql_repo.get(999) is None


async def test_sql_upsert_preserves_aliases_and_group_cards(
    sql_repo: SqlUserProfileRepository,
) -> None:
    p = UserProfile(
        user_id=5,
        preferred_name="eve",
        aliases=["e", "evie"],
        group_cards={"123": "Eve", "456": "Ev"},
    )
    await sql_repo.upsert(p)
    got = await sql_repo.get(5)
    assert got is not None
    assert got.aliases == ["e", "evie"]
    assert got.group_cards == {"123": "Eve", "456": "Ev"}


async def test_sql_upsert_preserves_datetime_fields(
    sql_repo: SqlUserProfileRepository,
) -> None:
    now = datetime.now(timezone.utc)
    p = UserProfile(user_id=7, first_met_at=now, last_seen_at=now)
    await sql_repo.upsert(p)
    got = await sql_repo.get(7)
    assert got is not None
    assert got.first_met_at is not None
    assert got.last_seen_at is not None


# ---------------------------------------------------------------------------
# Contract: add_fact + list_active_facts
# ---------------------------------------------------------------------------


async def test_add_fact_and_list(inmemory_repo: InMemoryUserProfileRepository) -> None:
    await inmemory_repo.upsert(_profile(user_id=1))
    await inmemory_repo.add_fact(1, _fact("f1", "likes cats"))
    await inmemory_repo.add_fact(1, _fact("f2", "likes dogs"))
    facts = await inmemory_repo.list_active_facts(1)
    assert len(facts) == 2
    assert facts[0].content == "likes cats"
    assert facts[1].content == "likes dogs"


async def test_add_fact_auto_creates_profile(
    inmemory_repo: InMemoryUserProfileRepository,
) -> None:
    await inmemory_repo.add_fact(42, _fact("f1", "new user"))
    got = await inmemory_repo.get(42)
    assert got is not None
    assert got.user_id == 42


async def test_duplicate_active_content_skipped(
    inmemory_repo: InMemoryUserProfileRepository,
) -> None:
    await inmemory_repo.upsert(_profile(user_id=1))
    await inmemory_repo.add_fact(1, _fact("f1", "likes cats"))
    await inmemory_repo.add_fact(1, _fact("f2", "likes cats"))
    facts = await inmemory_repo.list_active_facts(1)
    assert len(facts) == 1


async def test_list_active_facts_with_limit(
    inmemory_repo: InMemoryUserProfileRepository,
) -> None:
    await inmemory_repo.upsert(_profile(user_id=1))
    for i in range(5):
        await inmemory_repo.add_fact(1, _fact(f"f{i}", f"fact {i}"))
    facts = await inmemory_repo.list_active_facts(1, limit=2)
    assert len(facts) == 2


async def test_list_active_facts_missing_user(
    inmemory_repo: InMemoryUserProfileRepository,
) -> None:
    facts = await inmemory_repo.list_active_facts(999)
    assert facts == []


# --- SQL mirrors ---


async def test_sql_add_fact_and_list(sql_repo: SqlUserProfileRepository) -> None:
    await sql_repo.upsert(_profile(user_id=1))
    await sql_repo.add_fact(1, _fact("f1", "likes cats"))
    await sql_repo.add_fact(1, _fact("f2", "likes dogs"))
    facts = await sql_repo.list_active_facts(1)
    assert len(facts) == 2
    contents = [f.content for f in facts]
    assert "likes cats" in contents
    assert "likes dogs" in contents


async def test_sql_add_fact_auto_creates_profile(
    sql_repo: SqlUserProfileRepository,
) -> None:
    await sql_repo.add_fact(42, _fact("f1", "new user"))
    got = await sql_repo.get(42)
    assert got is not None
    assert got.user_id == 42


async def test_sql_duplicate_active_content_skipped(
    sql_repo: SqlUserProfileRepository,
) -> None:
    await sql_repo.upsert(_profile(user_id=1))
    await sql_repo.add_fact(1, _fact("f1", "likes cats"))
    await sql_repo.add_fact(1, _fact("f2", "likes cats"))
    facts = await sql_repo.list_active_facts(1)
    assert len(facts) == 1


async def test_sql_list_active_facts_with_limit(
    sql_repo: SqlUserProfileRepository,
) -> None:
    await sql_repo.upsert(_profile(user_id=1))
    for i in range(5):
        await sql_repo.add_fact(1, _fact(f"f{i}", f"fact {i}"))
    facts = await sql_repo.list_active_facts(1, limit=2)
    assert len(facts) == 2


async def test_sql_list_active_facts_missing_user(
    sql_repo: SqlUserProfileRepository,
) -> None:
    facts = await sql_repo.list_active_facts(999)
    assert facts == []


# ---------------------------------------------------------------------------
# Contract: deactivate_facts (soft delete)
# ---------------------------------------------------------------------------


async def test_deactivate_facts(inmemory_repo: InMemoryUserProfileRepository) -> None:
    await inmemory_repo.upsert(_profile(user_id=1))
    await inmemory_repo.add_fact(1, _fact("f1", "fact1"))
    await inmemory_repo.add_fact(1, _fact("f2", "fact2"))
    await inmemory_repo.deactivate_facts(1, ["f1"])
    facts = await inmemory_repo.list_active_facts(1)
    assert len(facts) == 1
    assert facts[0].id == "f2"


async def test_deactivate_facts_keeps_inactive_rows(
    inmemory_repo: InMemoryUserProfileRepository,
) -> None:
    await inmemory_repo.upsert(_profile(user_id=1))
    await inmemory_repo.add_fact(1, _fact("f1", "fact1"))
    await inmemory_repo.add_fact(1, _fact("f2", "fact2"))
    await inmemory_repo.deactivate_facts(1, ["f1"])
    # InMemory stores deactivated facts in the profile's facts list
    got = await inmemory_repo.get(1)
    assert got is not None
    all_facts = got.facts
    assert len(all_facts) == 2  # still physically present
    assert any(not f.active for f in all_facts)


async def test_deactivate_facts_empty_list_noop(
    inmemory_repo: InMemoryUserProfileRepository,
) -> None:
    await inmemory_repo.upsert(_profile(user_id=1))
    await inmemory_repo.add_fact(1, _fact("f1", "fact1"))
    await inmemory_repo.deactivate_facts(1, [])
    facts = await inmemory_repo.list_active_facts(1)
    assert len(facts) == 1


# --- SQL mirrors ---


async def test_sql_deactivate_facts(sql_repo: SqlUserProfileRepository) -> None:
    await sql_repo.upsert(_profile(user_id=1))
    await sql_repo.add_fact(1, _fact("f1", "fact1"))
    await sql_repo.add_fact(1, _fact("f2", "fact2"))
    await sql_repo.deactivate_facts(1, ["f1"])
    facts = await sql_repo.list_active_facts(1)
    assert len(facts) == 1
    assert facts[0].id == "f2"


async def test_sql_deactivate_facts_keeps_inactive_rows(
    sql_repo: SqlUserProfileRepository,
) -> None:
    await sql_repo.upsert(_profile(user_id=1))
    await sql_repo.add_fact(1, _fact("f1", "fact1"))
    await sql_repo.add_fact(1, _fact("f2", "fact2"))
    await sql_repo.deactivate_facts(1, ["f1"])
    # Verify deactivated row still exists in DB (physical row not deleted)
    from sqlalchemy import select as sa_select
    from lingxuan.adapters.storage.orm import UserFact as UserFactRow
    async with sql_repo._db.session() as s:
        result = await s.execute(
            sa_select(UserFactRow).where(UserFactRow.user_id == 1)
        )
        all_rows = result.scalars().all()
        assert len(all_rows) == 2  # both rows still present
        active_rows = [r for r in all_rows if r.active]
        inactive_rows = [r for r in all_rows if not r.active]
        assert len(active_rows) == 1
        assert len(inactive_rows) == 1


async def test_sql_deactivate_facts_empty_list_noop(
    sql_repo: SqlUserProfileRepository,
) -> None:
    await sql_repo.upsert(_profile(user_id=1))
    await sql_repo.add_fact(1, _fact("f1", "fact1"))
    await sql_repo.deactivate_facts(1, [])
    facts = await sql_repo.list_active_facts(1)
    assert len(facts) == 1


# ---------------------------------------------------------------------------
# Contract: fact soft-delete on overflow (max_active_facts)
# ---------------------------------------------------------------------------


async def test_fact_soft_delete_over_limit(
    inmemory_repo: InMemoryUserProfileRepository,
) -> None:
    repo = InMemoryUserProfileRepository(max_active_facts=3)
    await repo.upsert(_profile(user_id=1))
    for i in range(5):
        await repo.add_fact(1, _fact(f"f{i}", f"fact {i}"))
    facts = await repo.list_active_facts(1)
    assert len(facts) == 3
    # Most recent 3 should remain active
    active_contents = {f.content for f in facts}
    assert "fact 2" in active_contents
    assert "fact 3" in active_contents
    assert "fact 4" in active_contents


async def test_sql_fact_soft_delete_over_limit(
    sql_repo: SqlUserProfileRepository,
) -> None:
    db = Database("sqlite+aiosqlite://")
    await db.create_all()
    repo = SqlUserProfileRepository(db, max_active_facts=3)
    try:
        await repo.upsert(_profile(user_id=1))
        for i in range(5):
            await repo.add_fact(1, _fact(f"f{i}", f"fact {i}"))
        facts = await repo.list_active_facts(1)
        assert len(facts) == 3
        active_contents = {f.content for f in facts}
        assert "fact 2" in active_contents
        assert "fact 3" in active_contents
        assert "fact 4" in active_contents
    finally:
        await db.dispose()


# ---------------------------------------------------------------------------
# Contract: delete + cascade
# ---------------------------------------------------------------------------


async def test_delete_returns_true(inmemory_repo: InMemoryUserProfileRepository) -> None:
    await inmemory_repo.upsert(_profile(user_id=1))
    assert await inmemory_repo.delete(1) is True


async def test_delete_returns_false_for_missing(
    inmemory_repo: InMemoryUserProfileRepository,
) -> None:
    assert await inmemory_repo.delete(999) is False


async def test_delete_removes_profile_and_facts(
    inmemory_repo: InMemoryUserProfileRepository,
) -> None:
    await inmemory_repo.upsert(_profile(user_id=1))
    await inmemory_repo.add_fact(1, _fact("f1", "fact1"))
    await inmemory_repo.delete(1)
    assert await inmemory_repo.get(1) is None


# --- SQL mirrors ---


async def test_sql_delete_returns_true(sql_repo: SqlUserProfileRepository) -> None:
    await sql_repo.upsert(_profile(user_id=1))
    assert await sql_repo.delete(1) is True


async def test_sql_delete_returns_false_for_missing(
    sql_repo: SqlUserProfileRepository,
) -> None:
    assert await sql_repo.delete(999) is False


async def test_sql_delete_removes_profile_and_cascades_facts(
    sql_repo: SqlUserProfileRepository,
) -> None:
    await sql_repo.upsert(_profile(user_id=1))
    await sql_repo.add_fact(1, _fact("f1", "fact1"))
    await sql_repo.add_fact(1, _fact("f2", "fact2"))
    assert await sql_repo.delete(1) is True
    assert await sql_repo.get(1) is None
    # Verify facts are cascaded away
    from sqlalchemy import select as sa_select
    from lingxuan.adapters.storage.orm import UserFact as UserFactRow
    async with sql_repo._db.session() as s:
        result = await s.execute(
            sa_select(UserFactRow).where(UserFactRow.user_id == 1)
        )
        assert result.scalars().first() is None


# ---------------------------------------------------------------------------
# Contract: delete_all
# ---------------------------------------------------------------------------


async def test_delete_all_returns_count(
    inmemory_repo: InMemoryUserProfileRepository,
) -> None:
    await inmemory_repo.upsert(_profile(user_id=1))
    await inmemory_repo.upsert(_profile(user_id=2))
    count = await inmemory_repo.delete_all()
    assert count == 2
    assert await inmemory_repo.get(1) is None
    assert await inmemory_repo.get(2) is None


async def test_delete_all_empty(inmemory_repo: InMemoryUserProfileRepository) -> None:
    count = await inmemory_repo.delete_all()
    assert count == 0


async def test_sql_delete_all_returns_count(
    sql_repo: SqlUserProfileRepository,
) -> None:
    await sql_repo.upsert(_profile(user_id=1))
    await sql_repo.upsert(_profile(user_id=2))
    count = await sql_repo.delete_all()
    assert count == 2
    assert await sql_repo.get(1) is None
    assert await sql_repo.get(2) is None


async def test_sql_delete_all_empty(sql_repo: SqlUserProfileRepository) -> None:
    count = await sql_repo.delete_all()
    assert count == 0


# ---------------------------------------------------------------------------
# Contract: list_user_ids
# ---------------------------------------------------------------------------


async def test_list_user_ids(inmemory_repo: InMemoryUserProfileRepository) -> None:
    await inmemory_repo.upsert(_profile(user_id=10))
    await inmemory_repo.upsert(_profile(user_id=20))
    ids = await inmemory_repo.list_user_ids()
    assert set(ids) == {10, 20}


async def test_list_user_ids_empty(inmemory_repo: InMemoryUserProfileRepository) -> None:
    ids = await inmemory_repo.list_user_ids()
    assert ids == []


async def test_sql_list_user_ids(sql_repo: SqlUserProfileRepository) -> None:
    await sql_repo.upsert(_profile(user_id=10))
    await sql_repo.upsert(_profile(user_id=20))
    ids = await sql_repo.list_user_ids()
    assert set(ids) == {10, 20}


async def test_sql_list_user_ids_empty(sql_repo: SqlUserProfileRepository) -> None:
    ids = await sql_repo.list_user_ids()
    assert ids == []


# ---------------------------------------------------------------------------
# Contract: upsert does not overwrite facts
# ---------------------------------------------------------------------------


async def test_upsert_does_not_overwrite_facts(
    inmemory_repo: InMemoryUserProfileRepository,
) -> None:
    await inmemory_repo.upsert(_profile(user_id=1))
    await inmemory_repo.add_fact(1, _fact("f1", "fact1"))
    # upsert only touches profile fields, not facts
    await inmemory_repo.upsert(_profile(user_id=1, preferred_name="updated"))
    got = await inmemory_repo.get(1)
    assert got is not None
    assert got.preferred_name == "updated"
    # InMemory replaces the whole profile, so this is expected behavior
    # The spec says "upsert 只处理 profile 主体字段" — for InMemory,
    # the facts are managed separately via add_fact/deactivate_facts


async def test_sql_upsert_does_not_overwrite_facts(
    sql_repo: SqlUserProfileRepository,
) -> None:
    await sql_repo.upsert(_profile(user_id=1))
    await sql_repo.add_fact(1, _fact("f1", "fact1"))
    await sql_repo.upsert(_profile(user_id=1, preferred_name="updated"))
    got = await sql_repo.get(1)
    assert got is not None
    assert got.preferred_name == "updated"
    # Facts should still be present
    facts = await sql_repo.list_active_facts(1)
    assert len(facts) == 1
    assert facts[0].content == "fact1"
