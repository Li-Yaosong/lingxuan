"""Fake message transport: records outbound, allows injecting inbound."""

from __future__ import annotations

from collections.abc import AsyncIterator

from lingxuan.protocols.messaging import (
    InboundHandler,
    OutboundChunk,
    OutboundMessage,
    ReplyTarget,
)


class FakeTransport:
    """Implements MessageTransport protocol with recording and injection."""

    def __init__(self, self_id: int = 0) -> None:
        self._self_id = self_id
        self._handler: InboundHandler | None = None
        self.sent_messages: list[OutboundMessage] = []
        self.sent_stream_chunks: list[list[OutboundChunk]] = []
        self._stream_target: ReplyTarget | None = None

    async def send(self, out: OutboundMessage) -> None:
        self.sent_messages.append(out)

    async def send_stream(
        self, target: ReplyTarget, chunks: AsyncIterator[OutboundChunk]
    ) -> str:
        collected: list[OutboundChunk] = []
        full_text_parts: list[str] = []
        async for chunk in chunks:
            collected.append(chunk)
            full_text_parts.append(chunk.text)
        self.sent_stream_chunks.append(collected)
        return "".join(full_text_parts)

    def start(self, on_inbound: InboundHandler) -> None:
        self._handler = on_inbound

    async def resolve_self_id(self) -> int:
        return self._self_id

    async def inject(self, msg: InboundMessage) -> None:
        """Test helper: simulate an inbound message arriving."""
        if self._handler is not None:
            await self._handler(msg)
