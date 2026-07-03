"""OneBot v11 MessageTransport: matcher registration + send (multi-bubble, at, delay).

Migrates MVP ``handlers/private.py``, ``handlers/group.py`` matcher
registration and ``message_chunk.py`` sending into a single transport
implementation.  All NoneBot / OneBot API surface lives here.

Delay source: ``chunk.delay_before`` only — no random delay in transport.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

import nonebot
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)

from lingxuan.protocols.config import ConfigProvider
from lingxuan.protocols.logging import LogSink
from lingxuan.protocols.messaging import (
    InboundMessage,
    OutboundChunk,
    OutboundMessage,
    ReplyTarget,
)

logger = logging.getLogger(__name__)

# Type aliases for the injected mapping functions
PrivateMapper = Callable[[PrivateMessageEvent], Awaitable[InboundMessage]]
GroupMapper = Callable[[GroupMessageEvent], Awaitable[InboundMessage]]


class OneBotTransport:
    """Concrete ``MessageTransport`` backed by NoneBot + OneBot v11.

    Mapping functions (``to_inbound_private`` / ``to_inbound_group``) are
    injectable so the transport can be tested without the full mapping module.
    Default: import from ``lingxuan.adapters.onebot.mapping`` at call time.
    """

    def __init__(
        self,
        config: ConfigProvider,
        log: LogSink,
        *,
        to_private: PrivateMapper | None = None,
        to_group: GroupMapper | None = None,
    ) -> None:
        self._config = config
        self._log = log
        self._on_inbound: Callable[[InboundMessage], Awaitable[None]] | None = None
        self._to_private = to_private
        self._to_group = to_group

    # ------------------------------------------------------------------
    # Lazy mapping resolution (allows testing without mapping.py)
    # ------------------------------------------------------------------

    async def _map_private(self, event: PrivateMessageEvent) -> InboundMessage:
        if self._to_private is not None:
            return await self._to_private(event)
        from lingxuan.adapters.onebot.mapping import to_inbound_private

        return to_inbound_private(event, config=self._config)

    async def _map_group(self, event: GroupMessageEvent) -> InboundMessage:
        if self._to_group is not None:
            return await self._to_group(event)
        from lingxuan.adapters.onebot.mapping import to_inbound_group

        return to_inbound_group(event, self_id=int(event.self_id), config=self._config)

    # ------------------------------------------------------------------
    # Inbound: matcher registration
    # ------------------------------------------------------------------

    def start(self, on_inbound: Callable[[InboundMessage], Awaitable[None]]) -> None:
        """Register private & group matchers aligned with MVP priorities."""
        self._on_inbound = on_inbound

        private_matcher = nonebot.on_type(PrivateMessageEvent, priority=10, block=True)

        @private_matcher.handle()
        async def _handle_private(event: PrivateMessageEvent) -> None:
            if not self._config.get_bool("ENABLE_PRIVATE_CHAT"):
                return
            inbound = await self._map_private(event)
            if not inbound.text.strip():
                return
            await on_inbound(inbound)

        group_matcher = nonebot.on_type(GroupMessageEvent, priority=20, block=False)

        @group_matcher.handle()
        async def _handle_group(bot: Bot, event: GroupMessageEvent) -> None:
            if not self._config.get_bool("ENABLE_GROUP_CHAT"):
                return
            # Skip self messages (aligned with MVP)
            if event.user_id == event.self_id:
                return
            inbound = await self._map_group(event)
            # Guard: skip truly empty non-at messages
            if not inbound.text.strip() and not inbound.at_bot:
                return
            await on_inbound(inbound)

    # ------------------------------------------------------------------
    # resolve_self_id
    # ------------------------------------------------------------------

    async def resolve_self_id(self) -> int:
        """Return the self_id of the currently connected bot."""
        bot = self._current_bot()
        return int(bot.self_id)

    # ------------------------------------------------------------------
    # Send: single OutboundMessage
    # ------------------------------------------------------------------

    async def send(self, out: OutboundMessage) -> None:
        """Send an ``OutboundMessage`` with multi-bubble + at + delay."""
        target = out.target
        bot = self._current_bot()
        kind = target.session_id.kind
        peer_id = target.session_id.peer_id

        for idx, chunk in enumerate(out.chunks):
            if chunk.delay_before > 0:
                await asyncio.sleep(chunk.delay_before)

            if kind == "private":
                await bot.send_private_msg(user_id=peer_id, message=chunk.text)
            else:
                msg = self._build_group_msg(chunk, idx == 0)
                await bot.send_group_msg(group_id=peer_id, message=msg)

    # ------------------------------------------------------------------
    # Send: stream
    # ------------------------------------------------------------------

    async def send_stream(
        self,
        target: ReplyTarget,
        chunks: AsyncIterator[OutboundChunk],
    ) -> str:
        """Consume chunk stream, send each, and return concatenated text."""
        bot = self._current_bot()
        kind = target.session_id.kind
        peer_id = target.session_id.peer_id
        parts: list[str] = []
        idx = 0

        async for chunk in chunks:
            if chunk.delay_before > 0:
                await asyncio.sleep(chunk.delay_before)

            parts.append(chunk.text)

            if kind == "private":
                await bot.send_private_msg(user_id=peer_id, message=chunk.text)
            else:
                msg = self._build_group_msg(chunk, idx == 0)
                await bot.send_group_msg(group_id=peer_id, message=msg)

            idx += 1

        return "".join(parts)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_group_msg(chunk: OutboundChunk, is_first: bool) -> str | MessageSegment:
        """Build OneBot message for a group chunk.

        First chunk with ``at_user_id`` gets
        ``MessageSegment.at(uid) + " " + text``; all others are plain text.
        Aligned with MVP ``message_chunk.py`` first-chunk-at pattern.
        """
        if is_first and chunk.at_user_id is not None:
            return MessageSegment.at(chunk.at_user_id) + f" {chunk.text}"
        return chunk.text

    def _current_bot(self) -> Bot:
        """Get the current Bot instance from nonebot.

        Unified helper — eliminates the MVP dual-path inconsistency where
        some code got Bot from matcher injection and others from
        ``nonebot.get_bots()``.
        """
        bots = nonebot.get_bots()
        if not bots:
            raise RuntimeError("No bot instance available — nonebot.get_bots() is empty")
        return next(iter(bots.values()))
