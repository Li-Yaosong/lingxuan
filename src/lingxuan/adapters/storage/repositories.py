"""SQLite-backed repository implementations using SQLAlchemy 2.0 async ORM.

This module provides ``SqlSessionRepository`` (P2-04),
``SqlUserProfileRepository`` (P2-05), ``SqlSocialGraphRepository`` (P2-06),
``SqlConfigRepository``, ``SqlAuditRepository``,
``SqlPluginConfigRepository``, and ``SqlAdminUserRepository`` (P2-07).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import delete as sa_delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import selectinload

from lingxuan.adapters.storage.db import Database
from lingxuan.adapters.storage.orm import (
    AdminUser as AdminUserRow,
    AuditLog as AuditLogRow,
    NameIndex as NameIndexRow,
    PluginConfig as PluginConfigRow,
    Session as SessionRow,
    SessionEntity as SessionEntityRow,
    SessionMessage as SessionMessageRow,
    Setting as SettingRow,
    SocialEdge as SocialEdgeRow,
    UserFact as UserFactRow,
    UserProfile as UserProfileRow,
)
from lingxuan.protocols.messaging import SessionId
from lingxuan.protocols.repositories import (
    AdminUserRow as AdminUserDTO,
    AuditEntry,
    Session,
    SocialEdge,
    StoredMessage,
    UserFact,
    UserProfile,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_session(row: SessionRow) -> Session:
    """Convert an ORM ``SessionRow`` to a Protocol ``Session`` DTO."""
    last_active = row.last_active_at
    if isinstance(last_active, str) and last_active:
        last_active = datetime.fromisoformat(last_active)
    else:
        last_active = None

    return Session(
        session_id=SessionId.parse(row.session_id),
        kind=row.kind,
        group_id=row.group_id,
        summary=row.summary,
        nickname=row.nickname,
        last_active_at=last_active,
    )


def _row_to_stored_message(row: SessionMessageRow) -> StoredMessage:
    """Convert an ORM ``SessionMessageRow`` to a Protocol ``StoredMessage`` DTO."""
    created_at: datetime
    if isinstance(row.created_at, str) and row.created_at:
        created_at = datetime.fromisoformat(row.created_at)
    else:
        created_at = datetime.now(timezone.utc)

    return StoredMessage(
        role=row.role,
        content=row.content,
        user_id=row.user_id,
        seq=row.seq,
        created_at=created_at,
    )


class SqlSessionRepository:
    """SQLite-backed implementation of ``SessionRepository`` Protocol.

    Injected with a ``Database`` instance; each method opens its own
    ``db.session()`` context so that operations are transactional.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # get
    # ------------------------------------------------------------------

    async def get(self, sid: SessionId) -> Session | None:
        key = sid.as_str()
        async with self._db.session() as s:
            result = await s.execute(
                select(SessionRow).where(SessionRow.session_id == key)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return _row_to_session(row)

    # ------------------------------------------------------------------
    # ensure
    # ------------------------------------------------------------------

    async def ensure(
        self,
        sid: SessionId,
        *,
        group_id: int | None = None,
        nickname: str = "",
    ) -> Session:
        key = sid.as_str()
        async with self._db.session() as s:
            result = await s.execute(
                select(SessionRow).where(SessionRow.session_id == key)
            )
            row = result.scalar_one_or_none()
            if row is not None:
                return _row_to_session(row)

            row = SessionRow(
                session_id=key,
                kind=sid.kind,
                group_id=group_id,
                nickname=nickname,
                created_at=_now_iso(),
            )
            s.add(row)
            await s.flush()
            return _row_to_session(row)

    # ------------------------------------------------------------------
    # append_message
    # ------------------------------------------------------------------

    async def append_message(self, sid: SessionId, msg: StoredMessage) -> None:
        key = sid.as_str()
        # ensure session exists
        await self.ensure(sid)

        async with self._db.session() as s:
            # compute next seq
            result = await s.execute(
                select(func.coalesce(func.max(SessionMessageRow.seq), -1)).where(
                    SessionMessageRow.session_id == key
                )
            )
            max_seq = result.scalar_one()
            next_seq = max_seq + 1

            created_at = msg.created_at
            if not created_at or created_at.year == 1:
                created_at = datetime.now(timezone.utc)

            row = SessionMessageRow(
                session_id=key,
                seq=next_seq,
                role=msg.role,
                content=msg.content,
                user_id=msg.user_id,
                created_at=created_at.isoformat(),
            )
            s.add(row)

            # update last_active_at
            await s.execute(
                update(SessionRow)
                .where(SessionRow.session_id == key)
                .values(last_active_at=_now_iso())
            )

    # ------------------------------------------------------------------
    # load_history
    # ------------------------------------------------------------------

    async def load_history(
        self, sid: SessionId, *, limit: int | None = None, before_seq: int | None = None
    ) -> list[StoredMessage]:
        key = sid.as_str()
        async with self._db.session() as s:
            stmt = (
                select(SessionMessageRow)
                .where(SessionMessageRow.session_id == key)
                .order_by(SessionMessageRow.id.desc())
            )
            if before_seq is not None:
                # Keyset: find the row with this seq, then get rows with smaller id
                sub = (
                    select(SessionMessageRow.id)
                    .where(
                        SessionMessageRow.session_id == key,
                        SessionMessageRow.seq == before_seq,
                    )
                    .limit(1)
                )
                stmt = (
                    select(SessionMessageRow)
                    .where(
                        SessionMessageRow.session_id == key,
                        SessionMessageRow.id < sub,
                    )
                    .order_by(SessionMessageRow.id.desc())
                )
            if limit is not None:
                stmt = stmt.limit(limit)
            result = await s.execute(stmt)
            rows = result.scalars().all()
            # reverse to chronological order
            return [_row_to_stored_message(r) for r in reversed(rows)]

    # ------------------------------------------------------------------
    # count_messages
    # ------------------------------------------------------------------

    async def count_messages(self, sid: SessionId) -> int:
        key = sid.as_str()
        async with self._db.session() as s:
            result = await s.execute(
                select(func.count()).select_from(SessionMessageRow).where(
                    SessionMessageRow.session_id == key
                )
            )
            return result.scalar_one()

    # ------------------------------------------------------------------
    # count_sessions
    # ------------------------------------------------------------------

    async def count_sessions(self) -> int:
        async with self._db.session() as s:
            result = await s.execute(
                select(func.count()).select_from(SessionRow)
            )
            return result.scalar_one()

    # ------------------------------------------------------------------
    # count_total_messages
    # ------------------------------------------------------------------

    async def count_total_messages(self) -> int:
        async with self._db.session() as s:
            result = await s.execute(
                select(func.count()).select_from(SessionMessageRow)
            )
            return result.scalar_one()

    # ------------------------------------------------------------------
    # trim_to_last
    # ------------------------------------------------------------------

    async def trim_to_last(self, sid: SessionId, *, keep_last: int) -> int:
        key = sid.as_str()
        async with self._db.session() as s:
            # count total first
            result = await s.execute(
                select(func.count()).select_from(SessionMessageRow).where(
                    SessionMessageRow.session_id == key
                )
            )
            total = result.scalar_one()
            if total <= keep_last:
                return 0

            # delete rows whose id is NOT in the latest keep_last ids
            subq = (
                select(SessionMessageRow.id)
                .where(SessionMessageRow.session_id == key)
                .order_by(SessionMessageRow.id.desc())
                .limit(keep_last)
            )
            result = await s.execute(
                sa_delete(SessionMessageRow)
                .where(SessionMessageRow.session_id == key)
                .where(SessionMessageRow.id.not_in(subq))
            )
            removed = result.rowcount  # type: ignore[assignment]
            return removed

    # ------------------------------------------------------------------
    # get_summary / set_summary
    # ------------------------------------------------------------------

    async def get_summary(self, sid: SessionId) -> str:
        key = sid.as_str()
        async with self._db.session() as s:
            result = await s.execute(
                select(SessionRow.summary).where(SessionRow.session_id == key)
            )
            val = result.scalar_one_or_none()
            return val if val is not None else ""

    async def set_summary(self, sid: SessionId, summary: str) -> None:
        key = sid.as_str()
        async with self._db.session() as s:
            await s.execute(
                update(SessionRow)
                .where(SessionRow.session_id == key)
                .values(summary=summary)
            )

    # ------------------------------------------------------------------
    # clear
    # ------------------------------------------------------------------

    async def clear(self, sid: SessionId) -> None:
        key = sid.as_str()
        async with self._db.session() as s:
            await s.execute(
                sa_delete(SessionRow).where(SessionRow.session_id == key)
            )

    # ------------------------------------------------------------------
    # update_meta
    # ------------------------------------------------------------------

    async def update_meta(
        self,
        sid: SessionId,
        *,
        nickname: str | None = None,
        group_id: int | None = None,
        last_active_at: datetime | None = None,
    ) -> None:
        key = sid.as_str()
        values: dict[str, object] = {}
        if nickname is not None:
            values["nickname"] = nickname
        if group_id is not None:
            values["group_id"] = group_id
        if last_active_at is not None:
            values["last_active_at"] = last_active_at.isoformat()
        if not values:
            return

        async with self._db.session() as s:
            await s.execute(
                update(SessionRow)
                .where(SessionRow.session_id == key)
                .values(**values)
            )

    # ------------------------------------------------------------------
    # merge_entity
    # ------------------------------------------------------------------

    async def merge_entity(self, sid: SessionId, name: str, user_id: int) -> None:
        key = sid.as_str()
        # ensure session exists
        await self.ensure(sid)

        async with self._db.session() as s:
            stmt = sqlite_insert(SessionEntityRow).values(
                session_id=key, name=name, user_id=user_id
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["session_id", "name"],
                set_={"user_id": user_id},
            )
            await s.execute(stmt)

    # ------------------------------------------------------------------
    # get_entities
    # ------------------------------------------------------------------

    async def get_entities(self, sid: SessionId) -> dict[str, int]:
        key = sid.as_str()
        async with self._db.session() as s:
            result = await s.execute(
                select(SessionEntityRow.name, SessionEntityRow.user_id).where(
                    SessionEntityRow.session_id == key
                )
            )
            return {name: uid for name, uid in result.all()}

    # ------------------------------------------------------------------
    # list_sessions
    # ------------------------------------------------------------------

    async def list_sessions(
        self, *, limit: int = 50, before_id: str | None = None
    ) -> list[Session]:
        async with self._db.session() as s:
            stmt = select(SessionRow).order_by(SessionRow.session_id).limit(limit)
            if before_id is not None:
                stmt = stmt.where(SessionRow.session_id < before_id)
            result = await s.execute(stmt)
            rows = result.scalars().all()
            return [_row_to_session(r) for r in rows]

    async def list_all_sessions(self) -> list[Session]:
        async with self._db.session() as s:
            result = await s.execute(
                select(SessionRow).order_by(SessionRow.session_id)
            )
            rows = result.scalars().all()
            return [_row_to_session(r) for r in rows]

    async def list_all_messages(self) -> list[StoredMessage]:
        async with self._db.session() as s:
            result = await s.execute(
                select(SessionMessageRow).order_by(SessionMessageRow.id)
            )
            rows = result.scalars().all()
            return [_row_to_stored_message(r) for r in rows]

    async def list_all_entities(self) -> list[tuple[str, str, int]]:
        """Return all session entities as (session_id, name, user_id) tuples."""
        async with self._db.session() as s:
            result = await s.execute(
                select(SessionEntityRow.session_id, SessionEntityRow.name, SessionEntityRow.user_id)
            )
            return list(result.all())


# ---------------------------------------------------------------------------
# Helper: ORM row → Protocol DTO (SocialGraph)
# ---------------------------------------------------------------------------


def _row_to_social_edge(row: SocialEdgeRow) -> SocialEdge:
    """Convert an ORM ``SocialEdgeRow`` to a Protocol ``SocialEdge`` DTO."""
    learned_at: datetime
    if isinstance(row.learned_at, str) and row.learned_at:
        learned_at = datetime.fromisoformat(row.learned_at)
    else:
        learned_at = datetime.now(timezone.utc)

    return SocialEdge(
        from_user_id=row.from_user_id,
        to_user_id=row.to_user_id,
        relation=row.relation,
        label=row.label,
        evidence=row.evidence,
        group_id=row.group_id,
        learned_at=learned_at,
    )


# ---------------------------------------------------------------------------
# SqlSocialGraphRepository  (P2-06)
# ---------------------------------------------------------------------------


class SqlSocialGraphRepository:
    """SQLite-backed implementation of ``SocialGraphRepository`` Protocol.

    Uses ``INSERT … ON CONFLICT DO NOTHING`` on the
    ``(from_user_id, to_user_id, relation, label)`` unique constraint
    to implement four-tuple dedup, matching the MVP ``social_graph.json``
    semantics exactly.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # add_edge
    # ------------------------------------------------------------------

    async def add_edge(self, edge: SocialEdge) -> bool:
        learned_at = edge.learned_at
        if not learned_at or learned_at.year == 1:
            learned_at = datetime.now(timezone.utc)

        async with self._db.session() as s:
            stmt = sqlite_insert(SocialEdgeRow).values(
                from_user_id=edge.from_user_id,
                to_user_id=edge.to_user_id,
                relation=edge.relation,
                label=edge.label,
                evidence=edge.evidence,
                group_id=edge.group_id,
                learned_at=learned_at.isoformat(),
            )
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["from_user_id", "to_user_id", "relation", "label"],
            )
            result = await s.execute(stmt)
            # rowcount == 1 → new row inserted; 0 → conflict, duplicate
            return result.rowcount == 1  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # index_name
    # ------------------------------------------------------------------

    async def index_name(self, name: str, user_id: int) -> None:
        async with self._db.session() as s:
            stmt = sqlite_insert(NameIndexRow).values(
                name=name,
                user_id=user_id,
                updated_at=_now_iso(),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["name"],
                set_={"user_id": user_id, "updated_at": _now_iso()},
            )
            await s.execute(stmt)

    # ------------------------------------------------------------------
    # resolve_name
    # ------------------------------------------------------------------

    async def resolve_name(self, name: str) -> int | None:
        async with self._db.session() as s:
            result = await s.execute(
                select(NameIndexRow.user_id).where(NameIndexRow.name == name)
            )
            val = result.scalar_one_or_none()
            return val  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # edges_from
    # ------------------------------------------------------------------

    async def edges_from(self, user_id: int) -> list[SocialEdge]:
        async with self._db.session() as s:
            result = await s.execute(
                select(SocialEdgeRow)
                .where(SocialEdgeRow.from_user_id == user_id)
                .order_by(SocialEdgeRow.id)
            )
            rows = result.scalars().all()
            return [_row_to_social_edge(r) for r in rows]

    async def all_edges(self) -> list[SocialEdge]:
        async with self._db.session() as s:
            result = await s.execute(
                select(SocialEdgeRow).order_by(SocialEdgeRow.id)
            )
            rows = result.scalars().all()
            return [_row_to_social_edge(r) for r in rows]

    # ------------------------------------------------------------------
    # all_names
    # ------------------------------------------------------------------

    async def all_names(self) -> dict[str, int]:
        async with self._db.session() as s:
            result = await s.execute(
                select(NameIndexRow.name, NameIndexRow.user_id)
            )
            return {name: uid for name, uid in result.all()}

    # ------------------------------------------------------------------
    # count_edges
    # ------------------------------------------------------------------

    async def count_edges(self) -> int:
        async with self._db.session() as s:
            result = await s.execute(
                select(func.count()).select_from(SocialEdgeRow)
            )
            return result.scalar_one()

    # ------------------------------------------------------------------
    # clear
    # ------------------------------------------------------------------

    async def clear(self) -> None:
        async with self._db.session() as s:
            await s.execute(sa_delete(SocialEdgeRow))
            await s.execute(sa_delete(NameIndexRow))


# ===========================================================================
# UserProfileRepository — helpers
# ===========================================================================


def _parse_iso(val: str | None) -> datetime | None:
    """Parse an ISO-8601 string into a datetime, or return None."""
    if not val:
        return None
    return datetime.fromisoformat(val)


def _row_to_user_fact(row: UserFactRow) -> UserFact:
    """Convert an ORM ``UserFactRow`` to a Protocol ``UserFact`` DTO."""
    return UserFact(
        id=row.id,
        content=row.content,
        category=row.category,
        source_user_id=row.source_user_id,
        learned_at=_parse_iso(row.learned_at) or datetime.now(timezone.utc),
        confidence=row.confidence,
        active=row.active,
        supersedes=row.supersedes,
    )


def _row_to_user_profile(row: UserProfileRow) -> UserProfile:
    """Convert an ORM ``UserProfileRow`` to a Protocol ``UserProfile`` DTO.

    Deserialises ``aliases_json`` and ``group_cards_json`` and eagerly
    loaded ``facts`` relationship.
    """
    return UserProfile(
        user_id=row.user_id,
        preferred_name=row.preferred_name,
        aliases=json.loads(row.aliases_json) if row.aliases_json else [],
        group_cards=json.loads(row.group_cards_json) if row.group_cards_json else {},
        stage=row.stage,
        first_met_at=_parse_iso(row.first_met_at),
        last_seen_at=_parse_iso(row.last_seen_at),
        interaction_count=row.interaction_count,
        last_group_id=row.last_group_id,
        seen_in_private=row.seen_in_private,
        seen_in_group=row.seen_in_group,
        impression=row.impression,
        cognition_summary=row.cognition_summary,
        cognition_updated_at=_parse_iso(row.cognition_updated_at),
        cognition_interaction_at_update=row.cognition_interaction_at_update,
        facts=[_row_to_user_fact(f) for f in row.facts],
    )


def _profile_to_values(profile: UserProfile) -> dict[str, object]:
    """Build a values dict from a ``UserProfile`` DTO for INSERT/UPDATE."""
    values: dict[str, object] = {
        "user_id": profile.user_id,
        "preferred_name": profile.preferred_name,
        "aliases_json": json.dumps(profile.aliases, ensure_ascii=False),
        "group_cards_json": json.dumps(profile.group_cards, ensure_ascii=False),
        "stage": profile.stage,
        "interaction_count": profile.interaction_count,
        "last_group_id": profile.last_group_id,
        "seen_in_private": profile.seen_in_private,
        "seen_in_group": profile.seen_in_group,
        "impression": profile.impression,
        "cognition_summary": profile.cognition_summary,
        "cognition_interaction_at_update": profile.cognition_interaction_at_update,
    }
    # Nullable datetime fields — store as ISO string or None
    if profile.first_met_at is not None:
        values["first_met_at"] = profile.first_met_at.isoformat()
    else:
        values["first_met_at"] = None
    if profile.last_seen_at is not None:
        values["last_seen_at"] = profile.last_seen_at.isoformat()
    else:
        values["last_seen_at"] = None
    if profile.cognition_updated_at is not None:
        values["cognition_updated_at"] = profile.cognition_updated_at.isoformat()
    else:
        values["cognition_updated_at"] = None
    return values


class SqlUserProfileRepository:
    """SQLite-backed implementation of ``UserProfileRepository`` Protocol.

    Injected with a ``Database`` instance; each method opens its own
    ``db.session()`` context so that operations are transactional.
    """

    def __init__(self, db: Database, *, max_active_facts: int = 30) -> None:
        self._db = db
        self._max_active_facts = max_active_facts

    # ------------------------------------------------------------------
    # get
    # ------------------------------------------------------------------

    async def get(self, user_id: int) -> UserProfile | None:
        async with self._db.session() as s:
            result = await s.execute(
                select(UserProfileRow)
                .where(UserProfileRow.user_id == user_id)
                .options(selectinload(UserProfileRow.facts))
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return _row_to_user_profile(row)

    # ------------------------------------------------------------------
    # upsert
    # ------------------------------------------------------------------

    async def upsert(self, profile: UserProfile) -> None:
        values = _profile_to_values(profile)
        async with self._db.session() as s:
            stmt = sqlite_insert(UserProfileRow).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id"],
                set_={k: stmt.excluded[k] for k in values if k != "user_id"},
            )
            await s.execute(stmt)

    # ------------------------------------------------------------------
    # add_fact
    # ------------------------------------------------------------------

    async def add_fact(self, user_id: int, fact: UserFact) -> None:
        # Ensure profile exists (auto-create like InMemory)
        async with self._db.session() as s:
            result = await s.execute(
                select(UserProfileRow).where(UserProfileRow.user_id == user_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = UserProfileRow(user_id=user_id)
                s.add(row)
                await s.flush()

        # Skip if an active fact with same content already exists
        async with self._db.session() as s:
            result = await s.execute(
                select(UserFactRow).where(
                    UserFactRow.user_id == user_id,
                    UserFactRow.active.is_(True),
                    UserFactRow.content == fact.content,
                )
            )
            if result.scalar_one_or_none() is not None:
                return

        # Insert the fact (idempotent: if id exists, update)
        learned_at = fact.learned_at
        if not learned_at or learned_at.year == 1:
            learned_at = datetime.now(timezone.utc)

        async with self._db.session() as s:
            stmt = sqlite_insert(UserFactRow).values(
                id=fact.id,
                user_id=user_id,
                content=fact.content,
                category=fact.category,
                source_user_id=fact.source_user_id,
                learned_at=learned_at.isoformat(),
                confidence=fact.confidence,
                active=fact.active,
                supersedes=fact.supersedes,
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
            await s.execute(stmt)

        # Soft-delete overflow: deactivate oldest active facts exceeding limit
        async with self._db.session() as s:
            result = await s.execute(
                select(func.count()).select_from(UserFactRow).where(
                    UserFactRow.user_id == user_id,
                    UserFactRow.active.is_(True),
                )
            )
            active_count = result.scalar_one()
            if active_count > self._max_active_facts:
                excess = active_count - self._max_active_facts
                # Find the oldest active fact IDs by learned_at
                result = await s.execute(
                    select(UserFactRow.id).where(
                        UserFactRow.user_id == user_id,
                        UserFactRow.active.is_(True),
                    ).order_by(UserFactRow.learned_at.asc()).limit(excess)
                )
                oldest_ids = [row_id for (row_id,) in result.all()]
                if oldest_ids:
                    await s.execute(
                        update(UserFactRow)
                        .where(UserFactRow.id.in_(oldest_ids))
                        .values(active=False)
                    )

    # ------------------------------------------------------------------
    # list_active_facts
    # ------------------------------------------------------------------

    async def list_active_facts(
        self, user_id: int, *, limit: int | None = None
    ) -> list[UserFact]:
        async with self._db.session() as s:
            stmt = (
                select(UserFactRow)
                .where(UserFactRow.user_id == user_id, UserFactRow.active.is_(True))
                .order_by(UserFactRow.learned_at.desc())
            )
            if limit is not None:
                stmt = stmt.limit(limit)
            result = await s.execute(stmt)
            rows = result.scalars().all()
            # Reverse to ascending learned_at order (oldest first) to match
            # InMemory which returns facts in append order.
            return [_row_to_user_fact(r) for r in reversed(rows)]

    # ------------------------------------------------------------------
    # deactivate_facts
    # ------------------------------------------------------------------

    async def deactivate_facts(self, user_id: int, fact_ids: list[str]) -> None:
        if not fact_ids:
            return
        async with self._db.session() as s:
            await s.execute(
                update(UserFactRow)
                .where(
                    UserFactRow.user_id == user_id,
                    UserFactRow.id.in_(fact_ids),
                )
                .values(active=False)
            )

    # ------------------------------------------------------------------
    # list_user_ids
    # ------------------------------------------------------------------

    async def list_user_ids(self) -> list[int]:
        async with self._db.session() as s:
            result = await s.execute(
                select(UserProfileRow.user_id)
            )
            return [uid for (uid,) in result.all()]

    async def list_profiles(
        self, *, limit: int = 50, before_user_id: int | None = None
    ) -> list[UserProfile]:
        async with self._db.session() as s:
            stmt = (
                select(UserProfileRow)
                .options(selectinload(UserProfileRow.facts))
                .order_by(UserProfileRow.user_id)
                .limit(limit)
            )
            if before_user_id is not None:
                stmt = stmt.where(UserProfileRow.user_id < before_user_id)
            result = await s.execute(stmt)
            rows = result.scalars().all()
            return [_row_to_user_profile(r) for r in rows]

    async def list_all_profiles(self) -> list[UserProfile]:
        async with self._db.session() as s:
            result = await s.execute(
                select(UserProfileRow)
                .options(selectinload(UserProfileRow.facts))
                .order_by(UserProfileRow.user_id)
            )
            rows = result.scalars().all()
            return [_row_to_user_profile(r) for r in rows]

    async def list_all_facts(self) -> list[UserFact]:
        async with self._db.session() as s:
            result = await s.execute(
                select(UserFactRow).order_by(UserFactRow.user_id, UserFactRow.learned_at)
            )
            rows = result.scalars().all()
            return [_row_to_user_fact(r) for r in rows]

    # ------------------------------------------------------------------
    # count_users
    # ------------------------------------------------------------------

    async def count_users(self) -> int:
        async with self._db.session() as s:
            result = await s.execute(
                select(func.count()).select_from(UserProfileRow)
            )
            return result.scalar_one()

    # ------------------------------------------------------------------
    # count_active_facts
    # ------------------------------------------------------------------

    async def count_active_facts(self) -> int:
        async with self._db.session() as s:
            result = await s.execute(
                select(func.count()).select_from(UserFactRow).where(
                    UserFactRow.active.is_(True)
                )
            )
            return result.scalar_one()

    # ------------------------------------------------------------------
    # delete
    # ------------------------------------------------------------------

    async def delete(self, user_id: int) -> bool:
        async with self._db.session() as s:
            result = await s.execute(
                sa_delete(UserProfileRow).where(
                    UserProfileRow.user_id == user_id
                )
            )
            return result.rowcount > 0  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # delete_all
    # ------------------------------------------------------------------

    async def delete_all(self) -> int:
        async with self._db.session() as s:
            result = await s.execute(
                sa_delete(UserProfileRow)
            )
            return result.rowcount  # type: ignore[return-value]


# ===========================================================================
# SqlConfigRepository  (P2-07)
# ===========================================================================


class SqlConfigRepository:
    """SQLite-backed implementation of ``ConfigRepository`` Protocol.

    Values are JSON-serialised into ``value_json``; ``is_secret`` and
    ``group_name`` are backfilled from ``settings_defaults`` when available.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_all(self) -> dict[str, object]:
        async with self._db.session() as s:
            result = await s.execute(
                select(SettingRow.key, SettingRow.value_json)
            )
            return {key: json.loads(vjson) for key, vjson in result.all()}

    async def set(self, key: str, value: object) -> None:
        # Backfill is_secret / group_name from settings_defaults
        group_name: str | None = None
        is_secret = False
        try:
            from lingxuan.settings_defaults import SETTINGS_BY_KEY

            spec = SETTINGS_BY_KEY.get(key)
            if spec is not None:
                group_name = spec.group
                is_secret = spec.is_secret
        except ImportError:
            pass

        async with self._db.session() as s:
            stmt = sqlite_insert(SettingRow).values(
                key=key,
                value_json=json.dumps(value, ensure_ascii=False),
                group_name=group_name,
                is_secret=is_secret,
                updated_at=_now_iso(),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["key"],
                set_={
                    "value_json": stmt.excluded.value_json,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            await s.execute(stmt)

    async def bulk_set(self, items: dict[str, object]) -> None:
        if not items:
            return
        async with self._db.session() as s:
            for key, value in items.items():
                group_name: str | None = None
                is_secret = False
                try:
                    from lingxuan.settings_defaults import SETTINGS_BY_KEY

                    spec = SETTINGS_BY_KEY.get(key)
                    if spec is not None:
                        group_name = spec.group
                        is_secret = spec.is_secret
                except ImportError:
                    pass

                stmt = sqlite_insert(SettingRow).values(
                    key=key,
                    value_json=json.dumps(value, ensure_ascii=False),
                    group_name=group_name,
                    is_secret=is_secret,
                    updated_at=_now_iso(),
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["key"],
                    set_={
                        "value_json": stmt.excluded.value_json,
                        "updated_at": stmt.excluded.updated_at,
                    },
                )
                await s.execute(stmt)


# ===========================================================================
# SqlAuditRepository  (P2-07)
# ===========================================================================


def _row_to_audit_entry(row: AuditLogRow) -> AuditEntry:
    """Convert an ORM ``AuditLogRow`` to a Protocol ``AuditEntry`` DTO."""
    created_at: datetime
    if isinstance(row.created_at, str) and row.created_at:
        created_at = datetime.fromisoformat(row.created_at)
    else:
        created_at = datetime.now(timezone.utc)

    return AuditEntry(
        id=row.id,
        actor=row.actor or "",
        action=row.action or "",
        target=row.target or "",
        detail=json.loads(row.detail_json) if row.detail_json else {},
        ip=row.ip or "",
        success=row.success,
        created_at=created_at,
    )


class SqlAuditRepository:
    """SQLite-backed implementation of ``AuditRepository`` Protocol.

    ``query`` uses keyset pagination (``before_id``) for efficient
    descending traversal.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(
        self,
        *,
        actor: str,
        action: str,
        target: str = "",
        detail: dict | None = None,
        ip: str = "",
        success: bool = True,
    ) -> None:
        async with self._db.session() as s:
            row = AuditLogRow(
                actor=actor,
                action=action,
                target=target,
                detail_json=json.dumps(detail, ensure_ascii=False) if detail else None,
                ip=ip,
                success=success,
                created_at=_now_iso(),
            )
            s.add(row)

    async def query(
        self,
        *,
        actor: str | None = None,
        action: str | None = None,
        limit: int = 100,
        before_id: int | None = None,
    ) -> list[AuditEntry]:
        async with self._db.session() as s:
            stmt = select(AuditLogRow).order_by(AuditLogRow.id.desc())
            if actor is not None:
                stmt = stmt.where(AuditLogRow.actor == actor)
            if action is not None:
                stmt = stmt.where(AuditLogRow.action == action)
            if before_id is not None:
                stmt = stmt.where(AuditLogRow.id < before_id)
            stmt = stmt.limit(limit)
            result = await s.execute(stmt)
            rows = result.scalars().all()
            return [_row_to_audit_entry(r) for r in rows]


# ===========================================================================
# SqlPluginConfigRepository  (P2-07)
# ===========================================================================


class SqlPluginConfigRepository:
    """SQLite-backed implementation of ``PluginConfigRepository`` Protocol."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, name: str) -> tuple[bool, dict] | None:
        async with self._db.session() as s:
            result = await s.execute(
                select(PluginConfigRow).where(PluginConfigRow.name == name)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return (row.enabled, json.loads(row.config_json))

    async def upsert(self, name: str, *, enabled: bool, config: dict) -> None:
        async with self._db.session() as s:
            stmt = sqlite_insert(PluginConfigRow).values(
                name=name,
                enabled=enabled,
                config_json=json.dumps(config, ensure_ascii=False),
                updated_at=_now_iso(),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["name"],
                set_={
                    "enabled": stmt.excluded.enabled,
                    "config_json": stmt.excluded.config_json,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            await s.execute(stmt)

    async def all(self) -> dict[str, tuple[bool, dict]]:
        async with self._db.session() as s:
            result = await s.execute(
                select(PluginConfigRow)
            )
            rows = result.scalars().all()
            return {
                row.name: (row.enabled, json.loads(row.config_json))
                for row in rows
            }


# ===========================================================================
# SqlAdminUserRepository  (P2-07)
# ===========================================================================


def _row_to_admin_user(row: AdminUserRow) -> AdminUserDTO:
    """Convert an ORM ``AdminUserRow`` to a Protocol ``AdminUserRow`` DTO."""
    created_at: datetime
    if isinstance(row.created_at, str) and row.created_at:
        created_at = datetime.fromisoformat(row.created_at)
    else:
        created_at = datetime.now(timezone.utc)

    last_login_at: datetime | None = None
    if isinstance(row.last_login_at, str) and row.last_login_at:
        last_login_at = datetime.fromisoformat(row.last_login_at)

    return AdminUserDTO(
        id=row.id,
        username=row.username,
        password_hash=row.password_hash,
        role=row.role,
        must_change_password=row.must_change_password,
        created_at=created_at,
        last_login_at=last_login_at,
    )


class SqlAdminUserRepository:
    """SQLite-backed implementation of ``AdminUserRepository`` Protocol."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_by_username(self, username: str) -> AdminUserDTO | None:
        async with self._db.session() as s:
            result = await s.execute(
                select(AdminUserRow).where(AdminUserRow.username == username)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return _row_to_admin_user(row)

    async def create(
        self,
        *,
        username: str,
        password_hash: str,
        role: str,
        must_change_password: bool = True,
    ) -> None:
        async with self._db.session() as s:
            row = AdminUserRow(
                username=username,
                password_hash=password_hash,
                role=role,
                must_change_password=must_change_password,
                created_at=_now_iso(),
            )
            s.add(row)

    async def set_password(
        self,
        username: str,
        password_hash: str,
        *,
        must_change_password: bool = False,
    ) -> None:
        async with self._db.session() as s:
            await s.execute(
                update(AdminUserRow)
                .where(AdminUserRow.username == username)
                .values(
                    password_hash=password_hash,
                    must_change_password=must_change_password,
                )
            )

    async def touch_login(self, username: str) -> None:
        async with self._db.session() as s:
            await s.execute(
                update(AdminUserRow)
                .where(AdminUserRow.username == username)
                .values(last_login_at=_now_iso())
            )

    async def count(self) -> int:
        async with self._db.session() as s:
            result = await s.execute(
                select(func.count()).select_from(AdminUserRow)
            )
            return result.scalar_one()
