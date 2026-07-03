"""Tests for adapters/onebot/mapping.py.

Uses duck-typed fake event objects to avoid constructing real NoneBot events
(which require complex internal state). The mapping functions only access a
well-defined set of attributes, so minimal fakes suffice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytest

from lingxuan.adapters.onebot.mapping import (
    SendInstruction,
    _is_at_bot,
    _is_reply_bot,
    _parse_at_user_ids,
    _strip_at_text,
    _try_parse_command,
    outbound_to_send_instructions,
    to_inbound_group,
    to_inbound_private,
)
from lingxuan.protocols.messaging import (
    OutboundChunk,
    OutboundMessage,
    ReplyTarget,
    SessionId,
)
from tests.fakes.config import FakeConfigProvider

# ---------------------------------------------------------------------------
# Minimal fake event objects (duck-typed)
# ---------------------------------------------------------------------------


@dataclass
class FakeSender:
    user_id: int = 10001
    nickname: str = "测试用户"
    card: str = ""


@dataclass
class FakeReplySender:
    user_id: int = 9999


@dataclass
class FakeReply:
    sender: FakeReplySender = field(default_factory=FakeReplySender)


@dataclass
class FakeMessageSegment:
    type: str = "text"
    data: dict[str, Any] = field(default_factory=dict)


class FakeMessage(list):
    """A list of FakeMessageSegment that also supports str() serialization."""

    def __str__(self) -> str:
        parts: list[str] = []
        for seg in self:
            if seg.type == "text":
                parts.append(seg.data.get("text", ""))
            elif seg.type == "at":
                qq = seg.data.get("qq", "")
                parts.append(f"[CQ:at,qq={qq}]")
            elif seg.type == "reply":
                parts.append(f"[CQ:reply,id={seg.data.get('id', '')}]")
            else:
                parts.append(f"[CQ:{seg.type}]")
        return "".join(parts)


@dataclass
class FakePrivateEvent:
    """Duck-typed PrivateMessageEvent."""

    user_id: int = 10001
    message: Any = None
    sender: FakeSender = field(default_factory=FakeSender)
    time: int = 1700000000
    to_me: bool = False

    def get_plaintext(self) -> str:
        parts: list[str] = []
        for seg in self.message:
            if seg.type == "text":
                parts.append(seg.data.get("text", ""))
        return "".join(parts)


@dataclass
class FakeGroupEvent:
    """Duck-typed GroupMessageEvent."""

    user_id: int = 10001
    group_id: int = 12345
    self_id: int = 9999
    message: Any = None
    raw_message: str = ""
    sender: FakeSender = field(default_factory=FakeSender)
    time: int = 1700000000
    to_me: bool = False
    reply: FakeReply | None = None

    def get_plaintext(self) -> str:
        parts: list[str] = []
        for seg in self.message:
            if seg.type == "text":
                parts.append(seg.data.get("text", ""))
        return "".join(parts)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BOT_SELF_ID = 9999
ADMIN_USER_ID = 10001
NORMAL_USER_ID = 10002


@pytest.fixture
def config() -> FakeConfigProvider:
    return FakeConfigProvider(overrides={"BOT_ADMINS": [ADMIN_USER_ID]})


@pytest.fixture
def config_no_admin() -> FakeConfigProvider:
    return FakeConfigProvider(overrides={"BOT_ADMINS": []})


# ---------------------------------------------------------------------------
# to_inbound_private
# ---------------------------------------------------------------------------


class TestToInboundPrivate:
    def test_basic_mapping(self, config: FakeConfigProvider) -> None:
        msg = FakeMessage([FakeMessageSegment("text", {"text": "你好"})])
        event = FakePrivateEvent(
            user_id=10001,
            message=msg,
            sender=FakeSender(user_id=10001, nickname="小明"),
            time=1700000000,
        )
        result = to_inbound_private(event, config=config)

        assert result.session_id == SessionId(kind="private", peer_id=10001)
        assert result.actor.user_id == 10001
        assert result.actor.nickname == "小明"
        assert result.actor.is_admin is True
        assert result.actor.is_self is False
        assert result.text == "你好"
        assert result.at_bot is False
        assert result.reply_to_bot is False
        assert result.at_user_ids == []
        assert result.group_id is None
        assert isinstance(result.received_at, datetime)

    def test_non_admin_user(self, config_no_admin: FakeConfigProvider) -> None:
        msg = FakeMessage([FakeMessageSegment("text", {"text": "hello"})])
        event = FakePrivateEvent(
            user_id=10002,
            message=msg,
            sender=FakeSender(user_id=10002, nickname="路人"),
        )
        result = to_inbound_private(event, config=config_no_admin)

        assert result.actor.is_admin is False
        assert result.session_id.peer_id == 10002

    def test_fallback_nickname(self, config_no_admin: FakeConfigProvider) -> None:
        msg = FakeMessage([FakeMessageSegment("text", {"text": "hi"})])
        event = FakePrivateEvent(
            user_id=10002,
            message=msg,
            sender=FakeSender(user_id=10002, nickname=""),
        )
        result = to_inbound_private(event, config=config_no_admin)

        # Falls back to str(user_id)
        assert result.actor.nickname == "10002"

    def test_raw_text_preserved(self, config_no_admin: FakeConfigProvider) -> None:
        msg = FakeMessage([
            FakeMessageSegment("text", {"text": "你好"}),
            FakeMessageSegment("at", {"qq": "10003"}),
        ])
        event = FakePrivateEvent(
            user_id=10002,
            message=msg,
            sender=FakeSender(user_id=10002, nickname="路人"),
        )
        result = to_inbound_private(event, config=config_no_admin)
        assert "你好" in result.raw_text
        assert "[CQ:at,qq=10003]" in result.raw_text


# ---------------------------------------------------------------------------
# to_inbound_group
# ---------------------------------------------------------------------------


class TestToInboundGroup:
    def test_basic_mapping(self, config: FakeConfigProvider) -> None:
        msg = FakeMessage([FakeMessageSegment("text", {"text": "大家好"})])
        event = FakeGroupEvent(
            user_id=NORMAL_USER_ID,
            group_id=12345,
            self_id=BOT_SELF_ID,
            message=msg,
            raw_message="大家好",
            sender=FakeSender(user_id=NORMAL_USER_ID, nickname="普通用户"),
        )
        result = to_inbound_group(event, self_id=BOT_SELF_ID, config=config)

        assert result.session_id == SessionId(kind="group", peer_id=12345)
        assert result.actor.user_id == NORMAL_USER_ID
        assert result.actor.nickname == "普通用户"
        assert result.actor.is_admin is False
        assert result.actor.is_self is False
        assert result.text == "大家好"
        assert result.at_bot is False
        assert result.reply_to_bot is False
        assert result.at_user_ids == []
        assert result.group_id == 12345

    def test_admin_actor(self, config: FakeConfigProvider) -> None:
        msg = FakeMessage([FakeMessageSegment("text", {"text": "/灵轩 status"})])
        event = FakeGroupEvent(
            user_id=ADMIN_USER_ID,
            group_id=12345,
            self_id=BOT_SELF_ID,
            message=msg,
            raw_message="/灵轩 status",
            sender=FakeSender(user_id=ADMIN_USER_ID, nickname="管理员"),
        )
        result = to_inbound_group(event, self_id=BOT_SELF_ID, config=config)

        assert result.actor.is_admin is True
        assert result.command == ("status", [])

    def test_self_message(self, config: FakeConfigProvider) -> None:
        msg = FakeMessage([FakeMessageSegment("text", {"text": "我是机器人"})])
        event = FakeGroupEvent(
            user_id=BOT_SELF_ID,
            group_id=12345,
            self_id=BOT_SELF_ID,
            message=msg,
            sender=FakeSender(user_id=BOT_SELF_ID, nickname="灵轩"),
        )
        result = to_inbound_group(event, self_id=BOT_SELF_ID, config=config)

        assert result.actor.is_self is True

    def test_card_nickname(self, config_no_admin: FakeConfigProvider) -> None:
        """Group messages prefer card (群名片) over nickname."""
        msg = FakeMessage([FakeMessageSegment("text", {"text": "hi"})])
        event = FakeGroupEvent(
            user_id=NORMAL_USER_ID,
            message=msg,
            sender=FakeSender(user_id=NORMAL_USER_ID, nickname="昵称", card="群名片"),
        )
        result = to_inbound_group(event, self_id=BOT_SELF_ID, config=config_no_admin)

        assert result.actor.nickname == "群名片"

    def test_at_bot_via_to_me(self, config_no_admin: FakeConfigProvider) -> None:
        msg = FakeMessage([FakeMessageSegment("text", {"text": "灵轩你好"})])
        event = FakeGroupEvent(
            to_me=True,
            message=msg,
        )
        result = to_inbound_group(event, self_id=BOT_SELF_ID, config=config_no_admin)
        assert result.at_bot is True

    def test_at_bot_via_at_segment(self, config_no_admin: FakeConfigProvider) -> None:
        msg = FakeMessage([
            FakeMessageSegment("at", {"qq": str(BOT_SELF_ID)}),
            FakeMessageSegment("text", {"text": " 你好"}),
        ])
        event = FakeGroupEvent(
            message=msg,
            raw_message=f"[CQ:at,qq={BOT_SELF_ID}] 你好",
        )
        result = to_inbound_group(event, self_id=BOT_SELF_ID, config=config_no_admin)
        assert result.at_bot is True

    def test_at_all_not_counted_as_at_bot(
        self, config_no_admin: FakeConfigProvider
    ) -> None:
        msg = FakeMessage([
            FakeMessageSegment("at", {"qq": "all"}),
            FakeMessageSegment("text", {"text": "大家好"}),
        ])
        event = FakeGroupEvent(
            message=msg,
            raw_message="[CQ:at,qq=all] 大家好",
        )
        result = to_inbound_group(event, self_id=BOT_SELF_ID, config=config_no_admin)
        assert result.at_bot is False

    def test_at_bot_via_raw_fallback(self, config_no_admin: FakeConfigProvider) -> None:
        msg = FakeMessage([FakeMessageSegment("text", {"text": "你好"})])
        event = FakeGroupEvent(
            message=msg,
            raw_message=f"[CQ:at,qq={BOT_SELF_ID}] 你好",
        )
        result = to_inbound_group(event, self_id=BOT_SELF_ID, config=config_no_admin)
        assert result.at_bot is True

    def test_reply_to_bot(self, config_no_admin: FakeConfigProvider) -> None:
        msg = FakeMessage([FakeMessageSegment("text", {"text": "回复"})])
        event = FakeGroupEvent(
            message=msg,
            reply=FakeReply(sender=FakeReplySender(user_id=BOT_SELF_ID)),
        )
        result = to_inbound_group(event, self_id=BOT_SELF_ID, config=config_no_admin)
        assert result.reply_to_bot is True

    def test_reply_to_other_not_bot(self, config_no_admin: FakeConfigProvider) -> None:
        msg = FakeMessage([FakeMessageSegment("text", {"text": "回复"})])
        event = FakeGroupEvent(
            message=msg,
            reply=FakeReply(sender=FakeReplySender(user_id=10003)),
        )
        result = to_inbound_group(event, self_id=BOT_SELF_ID, config=config_no_admin)
        assert result.reply_to_bot is False

    def test_no_reply(self, config_no_admin: FakeConfigProvider) -> None:
        msg = FakeMessage([FakeMessageSegment("text", {"text": "普通消息"})])
        event = FakeGroupEvent(message=msg, reply=None)
        result = to_inbound_group(event, self_id=BOT_SELF_ID, config=config_no_admin)
        assert result.reply_to_bot is False

    def test_at_user_ids(self, config_no_admin: FakeConfigProvider) -> None:
        msg = FakeMessage([
            FakeMessageSegment("at", {"qq": str(BOT_SELF_ID)}),
            FakeMessageSegment("text", {"text": " "}),
            FakeMessageSegment("at", {"qq": "10003"}),
            FakeMessageSegment("text", {"text": " 你好"}),
        ])
        event = FakeGroupEvent(message=msg)
        result = to_inbound_group(event, self_id=BOT_SELF_ID, config=config_no_admin)

        # bot's own ID excluded, @all excluded
        assert result.at_user_ids == [10003]

    def test_at_user_ids_excludes_all(self, config_no_admin: FakeConfigProvider) -> None:
        msg = FakeMessage([
            FakeMessageSegment("at", {"qq": "all"}),
            FakeMessageSegment("text", {"text": " "}),
            FakeMessageSegment("at", {"qq": "10003"}),
        ])
        event = FakeGroupEvent(message=msg)
        result = to_inbound_group(event, self_id=BOT_SELF_ID, config=config_no_admin)

        assert 10003 in result.at_user_ids
        assert "all" not in [str(uid) for uid in result.at_user_ids]

    def test_text_strips_at_segments(self, config_no_admin: FakeConfigProvider) -> None:
        msg = FakeMessage([
            FakeMessageSegment("at", {"qq": str(BOT_SELF_ID)}),
            FakeMessageSegment("text", {"text": " 你好"}),
            FakeMessageSegment("at", {"qq": "10003"}),
            FakeMessageSegment("text", {"text": " 朋友"}),
        ])
        event = FakeGroupEvent(message=msg)
        result = to_inbound_group(event, self_id=BOT_SELF_ID, config=config_no_admin)

        # @ segments removed, text concatenated and stripped
        assert "你好" in result.text
        assert "朋友" in result.text
        # No CQ codes in text
        assert "[CQ:" not in result.text

    def test_command_parse_admin(self, config: FakeConfigProvider) -> None:
        msg = FakeMessage([FakeMessageSegment("text", {"text": "/灵轩 status"})])
        event = FakeGroupEvent(
            user_id=ADMIN_USER_ID,
            message=msg,
            raw_message="/灵轩 status",
            sender=FakeSender(user_id=ADMIN_USER_ID),
        )
        result = to_inbound_group(event, self_id=BOT_SELF_ID, config=config)
        assert result.command == ("status", [])

    def test_command_parse_with_args(self, config: FakeConfigProvider) -> None:
        msg = FakeMessage([FakeMessageSegment("text", {"text": "/灵轩 reset_memory all"})])
        event = FakeGroupEvent(
            user_id=ADMIN_USER_ID,
            message=msg,
            raw_message="/灵轩 reset_memory all",
            sender=FakeSender(user_id=ADMIN_USER_ID),
        )
        result = to_inbound_group(event, self_id=BOT_SELF_ID, config=config)
        assert result.command == ("reset_memory", ["all"])

    def test_command_alias(self, config: FakeConfigProvider) -> None:
        msg = FakeMessage([FakeMessageSegment("text", {"text": "/灵轩 状态"})])
        event = FakeGroupEvent(
            user_id=ADMIN_USER_ID,
            message=msg,
            raw_message="/灵轩 状态",
            sender=FakeSender(user_id=ADMIN_USER_ID),
        )
        result = to_inbound_group(event, self_id=BOT_SELF_ID, config=config)
        assert result.command == ("status", [])

    def test_prefix_only_no_match(self, config: FakeConfigProvider) -> None:
        """'/灵轩' (no trailing space after strip) does not match prefix '/灵轩 '.

        This aligns with MVP: get_plaintext().strip() removes trailing space,
        so '/灵轩 ' → '/灵轩' which doesn't startswith '/灵轩 '.
        """
        msg = FakeMessage([FakeMessageSegment("text", {"text": "/灵轩 "})])
        event = FakeGroupEvent(
            user_id=ADMIN_USER_ID,
            message=msg,
            raw_message="/灵轩 ",
            sender=FakeSender(user_id=ADMIN_USER_ID),
        )
        result = to_inbound_group(event, self_id=BOT_SELF_ID, config=config)
        # _strip_at_text strips trailing space → "/灵轩" doesn't match "/灵轩 "
        assert result.command is None

    def test_non_admin_no_command(self, config: FakeConfigProvider) -> None:
        msg = FakeMessage([FakeMessageSegment("text", {"text": "/灵轩 status"})])
        event = FakeGroupEvent(
            user_id=NORMAL_USER_ID,
            message=msg,
            raw_message="/灵轩 status",
            sender=FakeSender(user_id=NORMAL_USER_ID),
        )
        result = to_inbound_group(event, self_id=BOT_SELF_ID, config=config)
        assert result.command is None

    def test_no_command_prefix(self, config: FakeConfigProvider) -> None:
        msg = FakeMessage([FakeMessageSegment("text", {"text": "普通消息"})])
        event = FakeGroupEvent(
            user_id=ADMIN_USER_ID,
            message=msg,
            raw_message="普通消息",
            sender=FakeSender(user_id=ADMIN_USER_ID),
        )
        result = to_inbound_group(event, self_id=BOT_SELF_ID, config=config)
        assert result.command is None

    def test_command_after_at_bot(self, config: FakeConfigProvider) -> None:
        """Command after @bot: text is stripped of @, command parsed from clean text."""
        msg = FakeMessage([
            FakeMessageSegment("at", {"qq": str(BOT_SELF_ID)}),
            FakeMessageSegment("text", {"text": " /灵轩 status"}),
        ])
        event = FakeGroupEvent(
            user_id=ADMIN_USER_ID,
            message=msg,
            raw_message=f"[CQ:at,qq={BOT_SELF_ID}] /灵轩 status",
            sender=FakeSender(user_id=ADMIN_USER_ID),
        )
        result = to_inbound_group(event, self_id=BOT_SELF_ID, config=config)
        assert result.at_bot is True
        # After stripping @: "/灵轩 status" → matches prefix
        assert result.command == ("status", [])


# ---------------------------------------------------------------------------
# _is_at_bot — unit tests for the core detection logic
# ---------------------------------------------------------------------------


class TestIsAtBot:
    def test_to_me_flag(self) -> None:
        event = FakeGroupEvent(to_me=True)
        assert _is_at_bot(event, BOT_SELF_ID) is True

    def test_at_segment_matching_self_id(self) -> None:
        msg = FakeMessage([FakeMessageSegment("at", {"qq": str(BOT_SELF_ID)})])
        event = FakeGroupEvent(message=msg)
        assert _is_at_bot(event, BOT_SELF_ID) is True

    def test_at_segment_not_matching(self) -> None:
        msg = FakeMessage([FakeMessageSegment("at", {"qq": "10003"})])
        event = FakeGroupEvent(message=msg)
        assert _is_at_bot(event, BOT_SELF_ID) is False

    def test_at_all_not_matched(self) -> None:
        msg = FakeMessage([FakeMessageSegment("at", {"qq": "all"})])
        event = FakeGroupEvent(message=msg)
        assert _is_at_bot(event, BOT_SELF_ID) is False

    def test_at_所有人_not_matched(self) -> None:
        msg = FakeMessage([FakeMessageSegment("at", {"qq": "所有人"})])
        event = FakeGroupEvent(message=msg)
        assert _is_at_bot(event, BOT_SELF_ID) is False

    def test_raw_message_fallback(self) -> None:
        msg = FakeMessage([FakeMessageSegment("text", {"text": "hello"})])
        event = FakeGroupEvent(
            message=msg,
            raw_message=f"[CQ:at,qq={BOT_SELF_ID}] hello",
        )
        assert _is_at_bot(event, BOT_SELF_ID) is True

    def test_raw_message_no_match(self) -> None:
        msg = FakeMessage([FakeMessageSegment("text", {"text": "hello"})])
        event = FakeGroupEvent(
            message=msg,
            raw_message="hello",
        )
        assert _is_at_bot(event, BOT_SELF_ID) is False

    def test_at_segment_int_match(self) -> None:
        """int(qq) == self_id should match even if str representation differs."""
        msg = FakeMessage([FakeMessageSegment("at", {"qq": 9999})])
        event = FakeGroupEvent(message=msg)
        assert _is_at_bot(event, BOT_SELF_ID) is True


# ---------------------------------------------------------------------------
# _is_reply_bot
# ---------------------------------------------------------------------------


class TestIsReplyBot:
    def test_reply_from_bot(self) -> None:
        event = FakeGroupEvent(
            reply=FakeReply(sender=FakeReplySender(user_id=BOT_SELF_ID)),
        )
        assert _is_reply_bot(event, BOT_SELF_ID) is True

    def test_reply_from_other(self) -> None:
        event = FakeGroupEvent(
            reply=FakeReply(sender=FakeReplySender(user_id=10003)),
        )
        assert _is_reply_bot(event, BOT_SELF_ID) is False

    def test_no_reply(self) -> None:
        event = FakeGroupEvent(reply=None)
        assert _is_reply_bot(event, BOT_SELF_ID) is False


# ---------------------------------------------------------------------------
# _parse_at_user_ids
# ---------------------------------------------------------------------------


class TestParseAtUserIds:
    def test_multiple_at(self) -> None:
        msg = FakeMessage([
            FakeMessageSegment("at", {"qq": str(BOT_SELF_ID)}),
            FakeMessageSegment("at", {"qq": "10003"}),
            FakeMessageSegment("at", {"qq": "10004"}),
        ])
        event = FakeGroupEvent(message=msg)
        ids = _parse_at_user_ids(event, BOT_SELF_ID)
        assert ids == [10003, 10004]

    def test_excludes_self(self) -> None:
        msg = FakeMessage([
            FakeMessageSegment("at", {"qq": str(BOT_SELF_ID)}),
        ])
        event = FakeGroupEvent(message=msg)
        ids = _parse_at_user_ids(event, BOT_SELF_ID)
        assert ids == []

    def test_excludes_all(self) -> None:
        msg = FakeMessage([
            FakeMessageSegment("at", {"qq": "all"}),
            FakeMessageSegment("at", {"qq": "所有人"}),
        ])
        event = FakeGroupEvent(message=msg)
        ids = _parse_at_user_ids(event, BOT_SELF_ID)
        assert ids == []

    def test_no_at_segments(self) -> None:
        msg = FakeMessage([FakeMessageSegment("text", {"text": "hello"})])
        event = FakeGroupEvent(message=msg)
        ids = _parse_at_user_ids(event, BOT_SELF_ID)
        assert ids == []


# ---------------------------------------------------------------------------
# _strip_at_text
# ---------------------------------------------------------------------------


class TestStripAtText:
    def test_plain_text_unchanged(self) -> None:
        msg = FakeMessage([FakeMessageSegment("text", {"text": "你好世界"})])
        event = FakeGroupEvent(message=msg)
        assert _strip_at_text(event, BOT_SELF_ID) == "你好世界"

    def test_removes_at_segments(self) -> None:
        msg = FakeMessage([
            FakeMessageSegment("at", {"qq": str(BOT_SELF_ID)}),
            FakeMessageSegment("text", {"text": " 你好"}),
        ])
        event = FakeGroupEvent(message=msg)
        result = _strip_at_text(event, BOT_SELF_ID)
        assert result == "你好"

    def test_multiple_text_segments(self) -> None:
        msg = FakeMessage([
            FakeMessageSegment("text", {"text": "你好"}),
            FakeMessageSegment("at", {"qq": "10003"}),
            FakeMessageSegment("text", {"text": " 世界"}),
        ])
        event = FakeGroupEvent(message=msg)
        result = _strip_at_text(event, BOT_SELF_ID)
        assert "你好" in result
        assert "世界" in result


# ---------------------------------------------------------------------------
# _try_parse_command
# ---------------------------------------------------------------------------


class TestTryParseCommand:
    def test_valid_command(self, config: FakeConfigProvider) -> None:
        result = _try_parse_command("/灵轩 status", config)
        assert result == ("status", [])

    def test_command_with_args(self, config: FakeConfigProvider) -> None:
        result = _try_parse_command("/灵轩 reset_memory all", config)
        assert result == ("reset_memory", ["all"])

    def test_alias(self, config: FakeConfigProvider) -> None:
        result = _try_parse_command("/灵轩 状态", config)
        assert result == ("status", [])

    def test_empty_command(self, config: FakeConfigProvider) -> None:
        result = _try_parse_command("/灵轩 ", config)
        assert result == ("", [])

    def test_no_prefix(self, config: FakeConfigProvider) -> None:
        result = _try_parse_command("普通消息", config)
        assert result is None

    def test_partial_prefix(self, config: FakeConfigProvider) -> None:
        result = _try_parse_command("/灵轩", config)
        assert result is None


# ---------------------------------------------------------------------------
# outbound_to_send_instructions
# ---------------------------------------------------------------------------


class TestOutboundToSendInstructions:
    def test_group_message_with_at(self) -> None:
        target = ReplyTarget(
            session_id=SessionId(kind="group", peer_id=12345),
            at_user_id=10001,
        )
        out = OutboundMessage(
            target=target,
            chunks=[
                OutboundChunk(text="你好", at_user_id=10001),
                OutboundChunk(text="世界"),
            ],
        )
        instructions = outbound_to_send_instructions(out)

        assert len(instructions) == 2

        # First chunk: at + text
        first = instructions[0]
        assert first.group_id == 12345
        assert first.user_id is None
        first_str = str(first.message)
        assert "[CQ:at,qq=10001]" in first_str
        assert "你好" in first_str

        # Second chunk: plain text
        second = instructions[1]
        assert second.group_id == 12345
        second_str = str(second.message)
        assert "世界" in second_str
        assert "[CQ:at" not in second_str

    def test_group_message_without_at(self) -> None:
        target = ReplyTarget(
            session_id=SessionId(kind="group", peer_id=12345),
        )
        out = OutboundMessage(
            target=target,
            chunks=[OutboundChunk(text="观察回复")],
        )
        instructions = outbound_to_send_instructions(out)

        assert len(instructions) == 1
        assert instructions[0].group_id == 12345
        msg_str = str(instructions[0].message)
        assert "[CQ:at" not in msg_str
        assert "观察回复" in msg_str

    def test_private_message(self) -> None:
        target = ReplyTarget(
            session_id=SessionId(kind="private", peer_id=10001),
        )
        out = OutboundMessage(
            target=target,
            chunks=[OutboundChunk(text="私聊回复")],
        )
        instructions = outbound_to_send_instructions(out)

        assert len(instructions) == 1
        assert instructions[0].group_id is None
        assert instructions[0].user_id == 10001
        assert "私聊回复" in str(instructions[0].message)

    def test_empty_chunk_skipped(self) -> None:
        target = ReplyTarget(
            session_id=SessionId(kind="group", peer_id=12345),
            at_user_id=10001,
        )
        out = OutboundMessage(
            target=target,
            chunks=[
                OutboundChunk(text="你好", at_user_id=10001),
                OutboundChunk(text="   "),  # whitespace-only, non-first
                OutboundChunk(text="世界"),
            ],
        )
        instructions = outbound_to_send_instructions(out)
        # Whitespace-only non-first chunk is skipped
        assert len(instructions) == 2

    def test_multiple_chunks(self) -> None:
        target = ReplyTarget(
            session_id=SessionId(kind="group", peer_id=12345),
            at_user_id=10001,
        )
        out = OutboundMessage(
            target=target,
            chunks=[
                OutboundChunk(text="第一段", at_user_id=10001),
                OutboundChunk(text="第二段"),
                OutboundChunk(text="第三段"),
            ],
        )
        instructions = outbound_to_send_instructions(out)
        assert len(instructions) == 3

        # Only first has at
        assert "[CQ:at" in str(instructions[0].message)
        assert "[CQ:at" not in str(instructions[1].message)
        assert "[CQ:at" not in str(instructions[2].message)
