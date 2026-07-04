"""JSON→DB one-shot migration: import legacy ``data/memory`` into SQLite.

Reads session JSONs, user profile JSONs, and the social graph JSON, then
upserts into the corresponding SQLite tables.  Idempotent by design —
re-running produces no duplicate rows thanks to PK / unique-constraint
upserts.

Usage (typically via CLI ``lingxuan migrate-memory``)::

    from lingxuan.migration.from_json import migrate_from_json

    report = await migrate_from_json(
        source=Path("data/memory"),
        db=Database("sqlite+aiosqlite:///data/lingxuan.db"),
        dry_run=False,
    )
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

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
from lingxuan.protocols.messaging import SessionId


# ---------------------------------------------------------------------------
# Report data structures
# ---------------------------------------------------------------------------


@dataclass
class DomainCount:
    """Per-domain migration counters.

    ``inserted`` counts rows that were newly created (not previously existing).
    ``updated`` counts rows that matched an existing PK/unique key and were
    updated in place.  ``inserted + updated`` equals the total rows affected
    by upsert statements.
    """

    scanned: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    skipped_reasons: list[str] = field(default_factory=list)


@dataclass
class MigrationReport:
    """Full migration report — serialisable to JSON."""

    source: str = ""
    dry_run: bool = False
    sessions: DomainCount = field(default_factory=DomainCount)
    messages: DomainCount = field(default_factory=DomainCount)
    entities: DomainCount = field(default_factory=DomainCount)
    user_profiles: DomainCount = field(default_factory=DomainCount)
    user_facts: DomainCount = field(default_factory=DomainCount)
    social_edges: DomainCount = field(default_factory=DomainCount)
    name_index: DomainCount = field(default_factory=DomainCount)
    errors: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict."""
        return {
            "source": self.source,
            "dry_run": self.dry_run,
            "sessions": _dc_to_dict(self.sessions),
            "messages": _dc_to_dict(self.messages),
            "entities": _dc_to_dict(self.entities),
            "user_profiles": _dc_to_dict(self.user_profiles),
            "user_facts": _dc_to_dict(self.user_facts),
            "social_edges": _dc_to_dict(self.social_edges),
            "name_index": _dc_to_dict(self.name_index),
            "errors": self.errors,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "timestamp": self.timestamp,
        }


def _dc_to_dict(dc: DomainCount) -> dict[str, Any]:
    return {
        "scanned": dc.scanned,
        "inserted": dc.inserted,
        "updated": dc.updated,
        "skipped": dc.skipped,
        "skipped_reasons": dc.skipped_reasons,
    }


# ---------------------------------------------------------------------------
# JSON parsing helpers
# ---------------------------------------------------------------------------


def _parse_session_json(raw: Any) -> dict[str, Any]:
    """Parse a session JSON file (v1 list or v2 dict) into a normalised dict.

    Returns a dict with keys: ``version``, ``history``, ``summary``, ``meta``.
    """
    if isinstance(raw, list):
        # v1 format: bare list of messages
        return {"version": 1, "history": raw, "summary": "", "meta": {}}
    if isinstance(raw, dict):
        return {
            "version": int(raw.get("version", 2)),
            "history": list(raw.get("history", [])),
            "summary": str(raw.get("summary", "")),
            "meta": dict(raw.get("meta", {})),
        }
    return {"version": 2, "history": [], "summary": "", "meta": {}}


def _parse_session_id(filename_stem: str) -> SessionId | None:
    """Derive a ``SessionId`` from a JSON filename stem like ``private_123``."""
    if filename_stem.startswith("private_"):
        try:
            peer = int(filename_stem[len("private_"):])
            return SessionId(kind="private", peer_id=peer)
        except ValueError:
            return None
    if filename_stem.startswith("group_"):
        try:
            peer = int(filename_stem[len("group_"):])
            return SessionId(kind="group", peer_id=peer)
        except ValueError:
            return None
    return None


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _safe_str(val: Any, default: str = "") -> str:
    if val is None:
        return default
    return str(val)


