"""ReplyPlanner: chunk-splitting and pacing pure strategy.

Migrates MVP ``message_chunk.py`` splitting/rhythm algorithms into Core
pure logic, producing ``OutboundChunk`` sequences with inter-chunk delays.
No messaging — sending lives in the onebot transport.
"""

from __future__ import annotations

import random
import re
from collections.abc import AsyncIterator

from lingxuan.protocols.config import ConfigProvider
from lingxuan.protocols.messaging import OutboundChunk

# Same regex as MVP — matches sentence-ending punctuation or newline
_SENTENCE_END = re.compile(r"([。！？；…\n]|[.!?])(?:\s|$)")


def split_chunks(
    text: str,
    *,
    max_len: int,
    min_len: int,
    limit: int,
) -> list[str]:
    """Split *text* into chunks respecting sentence boundaries and length constraints.

    Algorithm (byte-for-byte aligned with MVP ``message_chunk.split_chunks``):

    1. Split on sentence-end punctuation / newline.
    2. Hard-cut any piece exceeding *max_len*.
    3. Merge short pieces (< *min_len*) into previous chunk if it fits.
    4. When chunk count exceeds *limit*, keep first *limit-1* and
       concatenate+truncate the tail.
    """
    text = text.strip()
    if not text:
        return []

    # Step 1 — sentence-boundary split
    raw_parts: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        end = match.end()
        part = text[start:end].strip()
        if part:
            raw_parts.append(part)
        start = end
    tail = text[start:].strip()
    if tail:
        raw_parts.append(tail)
    if not raw_parts:
        raw_parts = [text]

    # Step 2 — hard-cut overlength pieces
    pieces: list[str] = []
    for part in raw_parts:
        while len(part) > max_len:
            pieces.append(part[:max_len])
            part = part[max_len:].strip()
        if part:
            pieces.append(part)

    # Step 3 — merge short pieces into previous
    merged: list[str] = []
    for piece in pieces:
        if merged and len(piece) < min_len:
            combined = merged[-1] + piece
            if len(combined) <= max_len:
                merged[-1] = combined
                continue
        merged.append(piece)

    # Step 4 — enforce limit
    if len(merged) > limit:
        head = merged[: limit - 1]
        tail_text = "".join(merged[limit - 1 :])
        if len(tail_text) > max_len:
            tail_text = tail_text[:max_len]
        head.append(tail_text)
        return head
    return merged


def take_emit_chunk(
    buffer: str,
    *,
    max_len: int,
    min_len: int,
) -> tuple[str | None, str]:
    """Incremental streaming chunk extractor.

    Returns ``(chunk_text_or_None, remaining_buffer)``.
    Prefers cutting at a sentence boundary where the prefix >= *min_len*;
    falls back to a hard cut at *max_len*; otherwise returns ``(None, buffer)``.
    """
    if not buffer.strip():
        return None, buffer

    match = _SENTENCE_END.search(buffer)
    if match and match.end() >= min_len:
        chunk = buffer[: match.end()].strip()
        rest = buffer[match.end() :]
        if chunk:
            return chunk, rest

    if len(buffer) >= max_len:
        chunk = buffer[:max_len].strip()
        rest = buffer[max_len:]
        if chunk:
            return chunk, rest

    return None, buffer


class ReplyPlanner:
    """Produces ``OutboundChunk`` sequences with pacing delays.

    Reads chunk-related config from *config*; accepts an optional *rng*
    (``random.Random``) for deterministic testing.
    """

    def __init__(
        self,
        config: ConfigProvider,
        rng: random.Random | None = None,
    ) -> None:
        self._config = config
        self._rng = rng or random

    # -- config accessors (read each call for hot-reload) --------------------

    @property
    def _max_len(self) -> int:
        return self._config.get_int("GROUP_MSG_CHUNK_MAX")

    @property
    def _min_len(self) -> int:
        return self._config.get_int("GROUP_MSG_CHUNK_MIN")

    @property
    def _limit(self) -> int:
        return self._config.get_int("GROUP_MSG_CHUNK_LIMIT")

    @property
    def _delay_min(self) -> float:
        return self._config.get_float("GROUP_CHUNK_DELAY_MIN")

    @property
    def _delay_max(self) -> float:
        return self._config.get_float("GROUP_CHUNK_DELAY_MAX")

    @property
    def _stream_enabled(self) -> bool:
        return self._config.get_bool("ENABLE_STREAM_CHUNK")

    # -- public API ---------------------------------------------------------

    def plan_static(
        self,
        text: str,
        *,
        at_user_id: int | None = None,
    ) -> list[OutboundChunk]:
        """Split *text* into an ``OutboundChunk`` list with inter-chunk delays.

        First chunk has ``delay_before=0`` and optionally carries *at_user_id*.
        Subsequent chunks get a random delay in ``[DELAY_MIN, DELAY_MAX]``.
        """
        chunks = split_chunks(
            text,
            max_len=self._max_len,
            min_len=self._min_len,
            limit=self._limit,
        )
        if not chunks:
            return []

        result: list[OutboundChunk] = []
        for i, chunk_text in enumerate(chunks):
            delay = 0.0 if i == 0 else self._rng.uniform(self._delay_min, self._delay_max)
            at = at_user_id if i == 0 else None
            result.append(OutboundChunk(text=chunk_text, at_user_id=at, delay_before=delay))
        return result

    async def plan_stream(
        self,
        token_iter: AsyncIterator[str],
        *,
        at_user_id: int | None = None,
    ) -> AsyncIterator[OutboundChunk]:
        """Consume *token_iter* and yield ``OutboundChunk`` s incrementally.

        When ``ENABLE_STREAM_CHUNK`` is ``False``, collects all tokens first
        then delegates to :meth:`plan_static`.
        """
        if not self._stream_enabled:
            parts: list[str] = []
            async for token in token_iter:
                parts.append(token)
            full_text = "".join(parts)
            for chunk in self.plan_static(full_text, at_user_id=at_user_id):
                yield chunk
            return

        buffer = ""
        chunks_sent = 0
        limit = self._limit
        max_len = self._max_len
        min_len = self._min_len

        async for token in token_iter:
            buffer += token
            while chunks_sent < limit:
                chunk_text, buffer = take_emit_chunk(
                    buffer,
                    max_len=max_len,
                    min_len=min_len,
                )
                if chunk_text is None:
                    break
                # Enforce per-chunk max length
                if len(chunk_text) > max_len:
                    chunk_text = chunk_text[:max_len]
                delay = 0.0 if chunks_sent == 0 else self._rng.uniform(self._delay_min, self._delay_max)
                at = at_user_id if chunks_sent == 0 else None
                yield OutboundChunk(text=chunk_text, at_user_id=at, delay_before=delay)
                chunks_sent += 1

        # Flush remaining buffer
        if buffer.strip() and chunks_sent < limit:
            piece = buffer.strip()
            if len(piece) > max_len:
                piece = piece[:max_len]
            delay = 0.0 if chunks_sent == 0 else self._rng.uniform(self._delay_min, self._delay_max)
            at = at_user_id if chunks_sent == 0 else None
            yield OutboundChunk(text=piece, at_user_id=at, delay_before=delay)
