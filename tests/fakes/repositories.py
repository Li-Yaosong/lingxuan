"""In-memory repository fakes: dict/list-backed, reproduce business semantics."""

from __future__ import annotations

from datetime import datetime, timezone

from lingxuan.protocols.messaging import SessionId
from lingxuan.protocols.repositories import (
    AdminUserRow,
    AuditEntry,
    Session,
    SocialEdge,
    StoredMessage,
    UserFact,
    UserProfile,
)


class InMemorySessionRepository:
    """Implements SessionRepository with dict storage."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._messages: dict[str, list[StoredMessage]] = {}
        self._seq: dict[str, int] = {}
        self._entities: dict[str, dict[str, int]] = {}

    async def get(self, sid: SessionId) -> Session | None:
        return self._sessions.get(sid.as_str())

    async def ensure(
        self,
        sid: SessionId,
        *,
        group_id: int | None = None,
        nickname: str = "",
    ) -> Session:
        key = sid.as_str()
        if key not in self._sessions:
            self._sessions[key] = Session(
                session_id=sid,
                kind=sid.kind,
                group_id=group_id,
                nickname=nickname,
            )
            self._messages.setdefault(key, [])
            self._seq.setdefault(key, 0)
            self._entities.setdefault(key, {})
        return self._sessions[key]

    async def append_message(self, sid: SessionId, msg: StoredMessage) -> None:
        key = sid.as_str()
        await self.ensure(sid)
        seq = self._seq[key]
        msg.seq = seq
        if not msg.created_at or msg.created_at.year == 1:
            msg.created_at = datetime.now(timezone.utc)
        self._messages[key].append(msg)
        self._seq[key] = seq + 1

    async def load_history(
        self, sid: SessionId, *, limit: int | None = None
    ) -> list[StoredMessage]:
        key = sid.as_str()
        msgs = self._messages.get(key, [])
        if limit is not None:
            return msgs[-limit:]
        return list(msgs)

    async def count_messages(self, sid: SessionId) -> int:
        return len(self._messages.get(sid.as_str(), []))

    async def count_sessions(self) -> int:
        return len(self._sessions)

    async def count_total_messages(self) -> int:
        return sum(len(msgs) for msgs in self._messages.values())

    async def trim_to_last(self, sid: SessionId, *, keep_last: int) -> int:
        key = sid.as_str()
        msgs = self._messages.get(key, [])
        if len(msgs) <= keep_last:
            return 0
        removed = len(msgs) - keep_last
        self._messages[key] = msgs[-keep_last:]
        return removed

    async def get_summary(self, sid: SessionId) -> str:
        s = self._sessions.get(sid.as_str())
        return s.summary if s else ""

    async def set_summary(self, sid: SessionId, summary: str) -> None:
        s = self._sessions.get(sid.as_str())
        if s:
            s.summary = summary

    async def clear(self, sid: SessionId) -> None:
        key = sid.as_str()
        self._sessions.pop(key, None)
        self._messages.pop(key, None)
        self._seq.pop(key, None)
        self._entities.pop(key, None)

    async def update_meta(
        self,
        sid: SessionId,
        *,
        nickname: str | None = None,
        group_id: int | None = None,
        last_active_at: datetime | None = None,
    ) -> None:
        s = self._sessions.get(sid.as_str())
        if not s:
            return
        if nickname is not None:
            s.nickname = nickname
        if group_id is not None:
            s.group_id = group_id
        if last_active_at is not None:
            s.last_active_at = last_active_at

    async def merge_entity(self, sid: SessionId, name: str, user_id: int) -> None:
        await self.ensure(sid)
        self._entities[sid.as_str()][name] = user_id

    async def get_entities(self, sid: SessionId) -> dict[str, int]:
        return dict(self._entities.get(sid.as_str(), {}))

    async def list_sessions(
        self, *, limit: int = 50, before_id: int | None = None
    ) -> list[Session]:
        return list(self._sessions.values())[:limit]


class InMemoryUserProfileRepository:
    """Implements UserProfileRepository with dict storage and fact soft-delete semantics."""

    def __init__(self, max_active_facts: int = 30) -> None:
        self._profiles: dict[int, UserProfile] = {}
        self._max_active_facts = max_active_facts

    async def get(self, user_id: int) -> UserProfile | None:
        return self._profiles.get(user_id)

    async def upsert(self, profile: UserProfile) -> None:
        self._profiles[profile.user_id] = profile

    async def add_fact(self, user_id: int, fact: UserFact) -> None:
        profile = self._profiles.get(user_id)
        if profile is None:
            profile = UserProfile(user_id=user_id)
            self._profiles[user_id] = profile

        # Skip if active fact with same content already exists
        for f in profile.facts:
            if f.active and f.content == fact.content:
                return

        profile.facts.append(fact)

        # Soft-delete oldest active facts when exceeding limit
        active_facts = [f for f in profile.facts if f.active]
        if len(active_facts) > self._max_active_facts:
            active_facts.sort(key=lambda f: f.learned_at)
            to_deactivate = len(active_facts) - self._max_active_facts
            deactivated = 0
            for f in profile.facts:
                if f.active and deactivated < to_deactivate:
                    f.active = False
                    deactivated += 1

    async def list_active_facts(
        self, user_id: int, *, limit: int | None = None
    ) -> list[UserFact]:
        profile = self._profiles.get(user_id)
        if profile is None:
            return []
        active = [f for f in profile.facts if f.active]
        if limit is not None:
            return active[-limit:]
        return active

    async def deactivate_facts(self, user_id: int, fact_ids: list[str]) -> None:
        profile = self._profiles.get(user_id)
        if profile is None:
            return
        ids_set = set(fact_ids)
        for f in profile.facts:
            if f.id in ids_set:
                f.active = False

    async def list_user_ids(self) -> list[int]:
        return list(self._profiles.keys())

    async def count_users(self) -> int:
        return len(self._profiles)

    async def count_active_facts(self) -> int:
        return sum(len([f for f in p.facts if f.active]) for p in self._profiles.values())

    async def delete(self, user_id: int) -> bool:
        return self._profiles.pop(user_id, None) is not None

    async def delete_all(self) -> int:
        count = len(self._profiles)
        self._profiles.clear()
        return count


class InMemorySocialGraphRepository:
    """Implements SocialGraphRepository with list/dict storage and edge dedup."""

    def __init__(self) -> None:
        self._edges: list[SocialEdge] = []
        self._name_index: dict[str, int] = {}

    async def add_edge(self, edge: SocialEdge) -> bool:
        # Dedup by (from_user_id, to_user_id, relation, label) four-tuple
        for existing in self._edges:
            if (
                existing.from_user_id == edge.from_user_id
                and existing.to_user_id == edge.to_user_id
                and existing.relation == edge.relation
                and existing.label == edge.label
            ):
                return False
        self._edges.append(edge)
        return True

    async def index_name(self, name: str, user_id: int) -> None:
        self._name_index[name] = user_id

    async def resolve_name(self, name: str) -> int | None:
        return self._name_index.get(name)

    async def edges_from(self, user_id: int) -> list[SocialEdge]:
        return [e for e in self._edges if e.from_user_id == user_id]

    async def all_names(self) -> dict[str, int]:
        return dict(self._name_index)

    async def count_edges(self) -> int:
        return len(self._edges)

    async def clear(self) -> None:
        self._edges.clear()
        self._name_index.clear()


class InMemoryConfigRepository:
    """Implements ConfigRepository with dict storage."""

    def __init__(self, data: dict[str, object] | None = None) -> None:
        self._data: dict[str, object] = dict(data) if data else {}

    async def get_all(self) -> dict[str, object]:
        return dict(self._data)

    async def set(self, key: str, value: object) -> None:
        self._data[key] = value

    async def bulk_set(self, items: dict[str, object]) -> None:
        self._data.update(items)


class InMemoryAuditRepository:
    """Implements AuditRepository with list storage."""

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []
        self._next_id = 1

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
        entry = AuditEntry(
            id=self._next_id,
            actor=actor,
            action=action,
            target=target,
            detail=detail or {},
            ip=ip,
            success=success,
            created_at=datetime.now(timezone.utc),
        )
        self._entries.append(entry)
        self._next_id += 1

    async def query(
        self,
        *,
        actor: str | None = None,
        action: str | None = None,
        limit: int = 100,
        before_id: int | None = None,
    ) -> list[AuditEntry]:
        results = self._entries
        if actor is not None:
            results = [e for e in results if e.actor == actor]
        if action is not None:
            results = [e for e in results if e.action == action]
        if before_id is not None:
            results = [e for e in results if e.id < before_id]
        return results[-limit:]


class InMemoryPluginConfigRepository:
    """Implements PluginConfigRepository with dict storage."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[bool, dict]] = {}

    async def get(self, name: str) -> tuple[bool, dict] | None:
        return self._data.get(name)

    async def upsert(self, name: str, *, enabled: bool, config: dict) -> None:
        self._data[name] = (enabled, config)

    async def all(self) -> dict[str, tuple[bool, dict]]:
        return dict(self._data)


class InMemoryAdminUserRepository:
    """Implements AdminUserRepository with dict storage."""

    def __init__(self) -> None:
        self._users: dict[str, AdminUserRow] = {}
        self._next_id = 1

    async def get_by_username(self, username: str) -> AdminUserRow | None:
        return self._users.get(username)

    async def create(
        self,
        *,
        username: str,
        password_hash: str,
        role: str,
        must_change_password: bool = True,
    ) -> None:
        row = AdminUserRow(
            id=self._next_id,
            username=username,
            password_hash=password_hash,
            role=role,
            must_change_password=must_change_password,
        )
        self._users[username] = row
        self._next_id += 1

    async def set_password(
        self,
        username: str,
        password_hash: str,
        *,
        must_change_password: bool = False,
    ) -> None:
        row = self._users.get(username)
        if row:
            row.password_hash = password_hash
            row.must_change_password = must_change_password

    async def touch_login(self, username: str) -> None:
        row = self._users.get(username)
        if row:
            row.last_login_at = datetime.now(timezone.utc)

    async def count(self) -> int:
        return len(self._users)
