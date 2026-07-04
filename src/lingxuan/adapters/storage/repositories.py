"""SQLite-backed repository implementations using SQLAlchemy 2.0 async ORM.

This module provides ``SqlSessionRepository`` (P2-04). Additional repositories
(UserProfile, SocialGraph, Config, Audit, PluginConfig, AdminUser) will be
added by P2-05/06/07.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from lingxuan.adapters.storage.db import Database
from lingxuan.adapters.storage.orm import (
    Session as SessionRow,
    SessionEntity as SessionEntityRow,
    SessionMessage as SessionMessageRow,
)
from lingxuan.protocols.messaging import SessionId
from lingxuan.protocols.repositories import Session, StoredMessage


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
        self, sid: SessionId, *, limit: int | None = None
    ) -> list[StoredMessage]:
        key = sid.as_str()
        async with self._db.session() as s:
            stmt = (
                select(SessionMessageRow)
                .where(SessionMessageRow.session_id == key)
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
                delete(SessionMessageRow)
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
                delete(SessionRow).where(SessionRow.session_id == key)
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
        self, *, limit: int = 50, before_id: int | None = None
    ) -> list[Session]:
        async with self._db.session() as s:
            stmt = select(SessionRow).order_by(SessionRow.session_id).limit(limit)
            result = await s.execute(stmt)
            rows = result.scalars().all()
            return [_row_to_session(r) for r in rows]
