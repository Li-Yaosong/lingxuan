"""Messaging domain types and MessageTransport protocol."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Protocol


@dataclass(frozen=True)
class SessionId:
    kind: Literal["private", "group"]
    peer_id: int  # private=user_id, group=group_id

    def as_str(self) -> str:
        return f"{self.kind}_{self.peer_id}"

    @classmethod
    def parse(cls, s: str) -> SessionId:
        if "_" not in s:
            raise ValueError(f"Invalid SessionId format: {s!r}")
        kind, _, peer = s.partition("_")
        if kind not in ("private", "group"):
            raise ValueError(f"Invalid SessionId kind: {kind!r}")
        return cls(kind=kind, peer_id=int(peer))


@dataclass(frozen=True)
class Actor:
    user_id: int
    nickname: str = ""
    is_admin: bool = False
    is_self: bool = False


@dataclass
class InboundMessage:
    session_id: SessionId
    actor: Actor
    text: str  # 已去 @ 段的纯文本
    raw_text: str = ""
    at_bot: bool = False
    reply_to_bot: bool = False
    at_user_ids: list[int] = field(default_factory=list)
    group_id: int | None = None
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    command: tuple[str, list[str]] | None = None


@dataclass(frozen=True)
class ReplyTarget:
    session_id: SessionId
    at_user_id: int | None = None


@dataclass
class OutboundChunk:
    text: str
    at_user_id: int | None = None  # 仅首段可能非空
    delay_before: float = 0.0


@dataclass
class OutboundMessage:
    target: ReplyTarget
    chunks: list[OutboundChunk]


@dataclass
class ReplyPlan:
    should_reply: bool
    reason: str = ""  # shortcircuit / judge_yes / cooldown / disabled ...
    stream: bool = True
    observation_text: str = ""
    primary_user_id: int | None = None


@dataclass
class ObservationEntry:  # 对齐现有 group_observer.ObservationEntry
    user_id: int
    nickname: str
    text: str
    at_bot: bool = False
    reply_to_bot: bool = False
    at_user_ids: list[int] = field(default_factory=list)
    is_bot: bool = False
    ts: float = field(default_factory=time.time)


@dataclass
class ObservationContext:
    session_id: SessionId
    group_id: int
    buffer: list[ObservationEntry]
    last_bot_reply_at: float = 0.0
    cooldown_until: float = 0.0
    self_id: int = 0


InboundHandler = Callable[[InboundMessage], Awaitable[None]]


class MessageTransport(Protocol):
    async def send(self, out: OutboundMessage) -> None: ...

    async def send_stream(
        self, target: ReplyTarget, chunks: AsyncIterator[OutboundChunk]
    ) -> str: ...

    def start(self, on_inbound: InboundHandler) -> None: ...

    async def resolve_self_id(self) -> int: ...

    def is_connected(self) -> bool: ...
