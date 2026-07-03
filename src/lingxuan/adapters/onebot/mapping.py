"""OneBot v11 event ↔ domain type mapping.

Pure functions: input NoneBot/OneBot events, output domain types.
This is one of the only places where nonebot adapter types may be imported.
No business orchestration, no message sending — only mapping.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)

from lingxuan.protocols.config import ConfigProvider
from lingxuan.protocols.messaging import (
    Actor,
    InboundMessage,
    OutboundChunk,
    OutboundMessage,
    ReplyTarget,
    SessionId,
)


# ---------------------------------------------------------------------------
# Outbound send instruction — lightweight value object for transport layer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SendInstruction:
    """A single message to be sent via OneBot API.

    Transport layer iterates these and calls ``bot.send_group_msg`` /
    ``bot.send_private_msg`` accordingly.
    """

    message: Message
    group_id: int | None = None  # None → private message
    user_id: int | None = None  # None → group message


# ---------------------------------------------------------------------------
# Inbound: PrivateMessageEvent → InboundMessage
# ---------------------------------------------------------------------------


def to_inbound_private(
    event: PrivateMessageEvent, *, config: ConfigProvider
) -> InboundMessage:
    """Map a private-message event to a domain InboundMessage."""
    user_id = int(event.user_id)
    nickname = event.sender.nickname or str(user_id)
    bot_admins = config.get_int_list("BOT_ADMINS")

    return InboundMessage(
        session_id=SessionId(kind="private", peer_id=user_id),
        actor=Actor(
            user_id=user_id,
            nickname=nickname,
            is_admin=user_id in bot_admins,
            is_self=False,
        ),
        text=event.get_plaintext().strip(),
        raw_text=str(event.message),
        at_bot=False,
        reply_to_bot=False,
        at_user_ids=[],
        group_id=None,
        received_at=datetime.fromtimestamp(event.time, tz=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Inbound: GroupMessageEvent → InboundMessage
# ---------------------------------------------------------------------------


def to_inbound_group(
    event: GroupMessageEvent, *, self_id: int, config: ConfigProvider
) -> InboundMessage:
    """Map a group-message event to a domain InboundMessage."""
    user_id = int(event.user_id)
    group_id = int(event.group_id)
    nickname = event.sender.card or event.sender.nickname or str(user_id)
    bot_admins = config.get_int_list("BOT_ADMINS")
    is_self = user_id == self_id

    at_bot = _is_at_bot(event, self_id)
    reply_to_bot = _is_reply_bot(event, self_id)
    at_user_ids = _parse_at_user_ids(event, self_id)
    text = _strip_at_text(event, self_id)

    # Optional: pre-parse admin command
    command: tuple[str, list[str]] | None = None
    if user_id in bot_admins and not is_self:
        command = _try_parse_command(text, config)

    return InboundMessage(
        session_id=SessionId(kind="group", peer_id=group_id),
        actor=Actor(
            user_id=user_id,
            nickname=nickname,
            is_admin=user_id in bot_admins,
            is_self=is_self,
        ),
        text=text,
        raw_text=str(event.message),
        at_bot=at_bot,
        reply_to_bot=reply_to_bot,
        at_user_ids=at_user_ids,
        group_id=group_id,
        received_at=datetime.fromtimestamp(event.time, tz=timezone.utc),
        command=command,
    )


# ---------------------------------------------------------------------------
# Outbound: OutboundMessage → list[SendInstruction]
# ---------------------------------------------------------------------------


def outbound_to_send_instructions(out: OutboundMessage) -> list[SendInstruction]:
    """Convert an OutboundMessage into OneBot send instructions.

    First chunk with ``at_user_id`` → ``MessageSegment.at(uid) + " " + text``;
    subsequent chunks → plain text.
    """
    target = out.target
    is_group = target.session_id.kind == "group"
    instructions: list[SendInstruction] = []

    for idx, chunk in enumerate(out.chunks):
        if not chunk.text.strip() and idx != 0:
            continue

        msg = Message()
        if idx == 0 and chunk.at_user_id is not None:
            msg += MessageSegment.at(chunk.at_user_id) + f" {chunk.text}"
        else:
            msg += MessageSegment.text(chunk.text)

        instructions.append(
            SendInstruction(
                message=msg,
                group_id=target.session_id.peer_id if is_group else None,
                user_id=None if is_group else target.session_id.peer_id,
            )
        )

    return instructions


# ---------------------------------------------------------------------------
# Internal helpers — reproduce MVP logic exactly
# ---------------------------------------------------------------------------


def _is_at_bot(event: GroupMessageEvent, self_id: int) -> bool:
    """Detect whether the event @-mentions the bot.

    Reproduces MVP ``handlers/group._is_at_bot``:
    1. event.to_me
    2. at segment with qq == self_id
    3. raw_message fallback containing qq=<self_id>
    """
    if getattr(event, "to_me", False):
        return True

    self_qq = str(self_id)
    for seg in event.message:
        if seg.type != "at":
            continue
        qq = seg.data.get("qq")
        if qq is None:
            continue
        qq_str = str(qq)
        # Skip @all / @所有人
        if qq_str in ("all", "所有人"):
            continue
        try:
            if int(qq) == self_id:
                return True
        except (TypeError, ValueError):
            pass
        if qq_str == self_qq:
            return True

    # Raw message fallback
    raw = getattr(event, "raw_message", "") or ""
    if f"qq={self_qq}" in raw or f"qq={self_id}" in raw:
        return True

    return False


def _is_reply_bot(event: GroupMessageEvent, self_id: int) -> bool:
    """Detect whether the event is a reply to the bot's message.

    Reproduces MVP ``handlers/group._is_reply_bot``.
    """
    if not event.reply:
        return False
    try:
        return int(event.reply.sender.user_id) == self_id
    except (ValueError, TypeError):
        return False


def _parse_at_user_ids(event: GroupMessageEvent, self_id: int) -> list[int]:
    """Extract all @-target user IDs, excluding the bot itself and @all.

    Reproduces MVP ``handlers/group._parse_at_user_ids``.
    """
    ids: list[int] = []
    for seg in event.message:
        if seg.type != "at":
            continue
        qq = seg.data.get("qq")
        if qq is None:
            continue
        qq_str = str(qq)
        if qq_str in ("all", "所有人"):
            continue
        try:
            uid = int(qq)
        except (TypeError, ValueError):
            continue
        if uid == self_id:
            continue
        ids.append(uid)
    return ids


def _strip_at_text(event: GroupMessageEvent, self_id: int) -> str:
    """Return plain text with all @-segments removed.

    The MVP used ``event.get_plaintext()`` which includes @-nicknames as text.
    We strip at segments from the message and rebuild plain text.
    """
    parts: list[str] = []
    for seg in event.message:
        if seg.type == "at":
            continue
        if seg.type == "text":
            parts.append(seg.data.get("text", ""))
        # Other segment types (face, image, etc.) are ignored for text
    text = "".join(parts).strip()
    # Collapse multiple spaces left by removed @-segments
    while "  " in text:
        text = text.replace("  ", " ")
    return text.strip()


def _try_parse_command(
    text: str, config: ConfigProvider
) -> tuple[str, list[str]] | None:
    """Attempt to parse an admin command from text.

    Mirrors MVP ``admin.parse_command`` using BOT_NAME from config.
    Returns None if text doesn't start with ``/{BOT_NAME} ``.
    """
    bot_name = config.get_str("BOT_NAME")
    prefix = f"/{bot_name} "
    if not text.startswith(prefix):
        return None
    rest = text[len(prefix) :].strip()
    if not rest:
        return "", []
    parts = rest.split()
    cmd = parts[0]
    args = parts[1:]
    aliases = {
        "重置记忆": "reset_memory",
        "状态": "status",
        "观察": "observe",
        "用户记忆": "user_memory",
        "重置用户记忆": "reset_user_memory",
    }
    cmd = aliases.get(cmd, cmd)
    return cmd, args
