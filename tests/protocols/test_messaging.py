"""Tests for lingxuan.protocols.messaging — SessionId round-trip and validation."""

import pytest

from lingxuan.protocols.messaging import SessionId


class TestSessionIdAsStr:
    def test_private(self) -> None:
        sid = SessionId(kind="private", peer_id=12345)
        assert sid.as_str() == "private_12345"

    def test_group(self) -> None:
        sid = SessionId(kind="group", peer_id=98765)
        assert sid.as_str() == "group_98765"


class TestSessionIdParse:
    def test_private(self) -> None:
        sid = SessionId.parse("private_12345")
        assert sid.kind == "private"
        assert sid.peer_id == 12345

    def test_group(self) -> None:
        sid = SessionId.parse("group_98765")
        assert sid.kind == "group"
        assert sid.peer_id == 98765

    def test_no_underscore_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid SessionId format"):
            SessionId.parse("invalid")

    def test_bad_kind_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid SessionId kind"):
            SessionId.parse("channel_100")

    def test_non_numeric_peer_raises(self) -> None:
        with pytest.raises(ValueError):
            SessionId.parse("private_abc")


class TestSessionIdRoundTrip:
    def test_private_roundtrip(self) -> None:
        original = SessionId(kind="private", peer_id=42)
        assert SessionId.parse(original.as_str()) == original

    def test_group_roundtrip(self) -> None:
        original = SessionId(kind="group", peer_id=9999)
        assert SessionId.parse(original.as_str()) == original
