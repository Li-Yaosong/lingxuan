from __future__ import annotations

import asyncio
import random
import re
from collections.abc import AsyncIterator

import nonebot
from nonebot.adapters.onebot.v11 import Bot, MessageSegment

from lingxuan.config import (
    ENABLE_STREAM_CHUNK,
    GROUP_CHUNK_DELAY_MAX,
    GROUP_CHUNK_DELAY_MIN,
    GROUP_MSG_CHUNK_LIMIT,
    GROUP_MSG_CHUNK_MAX,
    GROUP_MSG_CHUNK_MIN,
)

logger = nonebot.logger

_SENTENCE_END = re.compile(r"([。！？；…\n]|[.!?])(?:\s|$)")


def split_chunks(
    text: str,
    *,
    max_len: int = GROUP_MSG_CHUNK_MAX,
    min_len: int = GROUP_MSG_CHUNK_MIN,
    limit: int = GROUP_MSG_CHUNK_LIMIT,
) -> list[str]:
    text = text.strip()
    if not text:
        return []

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

    pieces: list[str] = []
    for part in raw_parts:
        while len(part) > max_len:
            pieces.append(part[:max_len])
            part = part[max_len:].strip()
        if part:
            pieces.append(part)

    merged: list[str] = []
    for piece in pieces:
        if merged and len(piece) < min_len:
            combined = merged[-1] + piece
            if len(combined) <= max_len:
                merged[-1] = combined
                continue
        merged.append(piece)

    if len(merged) > limit:
        head = merged[: limit - 1]
        tail_text = "".join(merged[limit - 1 :])
        if len(tail_text) > max_len:
            tail_text = tail_text[:max_len]
        head.append(tail_text)
        return head
    return merged


def _take_emit_chunk(
    buffer: str,
    *,
    max_len: int,
    min_len: int,
) -> tuple[str | None, str]:
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


async def send_group_chunks(
    bot: Bot,
    group_id: int,
    user_id: int,
    text: str,
) -> str:
    chunks = split_chunks(text) if ENABLE_STREAM_CHUNK else [text]
    if not chunks:
        return ""

    for idx, chunk in enumerate(chunks):
        if idx == 0:
            msg = MessageSegment.at(user_id) + f" {chunk}"
        else:
            msg = chunk
        await bot.send_group_msg(group_id=group_id, message=msg)
        if idx == 0:
            logger.info("reply sent group={} user={} chunk=1", group_id, user_id)
        if idx + 1 < len(chunks):
            await asyncio.sleep(
                random.uniform(GROUP_CHUNK_DELAY_MIN, GROUP_CHUNK_DELAY_MAX)
            )
    if len(chunks) > 1:
        logger.info("reply sent group={} user={} chunks={}", group_id, user_id, len(chunks))
    return text


async def send_group_stream(
    bot: Bot,
    group_id: int,
    user_id: int,
    stream: AsyncIterator[str],
) -> str:
    if not ENABLE_STREAM_CHUNK:
        parts: list[str] = []
        async for token in stream:
            parts.append(token)
        return await send_group_chunks(bot, group_id, user_id, "".join(parts))

    buffer = ""
    full = ""
    chunks_sent = 0
    first = True

    async def _emit(chunk: str) -> None:
        nonlocal first, chunks_sent
        piece = chunk.strip()
        if not piece or chunks_sent >= GROUP_MSG_CHUNK_LIMIT:
            return
        if len(piece) > GROUP_MSG_CHUNK_MAX:
            piece = piece[:GROUP_MSG_CHUNK_MAX]
        if first:
            await bot.send_group_msg(
                group_id=group_id,
                message=MessageSegment.at(user_id) + f" {piece}",
            )
            logger.info("reply sent group={} user={} chunk=1", group_id, user_id)
            first = False
        else:
            await bot.send_group_msg(group_id=group_id, message=piece)
        chunks_sent += 1
        if chunks_sent < GROUP_MSG_CHUNK_LIMIT:
            await asyncio.sleep(
                random.uniform(GROUP_CHUNK_DELAY_MIN, GROUP_CHUNK_DELAY_MAX)
            )

    async for token in stream:
        buffer += token
        full += token
        while chunks_sent < GROUP_MSG_CHUNK_LIMIT:
            chunk, buffer = _take_emit_chunk(
                buffer,
                max_len=GROUP_MSG_CHUNK_MAX,
                min_len=GROUP_MSG_CHUNK_MIN,
            )
            if chunk is None:
                break
            await _emit(chunk)

    if buffer.strip() and chunks_sent < GROUP_MSG_CHUNK_LIMIT:
        await _emit(buffer)

    if chunks_sent > 1:
        logger.info("reply sent group={} user={} chunks={}", group_id, user_id, chunks_sent)
    return full.strip()
