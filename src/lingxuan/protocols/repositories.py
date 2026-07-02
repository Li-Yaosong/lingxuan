"""Repository interfaces and data classes (DTOs) for storage access."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from lingxuan.protocols.messaging import SessionId


# ---------------------------------------------------------------------------
# Data classes (DTOs)
# ---------------------------------------------------------------------------

@dataclass
class StoredMessage:
    role: str
    content: str
    user_id: int | None = None
    seq: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Session:
    session_id: SessionId
    kind: str
    group_id: int | None = None
    summary: str = ""
    nickname: str = ""
    last_active_at: datetime | None = None


@dataclass
class UserFact:
    id: str
    content: str
    category: str = "general"
    source_user_id: int = 0
    learned_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    confidence: float = 1.0
    active: bool = True
    supersedes: str | None = None


@dataclass
class UserProfile:
    user_id: int
    preferred_name: str = ""
    aliases: list[str] = field(default_factory=list)
    group_cards: dict[str, str] = field(default_factory=dict)
    stage: str = "stranger"
    first_met_at: datetime | None = None
    last_seen_at: datetime | None = None
    interaction_count: int = 0
    last_group_id: int | None = None
    seen_in_private: bool = False
    seen_in_group: bool = False
    impression: str = ""
    cognition_summary: str = ""
    cognition_updated_at: datetime | None = None
    cognition_interaction_at_update: int = 0
    facts: list[UserFact] = field(default_factory=list)


@dataclass
class SocialEdge:
    from_user_id: int
    to_user_id: int
    relation: str
    label: str = ""
    evidence: str = ""
    group_id: int | None = None
    learned_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class AuditEntry:
    id: int
    actor: str
    action: str
    target: str
    detail: dict
    ip: str
    success: bool
    created_at: datetime


@dataclass
class AdminUserRow:
    id: int
    username: str
    password_hash: str
    role: str
    must_change_password: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_login_at: datetime | None = None


# ---------------------------------------------------------------------------
# Repository protocols
# ---------------------------------------------------------------------------

class SessionRepository(Protocol):
    async def get(self, sid: SessionId) -> Session | None: ...
    async def ensure(self, sid: SessionId, *, group_id: int | None = None, nickname: str = "") -> Session: ...
    async def append_message(self, sid: SessionId, msg: StoredMessage) -> None: ...
    async def load_history(self, sid: SessionId, *, limit: int | None = None) -> list[StoredMessage]: ...
    async def count_messages(self, sid: SessionId) -> int: ...
    async def trim_to_last(self, sid: SessionId, *, keep_last: int) -> int: ...
    async def get_summary(self, sid: SessionId) -> str: ...
    async def set_summary(self, sid: SessionId, summary: str) -> None: ...
    async def clear(self, sid: SessionId) -> None: ...
    async def update_meta(self, sid: SessionId, *, nickname: str | None = None, group_id: int | None = None, last_active_at: datetime | None = None) -> None: ...
    async def merge_entity(self, sid: SessionId, name: str, user_id: int) -> None: ...
    async def get_entities(self, sid: SessionId) -> dict[str, int]: ...
    async def list_sessions(self, *, limit: int = 50, before_id: int | None = None) -> list[Session]: ...


class UserProfileRepository(Protocol):
    async def get(self, user_id: int) -> UserProfile | None: ...
    async def upsert(self, profile: UserProfile) -> None: ...
    async def add_fact(self, user_id: int, fact: UserFact) -> None: ...
    async def list_active_facts(self, user_id: int, *, limit: int | None = None) -> list[UserFact]: ...
    async def deactivate_facts(self, user_id: int, fact_ids: list[str]) -> None: ...
    async def list_user_ids(self) -> list[int]: ...
    async def delete(self, user_id: int) -> bool: ...
    async def delete_all(self) -> int: ...


class SocialGraphRepository(Protocol):
    async def add_edge(self, edge: SocialEdge) -> bool: ...
    async def index_name(self, name: str, user_id: int) -> None: ...
    async def resolve_name(self, name: str) -> int | None: ...
    async def edges_from(self, user_id: int) -> list[SocialEdge]: ...
    async def all_names(self) -> dict[str, int]: ...
    async def clear(self) -> None: ...


class ConfigRepository(Protocol):
    async def get_all(self) -> dict[str, object]: ...
    async def set(self, key: str, value: object) -> None: ...
    async def bulk_set(self, items: dict[str, object]) -> None: ...


class AuditRepository(Protocol):
    async def record(self, *, actor: str, action: str, target: str = "", detail: dict | None = None, ip: str = "", success: bool = True) -> None: ...
    async def query(self, *, actor: str | None = None, action: str | None = None, limit: int = 100, before_id: int | None = None) -> list[AuditEntry]: ...


class PluginConfigRepository(Protocol):
    async def get(self, name: str) -> tuple[bool, dict] | None: ...
    async def upsert(self, name: str, *, enabled: bool, config: dict) -> None: ...
    async def all(self) -> dict[str, tuple[bool, dict]]: ...


class AdminUserRepository(Protocol):
    async def get_by_username(self, username: str) -> AdminUserRow | None: ...
    async def create(self, *, username: str, password_hash: str, role: str, must_change_password: bool = True) -> None: ...
    async def set_password(self, username: str, password_hash: str, *, must_change_password: bool = False) -> None: ...
    async def touch_login(self, username: str) -> None: ...
    async def count(self) -> int: ...
