"""SQLAlchemy 2.0 ORM mappings for all application tables.

Defines ``Base`` (DeclarativeBase) and 11 model classes that align with
``docs/architecture-v2.md`` section 8.2.  Times are stored as ISO-8601 UTC
text (String) to match the existing JSON format; booleans use Integer 0/1.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Single declarative base shared by all ORM models."""


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------


class Session(Base):
    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str] = mapped_column(String, nullable=False, default="")
    nickname: Mapped[str] = mapped_column(String, nullable=False, default="")
    last_active_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    # Optional: rare meta keys not yet promoted to named columns
    meta_json: Mapped[str | None] = mapped_column(String, nullable=True)

    messages: Mapped[list[SessionMessage]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    entities: Mapped[list[SessionEntity]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


# ---------------------------------------------------------------------------
# session_messages
# ---------------------------------------------------------------------------


class SessionMessage(Base):
    __tablename__ = "session_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_session_messages_session_id_seq"),
        Index("ix_session_messages_session_id_id", "session_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    session: Mapped[Session] = relationship(back_populates="messages")


# ---------------------------------------------------------------------------
# session_entities
# ---------------------------------------------------------------------------


class SessionEntity(Base):
    __tablename__ = "session_entities"
    __table_args__ = (
        PrimaryKeyConstraint("session_id", "name", name="pk_session_entities"),
    )

    session_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)

    session: Mapped[Session] = relationship(back_populates="entities")


# ---------------------------------------------------------------------------
# user_profiles
# ---------------------------------------------------------------------------


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    preferred_name: Mapped[str] = mapped_column(String, nullable=False, default="")
    aliases_json: Mapped[str] = mapped_column(String, nullable=False, default="[]")
    group_cards_json: Mapped[str] = mapped_column(String, nullable=False, default="{}")
    stage: Mapped[str] = mapped_column(String, nullable=False, default="stranger")
    first_met_at: Mapped[str | None] = mapped_column(String, nullable=True)
    last_seen_at: Mapped[str | None] = mapped_column(String, nullable=True)
    interaction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seen_in_private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    seen_in_group: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    impression: Mapped[str] = mapped_column(String, nullable=False, default="")
    cognition_summary: Mapped[str] = mapped_column(String, nullable=False, default="")
    cognition_updated_at: Mapped[str | None] = mapped_column(String, nullable=True)
    cognition_interaction_at_update: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=2)

    facts: Mapped[list[UserFact]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


# ---------------------------------------------------------------------------
# user_facts
# ---------------------------------------------------------------------------


class UserFact(Base):
    __tablename__ = "user_facts"
    __table_args__ = (
        Index("ix_user_facts_user_id_active_learned_at", "user_id", "active", "learned_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("user_profiles.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False, default="general")
    source_user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    learned_at: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    supersedes: Mapped[str | None] = mapped_column(String, nullable=True)

    profile: Mapped[UserProfile] = relationship(back_populates="facts")


# ---------------------------------------------------------------------------
# social_edges
# ---------------------------------------------------------------------------


class SocialEdge(Base):
    __tablename__ = "social_edges"
    __table_args__ = (
        UniqueConstraint(
            "from_user_id", "to_user_id", "relation", "label",
            name="uq_social_edges_from_to_relation_label",
        ),
        Index("ix_social_edges_from_user_id", "from_user_id"),
        Index("ix_social_edges_to_user_id", "to_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    to_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    relation: Mapped[str] = mapped_column(String, nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=False, default="")
    evidence: Mapped[str] = mapped_column(String, nullable=False, default="")
    group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    learned_at: Mapped[str] = mapped_column(String, nullable=False)


# ---------------------------------------------------------------------------
# name_index
# ---------------------------------------------------------------------------


class NameIndex(Base):
    __tablename__ = "name_index"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[str | None] = mapped_column(String, nullable=True)


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value_json: Mapped[str] = mapped_column(String, nullable=False)
    group_name: Mapped[str | None] = mapped_column(String, nullable=True)
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String, nullable=True)


# ---------------------------------------------------------------------------
# admin_users
# ---------------------------------------------------------------------------


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, default="admin")
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
    )
    created_at: Mapped[str | None] = mapped_column(String, nullable=True)
    last_login_at: Mapped[str | None] = mapped_column(String, nullable=True)


# ---------------------------------------------------------------------------
# audit_logs
# ---------------------------------------------------------------------------


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_actor", "actor"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str | None] = mapped_column(String, nullable=True)
    target: Mapped[str | None] = mapped_column(String, nullable=True)
    detail_json: Mapped[str | None] = mapped_column(String, nullable=True)
    ip: Mapped[str | None] = mapped_column(String, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[str | None] = mapped_column(String, nullable=True)


# ---------------------------------------------------------------------------
# plugin_configs
# ---------------------------------------------------------------------------


class PluginConfig(Base):
    __tablename__ = "plugin_configs"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config_json: Mapped[str] = mapped_column(String, nullable=False, default="{}")
    updated_at: Mapped[str | None] = mapped_column(String, nullable=True)