def _safe_float(val: Any, default: float = 1.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_bool(val: Any, default: bool = True) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    return default


# ---------------------------------------------------------------------------
# Core migration logic
# ---------------------------------------------------------------------------


async def migrate_from_json(
    source: Path,
    db: Database,
    *,
    dry_run: bool = False,
) -> MigrationReport:
    """Run the JSON→DB migration.

    Args:
        source: Path to the ``data/memory`` directory.
        db: Async Database instance (schema must already be ensured).
        dry_run: If True, scan and validate only — do not write to DB.

    Returns:
        A ``MigrationReport`` with per-domain counts and any errors.
    """
    report = MigrationReport(
        source=str(source),
        dry_run=dry_run,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    t0 = time.monotonic()

    if not source.is_dir():
        report.errors.append(f"Source directory does not exist: {source}")
        report.elapsed_seconds = time.monotonic() - t0
        return report

    # Snapshot row counts before migration so we can distinguish
    # truly new rows (inserted) from updated existing rows.
    pre_counts: dict[str, int] = {}
    if not dry_run:
        async with db.session() as s:
            for name, orm_cls in [
                ("sessions", SessionRow),
                ("messages", SessionMessageRow),
                ("entities", SessionEntityRow),
                ("user_profiles", UserProfileRow),
                ("user_facts", UserFactRow),
                ("social_edges", SocialEdgeRow),
                ("name_index", NameIndexRow),
            ]:
                cnt = await s.scalar(select(func.count()).select_from(orm_cls))
                pre_counts[name] = cnt or 0

    # ── Phase 1: sessions → messages → entities ─────────────────────────
    await _migrate_sessions(source, db, report, dry_run=dry_run)

    # ── Phase 2: user_profiles → user_facts ─────────────────────────────
    await _migrate_user_profiles(source, db, report, dry_run=dry_run)

    # ── Phase 3: social_edges → name_index ──────────────────────────────
    await _migrate_social_graph(source, db, report, dry_run=dry_run)

    # Compute true inserted vs updated from actual row-count delta
    if not dry_run:
        async with db.session() as s:
            for name, orm_cls, dc in [
                ("sessions", SessionRow, report.sessions),
                ("messages", SessionMessageRow, report.messages),
                ("entities", SessionEntityRow, report.entities),
                ("user_profiles", UserProfileRow, report.user_profiles),
                ("user_facts", UserFactRow, report.user_facts),
                ("social_edges", SocialEdgeRow, report.social_edges),
                ("name_index", NameIndexRow, report.name_index),
            ]:
                post_cnt = await s.scalar(select(func.count()).select_from(orm_cls))
                new_rows = (post_cnt or 0) - pre_counts.get(name, 0)
                # "inserted" from upsert rowcount = new + updated;
                # actual new rows = post - pre
                dc.updated = max(0, dc.inserted - new_rows)
                dc.inserted = max(0, new_rows)

    report.elapsed_seconds = time.monotonic() - t0
    return report


# ---------------------------------------------------------------------------
# Phase 1: Sessions
# ---------------------------------------------------------------------------


async def _migrate_sessions(
    source: Path,
    db: Database,
    report: MigrationReport,
    *,
    dry_run: bool,
) -> None:
    """Migrate ``{source}/*.json`` (private_*/group_*) → sessions + messages + entities."""
    session_files = sorted(source.glob("*.json"))
    # Filter to only session files (private_*/group_*)
    session_files = [
        f for f in session_files
        if f.stem.startswith("private_") or f.stem.startswith("group_")
    ]

    for path in session_files:
        report.sessions.scanned += 1
        sid = _parse_session_id(path.stem)
        if sid is None:
            report.sessions.skipped += 1
            report.sessions.skipped_reasons.append(
                f"Cannot parse session_id from filename: {path.name}"
            )
            continue

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            report.sessions.skipped += 1
            report.sessions.skipped_reasons.append(
                f"Failed to read {path.name}: {exc}"
            )
            continue

        parsed = _parse_session_json(raw)
        meta: dict[str, Any] = parsed["meta"]
        key = sid.as_str()

        # Derive session fields from meta
        group_id: int | None = meta.get("group_id")
        if group_id is not None:
            group_id = _safe_int(group_id)
            if group_id == 0:
                group_id = None
        nickname = _safe_str(meta.get("nickname", ""))
        last_active_at = _safe_str(meta.get("last_active_at", "")) or None
        summary = _safe_str(parsed["summary"], "")
        # Store any meta keys not promoted to named columns
        promoted_keys = {"last_active_at", "nickname", "group_id", "entities"}
        extra_meta = {k: v for k, v in meta.items() if k not in promoted_keys}
        meta_json = json.dumps(extra_meta, ensure_ascii=False) if extra_meta else None

        if dry_run:
            report.sessions.inserted += 1
        else:
            async with db.session() as s:
                stmt = sqlite_insert(SessionRow).values(
                    session_id=key,
                    kind=sid.kind,
                    group_id=group_id,
                    summary=summary,
                    nickname=nickname,
                    last_active_at=last_active_at,
                    created_at=last_active_at or datetime.now(timezone.utc).isoformat(),
                    meta_json=meta_json,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["session_id"],
                    set_={
                        "kind": stmt.excluded.kind,
                        "group_id": stmt.excluded.group_id,
                        "summary": stmt.excluded.summary,
                        "nickname": stmt.excluded.nickname,
                        "last_active_at": stmt.excluded.last_active_at,
                        "meta_json": stmt.excluded.meta_json,
                    },
                )
                result = await s.execute(stmt)
                # rowcount >= 1 means the statement affected a row (insert or update)
                if result.rowcount and result.rowcount >= 1:
                    report.sessions.inserted += 1

        # ── Messages ────────────────────────────────────────────────────
        history: list[dict[str, Any]] = parsed["history"]
        for seq, msg in enumerate(history):
            report.messages.scanned += 1
            role = _safe_str(msg.get("role", ""), "user")
            content = _safe_str(msg.get("content", ""))
            user_id = msg.get("user_id")
            if user_id is not None:
                user_id = _safe_int(user_id)
                if user_id == 0:
                    user_id = None

            if dry_run:
                report.messages.inserted += 1
            else:
                async with db.session() as s:
                    stmt = sqlite_insert(SessionMessageRow).values(
                        session_id=key,
                        seq=seq,
                        role=role,
                        content=content,
                        user_id=user_id,
                        created_at=last_active_at or datetime.now(timezone.utc).isoformat(),
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["session_id", "seq"],
                        set_={
                            "role": stmt.excluded.role,
                            "content": stmt.excluded.content,
                            "user_id": stmt.excluded.user_id,
                            "created_at": stmt.excluded.created_at,
                        },
                    )
                    result = await s.execute(stmt)
                    if result.rowcount and result.rowcount >= 1:
                        report.messages.inserted += 1

        # ── Entities ────────────────────────────────────────────────────
        entities_raw = meta.get("entities", {})
        if isinstance(entities_raw, dict):
            for name, uid in entities_raw.items():
                report.entities.scanned += 1
                name_str = str(name).strip()
                uid_int = _safe_int(uid)
                if not name_str or uid_int == 0:
                    report.entities.skipped += 1
                    report.entities.skipped_reasons.append(
                        f"Session {key}: invalid entity ({name_str!r} → {uid})"
                    )
                    continue

                if dry_run:
                    report.entities.inserted += 1
                else:
                    async with db.session() as s:
                        stmt = sqlite_insert(SessionEntityRow).values(
                            session_id=key,
                            name=name_str,
                            user_id=uid_int,
                        )
                        stmt = stmt.on_conflict_do_update(
                            index_elements=["session_id", "name"],
                            set_={"user_id": stmt.excluded.user_id},
                        )
                        result = await s.execute(stmt)
                        if result.rowcount and result.rowcount >= 1:
                            report.entities.inserted += 1


# ---------------------------------------------------------------------------
# Phase 2: User profiles + facts
# ---------------------------------------------------------------------------


async def _migrate_user_profiles(
    source: Path,
    db: Database,
    report: MigrationReport,
    *,
    dry_run: bool,
) -> None:
    """Migrate ``{source}/users/*.json`` → user_profiles + user_facts."""
    users_dir = source / "users"
    if not users_dir.is_dir():
        return

    user_files = sorted(users_dir.glob("*.json"))
    for path in user_files:
        # Stem should be a numeric user_id
        try:
            user_id = int(path.stem)
        except ValueError:
            report.user_profiles.skipped += 1
            report.user_profiles.skipped_reasons.append(
                f"Filename is not a numeric user_id: {path.name}"
            )
            continue

        report.user_profiles.scanned += 1

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            report.user_profiles.skipped += 1
            report.user_profiles.skipped_reasons.append(
                f"Failed to read {path.name}: {exc}"
            )
            continue

        if not isinstance(raw, dict):
            report.user_profiles.skipped += 1
            report.user_profiles.skipped_reasons.append(
                f"User profile is not a dict: {path.name}"
            )
            continue

        identity = raw.get("identity", {})
        relationship = raw.get("relationship", {})
        cognition = raw.get("cognition", {})

        preferred_name = _safe_str(identity.get("preferred_name", ""))
        aliases = identity.get("aliases", [])
        if not isinstance(aliases, list):
            aliases = []
        group_cards = identity.get("group_cards", {})
        if not isinstance(group_cards, dict):
            group_cards = {}
        stage = _safe_str(relationship.get("stage", ""), "stranger")
        first_met_at = _safe_str(relationship.get("first_met_at", "")) or None
        last_seen_at = _safe_str(relationship.get("last_seen_at", "")) or None
        interaction_count = _safe_int(relationship.get("interaction_count", 0))
        last_group_id = relationship.get("last_group_id")
        if last_group_id is not None:
            last_group_id = _safe_int(last_group_id)
            if last_group_id == 0:
                last_group_id = None
        seen_in_private = _safe_bool(relationship.get("seen_in_private", False))
        seen_in_group = _safe_bool(relationship.get("seen_in_group", False))
        impression = _safe_str(raw.get("impression", ""))
        cognition_summary = _safe_str(cognition.get("summary", ""))
        cognition_updated_at = _safe_str(cognition.get("updated_at", "")) or None
        cognition_interaction_at_update = _safe_int(
            cognition.get("interaction_at_update", 0)
        )

        if dry_run:
            report.user_profiles.inserted += 1
        else:
            async with db.session() as s:
                stmt = sqlite_insert(UserProfileRow).values(
                    user_id=user_id,
                    preferred_name=preferred_name,
                    aliases_json=json.dumps(aliases, ensure_ascii=False),
                    group_cards_json=json.dumps(group_cards, ensure_ascii=False),
                    stage=stage,
                    first_met_at=first_met_at,
                    last_seen_at=last_seen_at,
                    interaction_count=interaction_count,
                    last_group_id=last_group_id,
                    seen_in_private=seen_in_private,
                    seen_in_group=seen_in_group,
                    impression=impression,
                    cognition_summary=cognition_summary,
                    cognition_updated_at=cognition_updated_at,
                    cognition_interaction_at_update=cognition_interaction_at_update,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["user_id"],
                    set_={
                        "preferred_name": stmt.excluded.preferred_name,
                        "aliases_json": stmt.excluded.aliases_json,
                        "group_cards_json": stmt.excluded.group_cards_json,
                        "stage": stmt.excluded.stage,
                        "first_met_at": stmt.excluded.first_met_at,
                        "last_seen_at": stmt.excluded.last_seen_at,
                        "interaction_count": stmt.excluded.interaction_count,
                        "last_group_id": stmt.excluded.last_group_id,
                        "seen_in_private": stmt.excluded.seen_in_private,
                        "seen_in_group": stmt.excluded.seen_in_group,
                        "impression": stmt.excluded.impression,
                        "cognition_summary": stmt.excluded.cognition_summary,
                        "cognition_updated_at": stmt.excluded.cognition_updated_at,
                        "cognition_interaction_at_update": stmt.excluded.cognition_interaction_at_update,
                    },
                )
                result = await s.execute(stmt)
                if result.rowcount and result.rowcount >= 1:
                    report.user_profiles.inserted += 1

        # ── Facts ───────────────────────────────────────────────────────
        facts_raw = raw.get("facts", [])
        if not isinstance(facts_raw, list):
            facts_raw = []

        for fact_data in facts_raw:
            report.user_facts.scanned += 1
            if not isinstance(fact_data, dict):
                report.user_facts.skipped += 1
                report.user_facts.skipped_reasons.append(
                    f"User {user_id}: fact entry is not a dict"
                )
                continue

            fact_id = _safe_str(fact_data.get("id", ""))
            if not fact_id:
                report.user_facts.skipped += 1
                report.user_facts.skipped_reasons.append(
                    f"User {user_id}: fact missing id"
                )
                continue

            content = _safe_str(fact_data.get("content", ""))
            category = _safe_str(fact_data.get("category", ""), "general")
            source_user_id = _safe_int(fact_data.get("source_user_id", 0))
            learned_at = _safe_str(fact_data.get("learned_at", ""))
            if not learned_at:
                learned_at = datetime.now(timezone.utc).isoformat()
            confidence = _safe_float(fact_data.get("confidence", 1.0))
            active = _safe_bool(fact_data.get("active", True))
            supersedes = fact_data.get("supersedes")
            if supersedes is not None:
                supersedes = _safe_str(supersedes)
                if not supersedes:
                    supersedes = None

            if dry_run:
                report.user_facts.inserted += 1
            else:
                async with db.session() as s:
                    stmt = sqlite_insert(UserFactRow).values(
                        id=fact_id,
                        user_id=user_id,
                        content=content,
                        category=category,
                        source_user_id=source_user_id,
                        learned_at=learned_at,
                        confidence=confidence,
                        active=active,
                        supersedes=supersedes,
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["id"],
                        set_={
                            "content": stmt.excluded.content,
                            "category": stmt.excluded.category,
                            "source_user_id": stmt.excluded.source_user_id,
                            "learned_at": stmt.excluded.learned_at,
                            "confidence": stmt.excluded.confidence,
                            "active": stmt.excluded.active,
                            "supersedes": stmt.excluded.supersedes,
                        },
                    )
                    result = await s.execute(stmt)
                    if result.rowcount and result.rowcount >= 1:
                        report.user_facts.inserted += 1


# ---------------------------------------------------------------------------
# Phase 3: Social graph
# ---------------------------------------------------------------------------


async def _migrate_social_graph(
    source: Path,
    db: Database,
    report: MigrationReport,
    *,
    dry_run: bool,
) -> None:
    """Migrate ``{source}/social_graph.json`` → social_edges + name_index."""
    graph_path = source / "social_graph.json"
    if not graph_path.is_file():
        return

    try:
        raw = json.loads(graph_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        report.errors.append(f"Failed to read social_graph.json: {exc}")
        return

    if not isinstance(raw, dict):
        report.errors.append("social_graph.json is not a dict")
        return

    # ── Edges ───────────────────────────────────────────────────────────
    edges_raw = raw.get("edges", [])
    if not isinstance(edges_raw, list):
        edges_raw = []

    for edge_data in edges_raw:
        report.social_edges.scanned += 1
        if not isinstance(edge_data, dict):
            report.social_edges.skipped += 1
            report.social_edges.skipped_reasons.append(
                "Edge entry is not a dict"
            )
            continue

        from_user_id = _safe_int(edge_data.get("from_user_id", 0))
        to_user_id = _safe_int(edge_data.get("to_user_id", 0))
        relation = _safe_str(edge_data.get("relation", ""))
        label = _safe_str(edge_data.get("label", ""))
        evidence = _safe_str(edge_data.get("evidence", ""))
        group_id = edge_data.get("group_id")
        if group_id is not None:
            group_id = _safe_int(group_id)
            if group_id == 0:
                group_id = None
        learned_at = _safe_str(edge_data.get("learned_at", ""))
        if not learned_at:
            learned_at = datetime.now(timezone.utc).isoformat()

        if not relation:
            report.social_edges.skipped += 1
            report.social_edges.skipped_reasons.append(
                f"Edge missing relation: ({from_user_id}→{to_user_id})"
            )
            continue

        if dry_run:
            report.social_edges.inserted += 1
        else:
            async with db.session() as s:
                stmt = sqlite_insert(SocialEdgeRow).values(
                    from_user_id=from_user_id,
                    to_user_id=to_user_id,
                    relation=relation,
                    label=label,
                    evidence=evidence,
                    group_id=group_id,
                    learned_at=learned_at,
                )
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["from_user_id", "to_user_id", "relation", "label"],
                )
                result = await s.execute(stmt)
                # rowcount == 1 means new row; 0 means duplicate (conflict)
                if result.rowcount == 1:
                    report.social_edges.inserted += 1

    # ── Name index ──────────────────────────────────────────────────────
    name_index_raw = raw.get("name_index", {})
    if not isinstance(name_index_raw, dict):
        name_index_raw = {}

    for name, uid in name_index_raw.items():
        report.name_index.scanned += 1
        name_str = str(name).strip()
        uid_int = _safe_int(uid)
        if not name_str or uid_int == 0:
            report.name_index.skipped += 1
            report.name_index.skipped_reasons.append(
                f"Invalid name_index entry ({name_str!r} → {uid})"
            )
            continue

        if dry_run:
            report.name_index.inserted += 1
        else:
            async with db.session() as s:
                stmt = sqlite_insert(NameIndexRow).values(
                    name=name_str,
                    user_id=uid_int,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["name"],
                    set_={
                        "user_id": stmt.excluded.user_id,
                        "updated_at": stmt.excluded.updated_at,
                    },
                )
                result = await s.execute(stmt)
                if result.rowcount and result.rowcount >= 1:
                    report.name_index.inserted += 1
