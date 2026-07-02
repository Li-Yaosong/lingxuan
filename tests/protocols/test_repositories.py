"""Tests for lingxuan.protocols.repositories — dataclass default construction."""

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


class TestStoredMessageDefaults:
    def test_minimal(self) -> None:
        msg = StoredMessage(role="user", content="hi")
        assert msg.role == "user"
        assert msg.content == "hi"
        assert msg.user_id is None
        assert msg.seq == 0
        assert isinstance(msg.created_at, datetime)


class TestSessionDefaults:
    def test_minimal(self) -> None:
        sid = SessionId(kind="private", peer_id=1)
        s = Session(session_id=sid, kind="private")
        assert s.session_id == sid
        assert s.kind == "private"
        assert s.group_id is None
        assert s.summary == ""
        assert s.nickname == ""
        assert s.last_active_at is None


class TestUserFactDefaults:
    def test_minimal(self) -> None:
        f = UserFact(id="f1", content="likes cats")
        assert f.id == "f1"
        assert f.content == "likes cats"
        assert f.category == "general"
        assert f.source_user_id == 0
        assert isinstance(f.learned_at, datetime)
        assert f.confidence == 1.0
        assert f.active is True
        assert f.supersedes is None


class TestUserProfileDefaults:
    def test_minimal(self) -> None:
        p = UserProfile(user_id=42)
        assert p.user_id == 42
        assert p.preferred_name == ""
        assert p.aliases == []
        assert p.group_cards == {}
        assert p.stage == "stranger"
        assert p.first_met_at is None
        assert p.last_seen_at is None
        assert p.interaction_count == 0
        assert p.last_group_id is None
        assert p.seen_in_private is False
        assert p.seen_in_group is False
        assert p.impression == ""
        assert p.cognition_summary == ""
        assert p.cognition_updated_at is None
        assert p.cognition_interaction_at_update == 0
        assert p.facts == []

    def test_with_facts(self) -> None:
        f = UserFact(id="f1", content="test")
        p = UserProfile(user_id=1, facts=[f])
        assert len(p.facts) == 1
        assert p.facts[0].id == "f1"


class TestSocialEdgeDefaults:
    def test_minimal(self) -> None:
        e = SocialEdge(from_user_id=1, to_user_id=2, relation="friend_of")
        assert e.from_user_id == 1
        assert e.to_user_id == 2
        assert e.relation == "friend_of"
        assert e.label == ""
        assert e.evidence == ""
        assert e.group_id is None
        assert isinstance(e.learned_at, datetime)


class TestAuditEntryDefaults:
    def test_minimal(self) -> None:
        now = datetime.now(timezone.utc)
        a = AuditEntry(id=1, actor="admin", action="login", target="", detail={}, ip="127.0.0.1", success=True, created_at=now)
        assert a.id == 1
        assert a.actor == "admin"
        assert a.action == "login"
        assert a.detail == {}
        assert a.success is True


class TestAdminUserRowDefaults:
    def test_minimal(self) -> None:
        u = AdminUserRow(id=1, username="admin", password_hash="hash", role="admin")
        assert u.id == 1
        assert u.username == "admin"
        assert u.password_hash == "hash"
        assert u.role == "admin"
        assert u.must_change_password is True
        assert isinstance(u.created_at, datetime)
        assert u.last_login_at is None
