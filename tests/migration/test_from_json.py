"""Tests for JSON→DB migration (P3-02).

Constructs sample ``data/memory`` directory structures and asserts:
- Correct row counts per table after migration.
- Idempotency: a second run adds no new rows.
- Dry-run: DB stays empty but report is non-empty.
- Edge cases: v1 session format, soft-deleted facts, duplicate social edges,
  bad data (gracefully skipped).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from lingxuan.adapters.storage.db import Database
from lingxuan.adapters.storage.orm import (
    NameIndex as NameIndexRow,
    Session as SessionRow,
    SessionEntity as SessionEntityRow,
    SessionMessage as SessionMessageRow,
    SocialEdge as SocialEdgeRow,
    UserFact as UserFactRow,
    UserProfile as UserProfileRow,
)
from lingxuan.migration.from_json import migrate_from_json


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    """Create a temp-file SQLite Database with schema ensured."""
    db_file = tmp_path / "test.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    database = Database(db_url)
    database.ensure_schema()
    yield database
    await database.dispose()


@pytest.fixture
def sample_source(tmp_path: Path) -> Path:
    """Build a sample ``data/memory`` directory with realistic data.

    Includes:
    - A v2 private session with entities
    - A v1 (bare list) group session
    - Two user profiles with facts (one with soft-deleted fact)
    - A social graph with duplicate edges and a name index
    """
    source = tmp_path / "memory"
    source.mkdir()

    # ── v2 private session ──────────────────────────────────────────────
    private_session = {
        "version": 2,
        "history": [
            {"role": "user", "content": "[小明]: 你好", "user_id": 10001},
            {"role": "assistant", "content": "你好呀~"},
            {"role": "user", "content": "[小明]: 今天天气不错", "user_id": 10001},
            {"role": "assistant", "content": "是呀，适合出去走走~"},
        ],
        "summary": "小明和灵轩打了招呼并聊了天气",
        "meta": {
            "last_active_at": "2026-06-15T10:30:00+00:00",
            "nickname": "小明",
            "entities": {"小堞宝": 10002},
        },
    }
    (source / "private_10001.json").write_text(
        json.dumps(private_session, ensure_ascii=False), encoding="utf-8"
    )

    # ── v1 (bare list) group session ────────────────────────────────────
    group_history = [
        {"role": "user", "content": "[小红]: 大家好", "user_id": 20001},
        {"role": "assistant", "content": "大家好呀~"},
    ]
    (source / "group_30001.json").write_text(
        json.dumps(group_history, ensure_ascii=False), encoding="utf-8"
    )

    # ── v2 group session with group_id in meta ─────────────────────────
    group_session = {
        "version": 2,
        "history": [
            {"role": "user", "content": "[小刚]: 在吗", "user_id": 30001},
        ],
        "summary": "",
        "meta": {
            "last_active_at": "2026-06-16T14:00:00+00:00",
            "group_id": 30002,
            "entities": {"小刚": 30001, "小芳": 30003},
        },
    }
    (source / "group_30002.json").write_text(
        json.dumps(group_session, ensure_ascii=False), encoding="utf-8"
    )

    # ── User profiles ───────────────────────────────────────────────────
    users_dir = source / "users"
    users_dir.mkdir()

    # User 10001 — active and soft-deleted facts
    profile_10001 = {
        "version": 2,
        "user_id": 10001,
        "identity": {
            "preferred_name": "小明",
            "aliases": ["明明", "小明同学"],
            "group_cards": {"30001": "小明在30001", "30002": "小明在30002"},
        },
        "relationship": {
            "stage": "familiar",
            "first_met_at": "2026-01-15T08:30:00+00:00",
            "last_seen_at": "2026-06-15T10:30:00+00:00",
            "interaction_count": 42,
            "last_group_id": 30001,
            "seen_in_private": True,
            "seen_in_group": True,
        },
        "facts": [
            {
                "id": "a1b2c3d4",
                "content": "喜欢喝咖啡",
                "category": "preference",
                "source_user_id": 10001,
                "learned_at": "2026-03-10T14:20:00+00:00",
                "confidence": 1.0,
                "active": True,
                "supersedes": None,
            },
            {
                "id": "e5f6g7h8",
                "content": "曾经喜欢喝茶",
                "category": "preference",
                "source_user_id": 10001,
                "learned_at": "2026-01-10T09:00:00+00:00",
                "confidence": 0.8,
                "active": False,
                "supersedes": "a1b2c3d4",
            },
        ],
        "impression": "热情开朗",
        "cognition": {
            "summary": "一个喜欢咖啡的人",
            "updated_at": "2026-06-01T10:00:00+00:00",
            "interaction_at_update": 38,
        },
    }
    (users_dir / "10001.json").write_text(
        json.dumps(profile_10001, ensure_ascii=False), encoding="utf-8"
    )

    # User 10002 — minimal profile
    profile_10002 = {
        "version": 2,
        "user_id": 10002,
        "identity": {"preferred_name": "小堞宝", "aliases": [], "group_cards": {}},
        "relationship": {
            "stage": "acquaintance",
            "first_met_at": "2026-02-20T12:00:00+00:00",
            "last_seen_at": "2026-06-15T10:30:00+00:00",
            "interaction_count": 5,
            "last_group_id": None,
            "seen_in_private": False,
            "seen_in_group": True,
        },
        "facts": [
            {
                "id": "f1f2f3f4",
                "content": "小堞宝是小明介绍的朋友",
                "category": "relation",
                "source_user_id": 10001,
                "learned_at": "2026-02-20T12:00:00+00:00",
                "confidence": 1.0,
                "active": True,
                "supersedes": None,
            },
        ],
        "impression": "",
        "cognition": {"summary": "", "updated_at": "", "interaction_at_update": 0},
    }
    (users_dir / "10002.json").write_text(
        json.dumps(profile_10002, ensure_ascii=False), encoding="utf-8"
    )

    # ── Social graph ────────────────────────────────────────────────────
    social_graph = {
        "version": 1,
        "edges": [
            {
                "from_user_id": 10001,
                "to_user_id": 10002,
                "relation": "introduced_as",
                "label": "小堞宝",
                "evidence": "这位就是小堞宝",
                "group_id": 30001,
                "learned_at": "2026-02-20T12:00:00+00:00",
            },
            # Duplicate edge — should be deduped
            {
                "from_user_id": 10001,
                "to_user_id": 10002,
                "relation": "introduced_as",
                "label": "小堞宝",
                "evidence": "又介绍了小堞宝",
                "group_id": 30002,
                "learned_at": "2026-03-01T12:00:00+00:00",
            },
            {
                "from_user_id": 10001,
                "to_user_id": 10001,
                "relation": "self_identified_as",
                "label": "小明",
                "evidence": "我就是小明",
                "group_id": None,
                "learned_at": "2026-01-15T08:30:00+00:00",
            },
        ],
        "name_index": {
            "小堞宝": 10002,
            "小明": 10001,
            "明明": 10001,
        },
    }
    (source / "social_graph.json").write_text(
        json.dumps(social_graph, ensure_ascii=False), encoding="utf-8"
    )

    return source


# ---------------------------------------------------------------------------
# Helper: count rows in a table
# ---------------------------------------------------------------------------


async def _count(db: Database, orm_cls: type) -> int:
    async with db.session() as s:
        result = await s.execute(select(func.count()).select_from(orm_cls))
        return result.scalar_one()


# ---------------------------------------------------------------------------
# Test: full migration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_migration(db: Database, sample_source: Path) -> None:
    """Full migration should produce correct row counts across all tables."""
    report = await migrate_from_json(sample_source, db, dry_run=False)

    # Sessions: private_10001, group_30001, group_30002
    assert report.sessions.scanned == 3
    assert report.sessions.inserted == 3
    assert report.sessions.updated == 0
    assert report.sessions.skipped == 0
    assert await _count(db, SessionRow) == 3

    # Messages: 4 + 2 + 1 = 7
    assert report.messages.scanned == 7
    assert report.messages.inserted == 7
    assert report.messages.updated == 0
    assert await _count(db, SessionMessageRow) == 7

    # Entities: 小堞宝 in private_10001, 小刚 + 小芳 in group_30002 = 3
    assert report.entities.scanned == 3
    assert report.entities.inserted == 3
    assert report.entities.updated == 0
    assert await _count(db, SessionEntityRow) == 3

    # User profiles: 10001 + 10002
    assert report.user_profiles.scanned == 2
    assert report.user_profiles.inserted == 2
    assert report.user_profiles.updated == 0
    assert await _count(db, UserProfileRow) == 2

    # Facts: 2 (10001) + 1 (10002) = 3
    assert report.user_facts.scanned == 3
    assert report.user_facts.inserted == 3
    assert report.user_facts.updated == 0
    assert await _count(db, UserFactRow) == 3

    # Social edges: 3 scanned, 2 inserted (1 deduped), 0 updated
    assert report.social_edges.scanned == 3
    assert report.social_edges.inserted == 2
    assert report.social_edges.updated == 0
    assert await _count(db, SocialEdgeRow) == 2

    # Name index: 3 entries
    assert report.name_index.scanned == 3
    assert report.name_index.inserted == 3
    assert report.name_index.updated == 0
    assert await _count(db, NameIndexRow) == 3


# ---------------------------------------------------------------------------
# Test: verify key field values
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_key_field_values(db: Database, sample_source: Path) -> None:
    """Verify specific field values are correctly migrated."""
    await migrate_from_json(sample_source, db, dry_run=False)

    async with db.session() as s:
        # Session: private_10001
        result = await s.execute(
            select(SessionRow).where(SessionRow.session_id == "private_10001")
        )
        session = result.scalar_one()
        assert session.kind == "private"
        assert session.summary == "小明和灵轩打了招呼并聊了天气"
        assert session.last_active_at == "2026-06-15T10:30:00+00:00"

        # Session: group_30001 (v1 format)
        result = await s.execute(
            select(SessionRow).where(SessionRow.session_id == "group_30001")
        )
        session = result.scalar_one()
        assert session.kind == "group"
        assert session.summary == ""  # v1 has no summary

        # User profile: 10001
        result = await s.execute(
            select(UserProfileRow).where(UserProfileRow.user_id == 10001)
        )
        profile = result.scalar_one()
        assert profile.preferred_name == "小明"
        assert json.loads(profile.aliases_json) == ["明明", "小明同学"]
        assert profile.stage == "familiar"
        assert profile.interaction_count == 42
        assert profile.seen_in_private is True
        assert profile.impression == "热情开朗"

        # User fact: soft-deleted
        result = await s.execute(
            select(UserFactRow).where(UserFactRow.id == "e5f6g7h8")
        )
        fact = result.scalar_one()
        assert fact.active is False
        assert fact.supersedes == "a1b2c3d4"

        # Social edge dedup: only one edge for the duplicate 4-tuple
        result = await s.execute(
            select(SocialEdgeRow).where(
                SocialEdgeRow.from_user_id == 10001,
                SocialEdgeRow.to_user_id == 10002,
            )
        )
        edges = result.scalars().all()
        assert len(edges) == 1
        # First edge should win (ON CONFLICT DO NOTHING keeps the first)
        assert edges[0].evidence == "这位就是小堞宝"

        # Name index
        result = await s.execute(
            select(NameIndexRow).where(NameIndexRow.name == "小堞宝")
        )
        ni = result.scalar_one()
        assert ni.user_id == 10002


# ---------------------------------------------------------------------------
# Test: idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotency(db: Database, sample_source: Path) -> None:
    """Running migration twice should produce no additional rows."""
    report1 = await migrate_from_json(sample_source, db, dry_run=False)

    counts1 = {
        "sessions": await _count(db, SessionRow),
        "messages": await _count(db, SessionMessageRow),
        "entities": await _count(db, SessionEntityRow),
        "profiles": await _count(db, UserProfileRow),
        "facts": await _count(db, UserFactRow),
        "edges": await _count(db, SocialEdgeRow),
        "names": await _count(db, NameIndexRow),
    }

    report2 = await migrate_from_json(sample_source, db, dry_run=False)

    counts2 = {
        "sessions": await _count(db, SessionRow),
        "messages": await _count(db, SessionMessageRow),
        "entities": await _count(db, SessionEntityRow),
        "profiles": await _count(db, UserProfileRow),
        "facts": await _count(db, UserFactRow),
        "edges": await _count(db, SocialEdgeRow),
        "names": await _count(db, NameIndexRow),
    }

    assert counts1 == counts2, f"Row counts changed on second run: {counts1} → {counts2}"

    # The second run should report all rows as "updated" (no new "inserted")
    assert report2.sessions.inserted == 0
    assert report2.sessions.updated == report1.sessions.inserted
    assert report2.messages.inserted == 0
    assert report2.messages.updated == report1.messages.inserted
    assert report2.entities.inserted == 0
    assert report2.entities.updated == report1.entities.inserted
    assert report2.user_profiles.inserted == 0
    assert report2.user_profiles.updated == report1.user_profiles.inserted
    assert report2.user_facts.inserted == 0
    assert report2.user_facts.updated == report1.user_facts.inserted
    assert report2.name_index.inserted == 0
    assert report2.name_index.updated == report1.name_index.inserted

    # Social edges use ON CONFLICT DO NOTHING — duplicates are neither
    # inserted nor updated on second run
    assert report2.social_edges.inserted == 0
    assert report2.social_edges.updated == 0

    # Scan counts remain the same
    assert report2.sessions.scanned == report1.sessions.scanned
    assert report2.messages.scanned == report1.messages.scanned


# ---------------------------------------------------------------------------
# Test: dry-run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run(db: Database, sample_source: Path) -> None:
    """Dry-run should not write to DB but should produce a non-empty report."""
    report = await migrate_from_json(sample_source, db, dry_run=True)

    # Report should be non-empty
    assert report.dry_run is True
    assert report.sessions.scanned > 0
    assert report.sessions.inserted > 0
    assert report.messages.scanned > 0
    assert report.user_profiles.scanned > 0

    # DB should be empty
    assert await _count(db, SessionRow) == 0
    assert await _count(db, SessionMessageRow) == 0
    assert await _count(db, SessionEntityRow) == 0
    assert await _count(db, UserProfileRow) == 0
    assert await _count(db, UserFactRow) == 0
    assert await _count(db, SocialEdgeRow) == 0
    assert await _count(db, NameIndexRow) == 0


# ---------------------------------------------------------------------------
# Test: missing source directory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_source(db: Database, tmp_path: Path) -> None:
    """Migration with a non-existent source should report error, not crash."""
    missing = tmp_path / "nonexistent"
    report = await migrate_from_json(missing, db, dry_run=False)

    assert len(report.errors) == 1
    assert "does not exist" in report.errors[0]
    assert report.sessions.scanned == 0


# ---------------------------------------------------------------------------
# Test: bad data is skipped gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bad_data_skipped(db: Database, tmp_path: Path) -> None:
    """Bad JSON files should be skipped with a reason, not crash the migration."""
    source = tmp_path / "memory"
    source.mkdir()

    # Invalid JSON session file
    (source / "private_99999.json").write_text("not valid json{{", encoding="utf-8")

    # Valid session with non-session filename (should be skipped)
    (source / "other.json").write_text(
        json.dumps({"version": 2, "history": [], "summary": "", "meta": {}}),
        encoding="utf-8",
    )

    # User profile with non-numeric filename
    users = source / "users"
    users.mkdir()
    (users / "notanid.json").write_text(
        json.dumps({"version": 2, "user_id": 0, "identity": {}, "relationship": {}, "facts": [], "impression": "", "cognition": {}}),
        encoding="utf-8",
    )

    # Valid user profile to ensure it still gets processed
    (users / "50001.json").write_text(
        json.dumps({
            "version": 2,
            "user_id": 50001,
            "identity": {"preferred_name": "Valid", "aliases": [], "group_cards": {}},
            "relationship": {"stage": "stranger", "first_met_at": "", "last_seen_at": "", "interaction_count": 0, "seen_in_private": False, "seen_in_group": False},
            "facts": [],
            "impression": "",
            "cognition": {"summary": "", "updated_at": "", "interaction_at_update": 0},
        }),
        encoding="utf-8",
    )

    # Social graph with an edge missing relation
    (source / "social_graph.json").write_text(
        json.dumps({
            "version": 1,
            "edges": [
                {"from_user_id": 1, "to_user_id": 2, "relation": "", "label": "x", "learned_at": "2026-01-01T00:00:00+00:00"},
                {"from_user_id": 1, "to_user_id": 3, "relation": "friend_of", "label": "Y", "learned_at": "2026-01-01T00:00:00+00:00"},
            ],
            "name_index": {},
        }),
        encoding="utf-8",
    )

    report = await migrate_from_json(source, db, dry_run=False)

    # The bad session was skipped
    assert report.sessions.skipped == 1
    assert any("private_99999" in r for r in report.sessions.skipped_reasons)

    # "other.json" is not a session filename — it wasn't even scanned as a session
    # (only private_*/group_* files are picked up)
    assert report.sessions.scanned == 1  # only private_99999

    # Non-numeric user file was skipped
    assert report.user_profiles.skipped == 1
    assert report.user_profiles.inserted == 1  # 50001 still processed

    # Edge missing relation was skipped
    assert report.social_edges.skipped == 1
    assert report.social_edges.inserted == 1  # friend_of edge still processed


# ---------------------------------------------------------------------------
# Test: empty source directory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_source(db: Database, tmp_path: Path) -> None:
    """Migration with an empty source directory should succeed with zero counts."""
    source = tmp_path / "memory"
    source.mkdir()

    report = await migrate_from_json(source, db, dry_run=False)

    assert report.sessions.scanned == 0
    assert report.messages.scanned == 0
    assert report.entities.scanned == 0
    assert report.user_profiles.scanned == 0
    assert report.user_facts.scanned == 0
    assert report.social_edges.scanned == 0
    assert report.name_index.scanned == 0
    assert report.errors == []
    assert report.elapsed_seconds >= 0


# ---------------------------------------------------------------------------
# Test: report serialisation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_serialisation(db: Database, sample_source: Path) -> None:
    """Report.to_dict() should produce a valid JSON-serialisable dict."""
    report = await migrate_from_json(sample_source, db, dry_run=False)
    d = report.to_dict()

    # Should be JSON-serialisable without errors
    json_str = json.dumps(d, ensure_ascii=False)
    assert isinstance(json_str, str)

    # Should have all expected keys
    assert "source" in d
    assert "dry_run" in d
    assert "sessions" in d
    assert "messages" in d
    assert "entities" in d
    assert "user_profiles" in d
    assert "user_facts" in d
    assert "social_edges" in d
    assert "name_index" in d
    assert "errors" in d
    assert "elapsed_seconds" in d
    assert "timestamp" in d

    # Each domain should have scanned/inserted/updated/skipped
    for domain in ["sessions", "messages", "entities", "user_profiles", "user_facts", "social_edges", "name_index"]:
        assert "scanned" in d[domain]
        assert "inserted" in d[domain]
        assert "updated" in d[domain]
        assert "skipped" in d[domain]


# ---------------------------------------------------------------------------
# Test: v1 session format (bare list)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v1_session_format(db: Database, tmp_path: Path) -> None:
    """v1 (bare list) session files should be migrated as v2 with empty summary/meta."""
    source = tmp_path / "memory"
    source.mkdir()

    # v1: bare list of messages
    history = [
        {"role": "user", "content": "Hello", "user_id": 123},
        {"role": "assistant", "content": "Hi there"},
    ]
    (source / "private_123.json").write_text(
        json.dumps(history, ensure_ascii=False), encoding="utf-8"
    )

    report = await migrate_from_json(source, db, dry_run=False)

    assert report.sessions.scanned == 1
    assert report.sessions.inserted == 1
    assert report.messages.scanned == 2
    assert report.messages.inserted == 2

    async with db.session() as s:
        result = await s.execute(
            select(SessionRow).where(SessionRow.session_id == "private_123")
        )
        session = result.scalar_one()
        assert session.kind == "private"
        assert session.summary == ""


# ---------------------------------------------------------------------------
# Test: session with no entities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_without_entities(db: Database, tmp_path: Path) -> None:
    """Session with no meta.entities should not create any entity rows."""
    source = tmp_path / "memory"
    source.mkdir()

    session_data = {
        "version": 2,
        "history": [{"role": "user", "content": "test"}],
        "summary": "",
        "meta": {"last_active_at": "2026-06-01T00:00:00+00:00"},
    }
    (source / "private_555.json").write_text(
        json.dumps(session_data, ensure_ascii=False), encoding="utf-8"
    )

    report = await migrate_from_json(source, db, dry_run=False)

    assert report.entities.scanned == 0
    assert await _count(db, SessionEntityRow) == 0
